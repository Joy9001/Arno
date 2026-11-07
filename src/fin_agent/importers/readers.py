"""Readers: turn any file into cells or positioned words on the host.

Monty never touches the file. It sees plain dicts, lists, strings, ints, floats, bools and
None. Every date crosses as an ISO string. The reader is chosen by magic bytes, never the
extension, and a locked Office file is decrypted here before Calamine sees it.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import struct
import zipfile
import zlib
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

MAX_ZIP_TOTAL = 200 * 1024 * 1024
MAX_ZIP_MEMBERS = 50

_KIND_NAMES = {
    "xlsx": "xlsx",
    "xls": "xls",
    "xlsb": "xlsb",
    "ods": "ods",
    "docx": "docx",
    "html": "html",
    "rtf": "rtf",
    "ofx": "ofx",
    "mt940": "mt940",
    "camt": "camt",
    "xml": "xml",
    "json": "json",
    "pdf": "pdf",
    "image": "image",
    "text": "text",
    "zip": "zip",
    "doc": "doc",
    "ole_other": "ole_other",
    "encrypted_ooxml": "encrypted_ooxml",
}


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
    ):
        return "image"
    if head[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
        except zipfile.BadZipFile as exc:
            raise DocError("corrupt", "bad zip") from exc
        if "xl/workbook.xml" in names:
            return "xlsx"
        if "xl/workbook.bin" in names:
            return "xlsb"
        if "word/document.xml" in names:
            return "docx"
        if "mimetype" in names:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if b"opendocument.spreadsheet" in archive.read("mimetype"):
                    return "ods"
        return "zip"
    if head[:4] == b"\xd0\xcf\x11\xe0":
        import olefile

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


def decode_text(data: bytes) -> tuple[str, str]:
    if data[:3] == b"\xef\xbb\xbf":
        return data[3:].decode("utf-8"), "utf-8-sig"
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16"), "utf-16"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace"), "cp1252"


def _clean_cell(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        return value.date().isoformat() if value.time() == dt.time(0) else value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, (dt.time, dt.timedelta)):
        return str(value)
    if isinstance(value, str):
        return value
    return value


class _HTMLTables(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self.stack: list[dict[str, Any]] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        del attrs
        if tag in ("script", "style"):
            self.skip += 1
        elif tag == "table":
            self.stack.append({"rows": [], "row": None, "cell": None})
        elif tag == "tr" and self.stack:
            self.stack[-1]["row"] = []
        elif tag in ("td", "th") and self.stack and self.stack[-1]["row"] is not None:
            self.stack[-1]["cell"] = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag in ("td", "th") and self.stack and self.stack[-1]["cell"] is not None:
            table = self.stack[-1]
            table["row"].append(" ".join("".join(table["cell"]).split()))
            table["cell"] = None
        elif tag == "tr" and self.stack and self.stack[-1]["row"] is not None:
            table = self.stack[-1]
            if any(cell for cell in table["row"]):
                table["rows"].append(table["row"])
            table["row"] = None
        elif tag == "table" and self.stack:
            self.tables.append(self.stack.pop()["rows"])

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        if self.stack and self.stack[-1]["cell"] is not None:
            self.stack[-1]["cell"].append(data)


def html_tables(text: str) -> list[list[list[str]]]:
    parser = _HTMLTables()
    parser.feed(text)
    return [table for table in parser.tables if table]


class Doc:
    """One opened file: pages for PDF/image, sheets for tabular things, lines for text."""

    def __init__(self, data: bytes, *, password: str | None = None) -> None:
        self.data = data
        self.password = password
        self.kind = sniff(data)
        self.name = ""
        self._pdf: Any = None
        self._sheets: list[tuple[str, list[list[Any]]]] | None = None
        self._lines: list[str] | None = None
        self._words_cache: dict[int, list[dict[str, Any]]] = {}
        self.delimiter: str | None = None
        self.encoding: str | None = None
        self._open()

    # Opening -------------------------------------------------------------------------------
    def _open(self) -> None:
        kind = self.kind
        if kind == "encrypted_ooxml":
            import msoffcrypto

            if not self.password:
                raise DocError("password_required", "encrypted Office file")
            office = msoffcrypto.OfficeFile(io.BytesIO(self.data))
            try:
                office.load_key(password=self.password)
                out = io.BytesIO()
                office.decrypt(out)
            except Exception as exc:  # noqa: BLE001 - a wrong key is a readable problem
                raise DocError("wrong_password", type(exc).__name__) from exc
            self.data = out.getvalue()
            self.kind = kind = sniff(self.data)
        if kind == "pdf":
            import pypdfium2 as pdfium

            try:
                self._pdf = pdfium.PdfDocument(self.data, password=self.password)
            except pdfium.PdfiumError as exc:
                message = str(exc).lower()
                if "password" in message:
                    code = "password_required" if not self.password else "wrong_password"
                    raise DocError(code, "PDF") from exc
                raise DocError("corrupt", str(exc)[:80]) from exc
        elif kind in ("xlsx", "xls", "xlsb", "ods"):
            from python_calamine import CalamineWorkbook

            try:
                workbook = CalamineWorkbook.from_filelike(io.BytesIO(self.data))
            except Exception as exc:  # noqa: BLE001 - calamine raises many types
                raise DocError("corrupt", f"{type(exc).__name__}: {str(exc)[:80]}") from exc
            self._sheets = []
            for name in workbook.sheet_names:
                try:
                    rows = workbook.get_sheet_by_name(name).to_python(skip_empty_area=False)
                except OSError:  # chart sheets and other non-cell sheets
                    rows = []
                self._sheets.append(
                    (name, [[_clean_cell(cell) for cell in self._trim(row)] for row in rows])
                )
        elif kind == "docx":
            import docx

            document = docx.Document(io.BytesIO(self.data))
            self._sheets = [
                (f"table{i}", [[cell.text for cell in row.cells] for row in table.rows])
                for i, table in enumerate(document.tables)
            ]
            self._sheets.append(
                ("paragraphs", [[p.text] for p in document.paragraphs if p.text.strip()])
            )
        elif kind == "html":
            text, self.encoding = decode_text(self.data)
            self._sheets = [(f"table{i}", table) for i, table in enumerate(html_tables(text))]
        elif kind == "rtf":
            from striprtf.striprtf import rtf_to_text

            raw = re.sub(r"\\cellx-?\d+", "", decode_text(self.data)[0])
            raw = raw.replace("\\cell", "\\tab ").replace("\\row", "\\par ")
            self._lines = rtf_to_text(raw).splitlines()
            self._sheets = [("text", [[line] for line in self._lines])]
        elif kind == "zip":
            self.members = self._zip_members()
        elif kind in ("camt", "xml"):
            try:
                self._lines = self._xml_lines(self.data)
                self._sheets = [("text", [[line] for line in self._lines])]
            except Exception:  # noqa: BLE001 - malformed XML gets the text reader
                text, self.encoding = decode_text(self.data)
                self._lines = text.splitlines()
                self._sheets = [("text", [[line] for line in self._lines])]
        elif kind in ("doc", "ole_other", "image"):
            pass
        else:
            text, self.encoding = decode_text(self.data)
            self._lines = text.splitlines()
            self._sheets = [("text", self._text_rows(self._lines))]

    def readable_bytes(self) -> bytes:
        """The bytes a later replay can open without the password.

        An encrypted Office file is decrypted in place, so `data` is already plain. A PDF is
        rewritten by PDFium, which drops the password from the saved copy.
        """
        if self.kind == "pdf" and self.password and self._pdf is not None:
            import pypdfium2.raw as pdfium_c

            buffer = io.BytesIO()
            # Without this flag PDFium keeps the encryption, and the copy still needs the password.
            self._pdf.save(buffer, flags=pdfium_c.FPDF_REMOVE_SECURITY)
            return buffer.getvalue()
        return self.data

    @staticmethod
    def _trim(row: list[Any]) -> list[Any]:
        row = list(row)
        while row and row[-1] in ("", None):
            row.pop()
        return row

    def _text_rows(self, lines: list[str]) -> list[list[str]]:
        """Pick the delimiter whose per-line count is most consistent; tolerate preambles."""
        if self.kind != "text":
            return [[line] for line in lines]
        sample = [line for line in lines if line.strip()][:60]
        best: str | None = None
        best_frequency = 0.0
        for delimiter in ("\t", ";", "|", ","):
            counts = [line.count(delimiter) for line in sample if line.count(delimiter)]
            if not counts:
                continue
            modal = max(set(counts), key=counts.count)
            frequency = counts.count(modal) / len(counts)
            if modal >= 1 and frequency >= 0.5 and frequency > best_frequency + 1e-9:
                best, best_frequency = delimiter, frequency
        if best:
            self.delimiter = best
            csv.field_size_limit(4 * 1024 * 1024)
            try:
                return [row for row in csv.reader(lines, delimiter=best)]
            except csv.Error as exc:
                raise DocError("corrupt", f"csv: {exc}") from exc
        return [[line] for line in lines]

    def _zip_members(self) -> list[tuple[str, bytes]]:
        with zipfile.ZipFile(io.BytesIO(self.data)) as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            if (
                len(infos) > MAX_ZIP_MEMBERS
                or sum(info.file_size for info in infos) > MAX_ZIP_TOTAL
            ):
                raise DocError("zip_too_big", "member count or uncompressed size over limit")
            members: list[tuple[str, bytes]] = []
            for info in infos:
                name = info.filename
                parts = Path(name).parts
                if name.startswith(("/", "\\")) or ".." in parts:
                    raise DocError("zip_path", name)
                members.append((Path(name).name, archive.read(info)))
            return members

    @staticmethod
    def _xml_lines(data: bytes) -> list[str]:
        """Flatten CAMT or other XML into `path: text` lines; the script has no XML library."""
        import xml.etree.ElementTree as ET

        root = ET.fromstring(data)
        lines: list[str] = []

        def local(tag: str) -> str:
            return tag.rsplit("}", 1)[-1]

        def walk(element: Any, path: str) -> None:
            name = local(element.tag)
            here = f"{path}/{name}" if path else name
            if element.text and element.text.strip():
                lines.append(f"{here}: {' '.join(element.text.split())}")
            for child in element:
                walk(child, here)

        walk(root, "")
        return lines

    # Host functions -----------------------------------------------------------------------
    def info(self) -> dict[str, Any]:
        info: dict[str, Any] = {"kind": self.kind}
        if self._pdf is not None:
            pages = []
            for index in range(len(self._pdf)):
                page = self._pdf[index]
                chars = page.get_textpage().count_chars()
                pages.append(
                    {
                        "page": index,
                        "chars": chars,
                        "text_layer": chars > 20,
                        "rotation": page.get_rotation(),
                    }
                )
            info["pages"] = len(pages)
            info["text_layer_pages"] = sum(1 for page in pages if page["text_layer"])
            info["page_info"] = pages if len(pages) <= 5 else pages[:2] + [{"...": len(pages) - 2}]
        if self.kind == "image":
            info["pages"] = 1
            info["text_layer_pages"] = 0
        if self._sheets is not None:
            info["sheets"] = [
                {
                    "name": name,
                    "rows": len(rows),
                    "cols": max((len(row) for row in rows), default=0),
                }
                for name, rows in self._sheets
            ]
        if self.delimiter:
            info["delimiter"] = self.delimiter
        if self.encoding:
            info["encoding"] = self.encoding
        return info

    def cells(self, sheet: Any = 0) -> list[list[Any]]:
        if self._sheets is None:
            raise DocError("no_sheets", f"{self.kind} has no cell view")
        if isinstance(sheet, str):
            for name, rows in self._sheets:
                if name == sheet:
                    return rows
            raise DocError("no_such_sheet", sheet)
        try:
            return self._sheets[int(sheet)][1]
        except (IndexError, TypeError, ValueError) as exc:
            raise DocError("no_such_sheet", str(sheet)) from exc

    def words(self, page: Any = 0) -> list[dict[str, Any]]:
        page = int(page)
        if page in self._words_cache:
            return self._words_cache[page]
        if self._pdf is not None:
            words = self._pdf_words(page)
        else:
            raise DocError("no_words", f"{self.kind} has no word view")
        self._words_cache[page] = words
        return words

    def words_range(self, first: Any = 0, last: Any = 0) -> list[list[dict[str, Any]]]:
        first, last = int(first), int(last)
        if last < first:
            raise DocError("bad_range", "last is before first")
        if last - first > 50:
            raise DocError("bad_range", "at most 50 pages at once")
        return [self.words(page) for page in range(first, min(last, self.page_count()) + 1)]

    def page_count(self) -> int:
        if self._pdf is not None:
            return len(self._pdf)
        if self.kind == "image":
            return 1
        return 0

    @staticmethod
    def _xf(
        rotation: int, left: float, bottom: float, right: float, top: float, w: float, h: float
    ):
        if rotation == 0:
            return left, right, h - top, h - bottom
        if rotation == 90:
            return bottom, top, left, right
        if rotation == 180:
            return w - right, w - left, bottom, top
        return h - top, h - bottom, w - right, w - left

    def _pdf_words(self, page_no: int) -> list[dict[str, Any]]:
        page = self._pdf[page_no]
        x0, y0, x1, y1 = page.get_mediabox()
        width, height = x1 - x0, y1 - y0
        textpage = page.get_textpage()
        text = textpage.get_text_range()
        raw: list[Any] = []
        for index, char in enumerate(text):
            if char.isspace() or char == "\x00":
                raw.append(None)
                continue
            left, bottom, right, top = textpage.get_charbox(index, loose=True)
            raw.append((char, left - x0, bottom - y0, right - x0, top - y0))
        sample = [item for item in raw if item][:400]
        best, best_score = page.get_rotation(), -1
        for rotation in (page.get_rotation(), 0, 90, 180, 270):
            score, previous = 0, None
            for _char, left, bottom, right, top in sample:
                vx0, vx1, vt, vb = self._xf(rotation, left, bottom, right, top, width, height)
                if (
                    previous
                    and 0 <= vx0 - previous[0] < 3 * (vb - vt)
                    and abs((vt + vb) / 2 - previous[1]) < 0.5 * (vb - vt)
                ):
                    score += 1
                previous = (vx1, (vt + vb) / 2)
            if score > best_score:
                best, best_score = rotation, score
        self._last_reading_rotation = best
        out: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for item in raw:
            if item is None:
                if current:
                    out.append(current)
                current = None
                continue
            char, left, bottom, right, top = item
            vx0, vx1, vt, vb = self._xf(best, left, bottom, right, top, width, height)
            height_here = max(vb - vt, 1.0)
            if current is not None and (
                abs((vt + vb) / 2 - (current["top"] + current["bottom"]) / 2) > 0.5 * height_here
                or vx0 - current["x1"] > 0.45 * height_here
                or vx0 - current["x1"] < -height_here
            ):
                out.append(current)
                current = None
            if current is None:
                current = {"text": char, "x0": vx0, "x1": vx1, "top": vt, "bottom": vb}
            else:
                current["text"] += char
                current["x1"] = max(current["x1"], vx1)
                current["top"] = min(current["top"], vt)
                current["bottom"] = max(current["bottom"], vb)
        if current:
            out.append(current)
        for word in out:
            for key in ("x0", "x1", "top", "bottom"):
                word[key] = round(word[key], 1)
        return out

    def page_png(self, page: Any = 0) -> bytes:
        """A PNG for the vision role, encoded with the standard library."""
        if self.kind == "image":
            return self.data
        if self._pdf is None:
            raise DocError("no_pages", f"{self.kind} has no page view")
        bitmap = self._pdf[int(page)].render(scale=2)
        width, height = bitmap.width, bitmap.height
        buffer = bytes(bitmap.buffer)
        # PDFium gives BGRA, bottom-up. Convert to RGB, top-down.
        channels = 4 if len(buffer) >= width * height * 4 else 3
        rows = []
        stride = width * channels
        for y in range(height - 1, -1, -1):
            line = bytearray(width * 3)
            base = y * stride
            for x in range(width):
                pixel = buffer[base + x * channels : base + x * channels + 3]
                line[x * 3 : x * 3 + 3] = bytes((pixel[2], pixel[1], pixel[0]))
            rows.append(b"\x00" + bytes(line))
        raw = b"".join(rows)

        def chunk(tag: bytes, payload: bytes) -> bytes:
            return (
                struct.pack(">I", len(payload))
                + tag
                + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
            )

        header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw, 6))
            + chunk(b"IEND", b"")
        )


def open_documents(data: bytes, *, password: str | None = None) -> list[Doc]:
    """Open a file, or every statement inside a ZIP, each on its own."""
    return _open_documents(data, password=password)


def _open_documents(data: bytes, *, password: str | None = None) -> list[Doc]:
    doc = Doc(data, password=password)
    if doc.kind != "zip":
        return [doc]
    docs: list[Doc] = []
    for name, member in doc.members:
        if name.lower().endswith("/"):
            continue
        try:
            child = Doc(member, password=password)
        except DocError:
            continue
        if child.kind == "zip":
            continue
        child.name = name
        docs.append(child)
    if not docs:
        raise DocError("empty_zip", "no readable statement inside the archive")
    return docs


def describe(data: bytes, *, password: str | None = None) -> dict[str, Any]:
    """A short description for the import card, with no statement text."""
    doc = Doc(data, password=password)
    return {"kind": doc.kind, **doc.info()}


__all__ = ["Doc", "DocError", "describe", "decode_text", "open_documents", "sniff"]
