"""Host file handling for statements: kind, lock, unlock, PDF pages, and a text preview.

The host never turns a file into rows. It sniffs the kind from magic bytes, opens locks, counts and
renders PDF pages for the vision path, and reads a few header lines so the period and the account
can be found. The sandbox and the vision model do the reading.
"""

from __future__ import annotations

import asyncio
import io
import json
import threading
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

MAX_ZIP_TOTAL = 200 * 1024 * 1024
MAX_ZIP_MEMBERS = 50
MAX_DOC_PAGES = 150
PREVIEW_ROWS = 40

# PDFium is not thread-safe, and PDF work runs on worker threads (`in_pdf_worker`), so every
# call that touches it takes this one lock.
_PDF_LOCK = threading.RLock()


class DocError(Exception):
    """A readable problem: wrong password, corrupt file, unsupported kind."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def sniff(data: bytes) -> str:
    """The kind from magic bytes and content, never from the file name."""
    if not data:
        raise DocError("empty", "zero-byte file")
    head = data[:8]
    if data[:5] == b"%PDF-":
        return "pdf"
    if (
        head[:4] == b"\x89PNG"
        or head[:3] == b"\xff\xd8\xff"
        or data[:4] in (b"II*\x00", b"MM\x00*")
        or data[:2] == b"BM"
        or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")
    ):
        return "image"
    if head[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
                if "xl/workbook.xml" in names:
                    return "xlsx"
                if "xl/workbook.bin" in names:
                    return "xlsb"
                if "word/document.xml" in names:
                    return "docx"
                if "mimetype" in names and b"opendocument.spreadsheet" in archive.read("mimetype"):
                    return "ods"
        except zipfile.BadZipFile as exc:
            raise DocError("corrupt", "bad zip") from exc
        return "zip"
    if head[:4] == b"\xd0\xcf\x11\xe0":
        import olefile  # pyrefly: ignore[untyped-import]  # olefile ships no type information

        try:
            ole = olefile.OleFileIO(io.BytesIO(data))
        except Exception as exc:  # noqa: BLE001 - olefile raises many types
            raise DocError("corrupt", "bad OLE container") from exc
        entries = {"/".join(part) for part in ole.listdir()}
        if "EncryptedPackage" in entries:
            return "encrypted_ooxml"
        if "Workbook" in entries or "Book" in entries:
            return "xls"
        if "WordDocument" in entries:
            return "doc"
        return "ole_other"
    text, _encoding = decode_text(data)
    stripped = text.lstrip()[:2000]
    lowered = stripped.lower()
    if stripped.startswith("{\\rtf"):
        return "rtf"
    if "<html" in lowered or "<table" in lowered or "<!doctype html" in lowered:
        return "html"
    if "OFXHEADER" in stripped or "<OFX>" in stripped.upper()[:3000]:
        return "ofx"
    if stripped.startswith(":20:") or "\n:20:" in text[:200]:
        return "mt940"
    if stripped.startswith("<?xml") or stripped.startswith("<Document"):
        return "camt" if "BkToCstmrStmt" in text[:5000] else "xml"
    if stripped[:1] in "{[=":
        try:
            json.loads(text)
            return "json"
        except ValueError:
            pass
    return "text"


def image_media_type(data: bytes) -> str | None:
    """Only still images a multimodal model accepts. The header comes from the bytes."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    return None


def decode_text(data: bytes) -> tuple[str, str]:
    if data[:3] == b"\xef\xbb\xbf":
        return data[3:].decode("utf-8"), "utf-8-sig"
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16"), "utf-16"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace"), "cp1252"


def unwrap_zip(data: bytes) -> bytes:
    """The one file inside a ZIP; a statement archive of several files is refused."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        if len(infos) > MAX_ZIP_MEMBERS or sum(i.file_size for i in infos) > MAX_ZIP_TOTAL:
            raise DocError("zip_too_big", "member count or uncompressed size over limit")
        if len(infos) != 1:
            raise DocError("zip_many", "upload one statement at a time, not an archive of files")
        parts = Path(infos[0].filename).parts
        if infos[0].filename.startswith(("/", "\\")) or ".." in parts:
            raise DocError("zip_path", infos[0].filename)
        return archive.read(infos[0])


def is_locked(data: bytes) -> bool:
    """Whether the file needs a password before it can be read."""
    kind = sniff(data)
    if kind == "encrypted_ooxml":
        return True
    if kind != "pdf":
        return False
    import pypdfium2 as pdfium

    with _PDF_LOCK:
        try:
            pdfium.PdfDocument(data).close()
        except pdfium.PdfiumError as exc:
            return "password" in str(exc).lower()
    return False


def unlock(data: bytes, password: str) -> bytes:
    """The file without its lock. The password is used here and kept nowhere."""
    kind = sniff(data)
    if kind == "encrypted_ooxml":
        import msoffcrypto

        office = msoffcrypto.OfficeFile(io.BytesIO(data))
        out = io.BytesIO()
        try:
            office.load_key(password=password)
            office.decrypt(out)
        except Exception as exc:  # noqa: BLE001 - a wrong key is a readable problem
            raise DocError("wrong_password", type(exc).__name__) from exc
        return out.getvalue()
    if kind != "pdf":
        return data
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_c

    with _PDF_LOCK:
        try:
            pdf = pdfium.PdfDocument(data, password=password)
        except pdfium.PdfiumError as exc:
            raise DocError("wrong_password", "PDF") from exc
        buffer = io.BytesIO()
        # Without this flag PDFium keeps the encryption, and the copy still needs the password.
        pdf.save(buffer, flags=pdfium_c.FPDF_REMOVE_SECURITY)
        return buffer.getvalue()


def _open_pdf(data: bytes) -> Any:
    import pypdfium2 as pdfium

    try:
        return pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        raise DocError("corrupt", str(exc)[:80]) from exc


def pdf_pages(data: bytes) -> tuple[int, int]:
    """(page count, pages with a text layer) of an unlocked PDF."""
    with _PDF_LOCK:
        pdf = _open_pdf(data)
        counts = [pdf[i].get_textpage().count_chars() for i in range(len(pdf))]
    return len(counts), sum(1 for chars in counts if chars > 20)


def render_pages(data: bytes) -> list[bytes]:
    """Every page of an unlocked PDF as PNG, for the vision model."""
    import struct
    import zlib

    def chunk(tag: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(tag + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", crc)

    pages: list[bytes] = []
    with _PDF_LOCK:
        pdf = _open_pdf(data)
        for index in range(len(pdf)):
            # `rev_byteorder` makes PDFium emit RGB; each PNG row is a filter byte plus the pixels.
            bitmap = pdf[index].render(scale=2, rev_byteorder=True)
            raw, stride, width = bytes(bitmap.buffer), bitmap.stride, bitmap.width
            rows = b"".join(
                b"\x00" + raw[y * stride : y * stride + width * 3] for y in range(bitmap.height)
            )
            header = struct.pack(">IIBBBBB", width, bitmap.height, 8, 2, 0, 0, 0)
            pages.append(
                b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", header)
                + chunk(b"IDAT", zlib.compress(rows, 6))
                + chunk(b"IEND", b"")
            )
    return pages


def preview_text(data: bytes, kind: str) -> str:
    """The file's first lines as plain text, for finding its period and account. Best effort."""
    try:
        if kind == "pdf":
            with _PDF_LOCK:
                pdf = _open_pdf(data)
                return "\n".join(
                    str(pdf[i].get_textpage().get_text_range()) for i in range(min(len(pdf), 2))
                )
        if kind in ("xlsx", "xls", "xlsb", "ods"):
            from python_calamine import CalamineWorkbook

            workbook = CalamineWorkbook.from_filelike(io.BytesIO(data))
            rows = workbook.get_sheet_by_index(0).to_python()[:PREVIEW_ROWS]
            return "\n".join(" ".join(str(cell) for cell in row if cell != "") for row in rows)
        if kind in ("image", "doc", "ole_other", "zip", "docx"):
            return ""
        return "\n".join(decode_text(data)[0].splitlines()[:PREVIEW_ROWS])
    except Exception:  # noqa: BLE001 - a preview is best effort; the checks say when it is missing
        return ""


async def in_pdf_worker[T](fn: Callable[[], T]) -> T:
    """Run PDF work on a worker thread: rendering a long statement would stall the event loop."""
    return await asyncio.to_thread(fn)


__all__ = [
    "MAX_DOC_PAGES",
    "DocError",
    "decode_text",
    "image_media_type",
    "in_pdf_worker",
    "is_locked",
    "pdf_pages",
    "preview_text",
    "render_pages",
    "sniff",
    "unlock",
    "unwrap_zip",
]
