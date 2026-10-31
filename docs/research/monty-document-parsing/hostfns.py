"""Host side (normal CPython): open any file, expose doc_info / doc_words / doc_cells / check_statement.

What Monty sees is only plain data: dict, list, str, int, float, bool, None. Dates arrive as ISO strings.
Packages (all permissive): pypdfium2 (Apache-2.0/BSD-3), python-calamine (MIT), msoffcrypto-tool (MIT),
python-docx (MIT), striprtf (BSD-3), olefile (BSD-2), stdlib csv/html.parser/zipfile/xml.etree.
Optional: rapidocr (Apache-2.0) for image pages.
"""
import csv
import datetime as dt
import io
import json
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path

MAX_ZIP_TOTAL = 200 * 1024 * 1024
MAX_ZIP_MEMBERS = 50


class DocError(Exception):
    """Raised for readable problems (wrong password, corrupt, unsupported)."""

    def __init__(self, code, msg=""):
        super().__init__(f"{code}: {msg}")
        self.code = code


def sniff(data: bytes) -> str:
    """Kind from magic bytes + content, never from the extension."""
    if not data:
        raise DocError("empty", "zero-byte file")
    h = data[:8]
    if data[:5] == b"%PDF-":
        return "pdf"
    if h[:4] == b"\x89PNG" or h[:3] == b"\xff\xd8\xff" or data[:4] in (b"II*\x00", b"MM\x00*") or data[:2] == b"BM":
        return "image"
    if h[:4] == b"PK\x03\x04":
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise DocError("corrupt", "bad zip")
        names = set(z.namelist())
        if "xl/workbook.xml" in names:
            return "xlsx"
        if "xl/workbook.bin" in names:
            return "xlsb"
        if "word/document.xml" in names:
            return "docx"
        if "mimetype" in names and b"opendocument.spreadsheet" in z.read("mimetype"):
            return "ods"
        return "zip"
    if h[:4] == b"\xd0\xcf\x11\xe0":
        import olefile
        try:
            ole = olefile.OleFileIO(io.BytesIO(data))
        except Exception:
            raise DocError("corrupt", "bad OLE container")
        ents = {"/".join(e) for e in ole.listdir()}
        if "EncryptedPackage" in ents:
            return "encrypted_ooxml"
        if "Workbook" in ents or "Book" in ents:
            return "xls"
        if "WordDocument" in ents:
            return "doc"
        return "ole_other"
    text = decode_text(data)[0]
    t = text.lstrip()[:2000]
    low = t.lower()
    if t.startswith("{\\rtf"):
        return "rtf"
    if "<html" in low or "<table" in low or "<!doctype html" in low:
        return "html"
    if "OFXHEADER" in t or "<OFX>" in t.upper()[:3000]:
        return "ofx"
    if t.startswith(":20:") or "\n:20:" in t[:200]:
        return "mt940"
    if t.startswith("<?xml") or t.startswith("<Document"):
        return "camt" if "BkToCstmrStmt" in text[:5000] else "xml"
    if t[:1] in "{[":
        try:
            json.loads(text)
            return "json"
        except Exception:
            pass
    return "text"


def decode_text(data: bytes):
    if data[:3] == b"\xef\xbb\xbf":
        return data[3:].decode("utf-8"), "utf-8-sig"
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16"), "utf-16"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace"), "cp1252"


def _clean_cell(v):
    if isinstance(v, (dt.datetime,)):
        return v.date().isoformat() if v.time() == dt.time(0) else v.isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, (dt.time, dt.timedelta)):
        return str(v)
    if isinstance(v, str):
        return v
    return v


class _HTMLTables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.stack, self.skip, self.lines = [], [], 0, []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag == "table":
            self.stack.append({"rows": [], "row": None, "cell": None})
        elif tag == "tr" and self.stack:
            self.stack[-1]["row"] = []
        elif tag in ("td", "th") and self.stack and self.stack[-1]["row"] is not None:
            self.stack[-1]["cell"] = []

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag in ("td", "th") and self.stack and self.stack[-1]["cell"] is not None:
            t = self.stack[-1]
            t["row"].append(" ".join("".join(t["cell"]).split()))
            t["cell"] = None
        elif tag == "tr" and self.stack and self.stack[-1]["row"] is not None:
            t = self.stack[-1]
            if any(c for c in t["row"]):
                t["rows"].append(t["row"])
            t["row"] = None
        elif tag == "table" and self.stack:
            self.tables.append(self.stack.pop()["rows"])

    def handle_data(self, data):
        if self.skip:
            return
        if self.stack and self.stack[-1]["cell"] is not None:
            self.stack[-1]["cell"].append(data)


def html_tables(text):
    p = _HTMLTables()
    p.feed(text)
    return [t for t in p.tables if t]


class Doc:
    """One opened file. Pages for PDF/image, sheets for tabular things, lines for text-like."""

    def __init__(self, path, password=None, ocr=None):
        self.path = Path(path)
        self.password = password
        self.ocr = ocr                    # callable(PIL image) -> list of words, or None
        self.data = self.path.read_bytes()
        self.kind = sniff(self.data)
        self._pdf = None
        self._sheets = None               # list[(name, rows)]
        self._lines = None
        self._words_cache = {}
        self._open()

    # ------------------------------------------------------------------ opening
    def _open(self):
        k = self.kind
        if k == "encrypted_ooxml":
            import msoffcrypto
            if not self.password:
                raise DocError("password_required", "encrypted Office file")
            of = msoffcrypto.OfficeFile(io.BytesIO(self.data))
            try:
                of.load_key(password=self.password)
                out = io.BytesIO()
                of.decrypt(out)
            except Exception as e:
                raise DocError("wrong_password", type(e).__name__)
            self.data = out.getvalue()
            self.kind = k = sniff(self.data)
        if k == "pdf":
            import pypdfium2 as pdfium
            try:
                self._pdf = pdfium.PdfDocument(self.data, password=self.password)
            except pdfium.PdfiumError as e:
                m = str(e).lower()
                if "password" in m:
                    raise DocError("password_required" if not self.password else "wrong_password", "PDF")
                raise DocError("corrupt", str(e)[:80])
        elif k in ("xlsx", "xls", "xlsb", "ods"):
            from python_calamine import CalamineWorkbook
            try:
                wb = CalamineWorkbook.from_filelike(io.BytesIO(self.data))
            except Exception as e:
                raise DocError("corrupt", f"{type(e).__name__}: {str(e)[:80]}")
            self._sheets = []
            for n in wb.sheet_names:
                try:
                    rows = wb.get_sheet_by_name(n).to_python(skip_empty_area=False)
                except OSError:                  # chart sheets and other non-cell sheets raise here
                    rows = []
                self._sheets.append((n, [[_clean_cell(c) for c in self._trim(r)] for r in rows]))
        elif k == "docx":
            import docx
            d = docx.Document(io.BytesIO(self.data))
            self._sheets = [(f"table{i}", [[c.text for c in r.cells] for r in t.rows]) for i, t in enumerate(d.tables)]
            self._sheets.append(("paragraphs", [[p.text] for p in d.paragraphs if p.text.strip()]))
        elif k == "html":
            text = decode_text(self.data)[0]
            self._sheets = [(f"table{i}", t) for i, t in enumerate(html_tables(text))]
        elif k == "rtf":
            from striprtf.striprtf import rtf_to_text
            raw = re.sub(r"\\cellx-?\d+", "", decode_text(self.data)[0])      # drop table column definitions first
            raw = raw.replace("\\cell", "\\tab ").replace("\\row", "\\par ")
            self._lines = rtf_to_text(raw).splitlines()
            self._sheets = [("text", [[l] for l in self._lines])]
        elif k == "zip":
            self.members = self._zip_members()
        elif k in ("doc", "ole_other", "image", "xml"):
            pass
        else:  # text-like
            text, self.encoding = decode_text(self.data)
            self._lines = text.splitlines()
            self._sheets = [("text", self._text_rows(self._lines))]

    @staticmethod
    def _trim(r):
        r = list(r)
        while r and r[-1] in ("", None):
            r.pop()
        return r

    def _text_rows(self, lines):
        """Dialect sniffing: pick the delimiter whose per-line count is most consistent (preamble/footer tolerant)."""
        if self.kind != "text":
            return [[l] for l in lines]
        sample = [l for l in lines if l.strip()][:60]
        best, best_freq = None, 0.0
        for d in ("	", ";", "|", ","):
            counts = [l.count(d) for l in sample]
            if not counts:
                break
            modal = max(set(counts), key=counts.count)
            freq = counts.count(modal) / len(counts)
            if modal >= 2 and freq >= 0.5 and freq > best_freq + 1e-9:
                best, best_freq = d, freq
        if best:
            self.delimiter = best
            csv.field_size_limit(4 * 1024 * 1024)       # default 128 KB makes csv raise on one oversized narration
            try:
                return [r for r in csv.reader(lines, delimiter=best)]
            except csv.Error as e:
                raise DocError("corrupt", f"csv: {e}")
        return [[l] for l in lines]            # fixed-width / free text: one cell per line

    def _zip_members(self):
        z = zipfile.ZipFile(io.BytesIO(self.data))
        infos = [i for i in z.infolist() if not i.is_dir()]
        if len(infos) > MAX_ZIP_MEMBERS or sum(i.file_size for i in infos) > MAX_ZIP_TOTAL:
            raise DocError("zip_too_big", "member count or uncompressed size over limit")
        out = []
        for i in infos:
            if i.filename.startswith("/") or ".." in Path(i.filename).parts:
                raise DocError("zip_path", i.filename)
            out.append((Path(i.filename).name, z.read(i)))
        return out

    # ------------------------------------------------------------------ the four functions
    def info(self):
        d = {"kind": self.kind}
        if self._pdf is not None:
            pages = []
            for i in range(len(self._pdf)):
                n_chars = self._pdf[i].get_textpage().count_chars()
                pages.append({"page": i, "chars": n_chars, "text_layer": n_chars > 20,
                              "rotation": self._pdf[i].get_rotation()})
            d["pages"] = len(pages)
            d["text_layer_pages"] = sum(p["text_layer"] for p in pages)
            d["page_info"] = pages if len(pages) <= 5 else pages[:2] + [{"...": len(pages) - 2}]
        if self.kind == "image":
            d["pages"] = 1; d["text_layer_pages"] = 0
        if self._sheets is not None:
            d["sheets"] = [{"name": n, "rows": len(r), "cols": max((len(x) for x in r), default=0)} for n, r in self._sheets]
        if getattr(self, "delimiter", None):
            d["delimiter"] = self.delimiter
        if getattr(self, "encoding", None):
            d["encoding"] = self.encoding
        return d

    def cells(self, sheet=0):
        if self._sheets is None:
            raise DocError("no_sheets", f"{self.kind} has no cell view")
        if isinstance(sheet, str):
            for n, r in self._sheets:
                if n == sheet:
                    return r
            raise DocError("no_such_sheet", sheet)
        return self._sheets[sheet][1]

    def words(self, page=0):
        if page in self._words_cache:
            return self._words_cache[page]
        if self._pdf is not None:
            w = self._pdf_words(page)
            if not w and self.ocr:
                w = self.ocr(self._pdf[page].render(scale=300 / 72).to_pil())
        elif self.kind == "image" and self.ocr:
            from PIL import Image
            w = self.ocr(Image.open(io.BytesIO(self.data)).convert("RGB"))
        else:
            raise DocError("no_words", f"{self.kind} has no word view")
        self._words_cache[page] = w
        return w

    @staticmethod
    def _xf(rot, l, b, r, t, W, H):
        """Raw PDF box (origin bottom-left) -> reading frame box (origin top-left) for a reading rotation."""
        if rot == 0:
            return l, r, H - t, H - b
        if rot == 90:
            return b, t, l, r
        if rot == 180:
            return W - r, W - l, b, t
        return H - t, H - b, W - r, W - l

    def _pdf_words(self, page_no):
        pg = self._pdf[page_no]
        x0, y0, x1, y1 = pg.get_mediabox()
        W, H = x1 - x0, y1 - y0
        tp = pg.get_textpage()
        text = tp.get_text_range()
        raw = []
        for i, ch in enumerate(text):
            if ch.isspace() or ch == "\x00":
                raw.append(None)
                continue
            l, b, r, t = tp.get_charbox(i, loose=True)
            raw.append((ch, l - x0, b - y0, r - x0, t - y0))
        # pick the reading rotation: the one in which successive characters move left-to-right on one line
        sample = [c for c in raw if c][:400]
        best, best_score = pg.get_rotation(), -1
        for rot in (pg.get_rotation(), 0, 90, 180, 270):
            sc, prev = 0, None
            for ch, l, b, r, t in sample:
                vx0, vx1, vt, vb = self._xf(rot, l, b, r, t, W, H)
                if prev and 0 <= vx0 - prev[0] < 3 * (vb - vt) and abs((vt + vb) / 2 - prev[1]) < 0.5 * (vb - vt):
                    sc += 1
                prev = (vx1, (vt + vb) / 2)
            if sc > best_score:
                best, best_score = rot, sc
        self._last_reading_rotation = best
        out, cur = [], None
        for c in raw:
            if c is None:
                if cur:
                    out.append(cur)
                cur = None
                continue
            ch, l, b, r, t = c
            vx0, vx1, vt, vb = self._xf(best, l, b, r, t, W, H)
            hgt = max(vb - vt, 1.0)
            if cur is not None and (abs((vt + vb) / 2 - (cur["top"] + cur["bottom"]) / 2) > 0.5 * hgt
                                    or vx0 - cur["x1"] > 0.45 * hgt or vx0 - cur["x1"] < -hgt):
                out.append(cur)
                cur = None
            if cur is None:
                cur = {"text": ch, "x0": vx0, "x1": vx1, "top": vt, "bottom": vb}
            else:
                cur["text"] += ch
                cur["x1"] = max(cur["x1"], vx1)
                cur["top"] = min(cur["top"], vt)
                cur["bottom"] = max(cur["bottom"], vb)
        if cur:
            out.append(cur)
        for w in out:
            for k in ("x0", "x1", "top", "bottom"):
                w[k] = round(w[k], 1)
        return out


# ---------------------------------------------------------------------- balance check
def check_statement(rows, opening, closing, period=None):
    """Integer paise. rows: dicts with debit, credit (>=0, one of them non-zero) and optional balance."""
    res = {"status": "FAILED", "rows": 0, "reason": None}
    try:
        n = len(rows)
        res["rows"] = n
        if n == 0:
            if isinstance(opening, int) and opening == closing:
                res["status"] = "VERIFIED"       # a quiet month: no movements, balance unchanged
                res["reason"] = "empty statement, balance unchanged"
                return res
            res["reason"] = "no rows"
            return res
        for i, r in enumerate(rows):
            for k in ("debit", "credit"):
                if not isinstance(r.get(k), int) or isinstance(r.get(k), bool) or r[k] < 0:
                    res["reason"] = f"row {i}: {k} not a non-negative int"
                    return res
            if r["debit"] and r["credit"]:
                res["reason"] = f"row {i}: both debit and credit"
                return res
            if r.get("balance") is not None and not isinstance(r["balance"], int):
                res["reason"] = f"row {i}: balance not int"
                return res
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(r.get("date"))):
                res["reason"] = f"row {i}: bad date {r.get('date')!r}"
                return res
        if not isinstance(opening, int) or not isinstance(closing, int):
            res["reason"] = "opening/closing not int"
            return res
        cr = sum(r["credit"] for r in rows)
        dr = sum(r["debit"] for r in rows)
        res["computed_closing"] = opening + cr - dr
        res["total_ok"] = res["computed_closing"] == closing
        run, brk, nb = opening, [], 0
        for i, r in enumerate(rows):
            run += r["credit"] - r["debit"]
            if r.get("balance") is not None:
                nb += 1
                if r["balance"] != run:
                    brk.append(i)
                    run = r["balance"]
        res["balance_rows"] = nb
        res["chain_ok"] = (not brk) if nb else None
        res["chain_breaks"] = brk[:5]
        dates = [r["date"] for r in rows]
        res["dates_monotonic"] = dates == sorted(dates) or dates == sorted(dates, reverse=True)
        if period:
            res["dates_in_period"] = all(period[0] <= d <= period[1] for d in dates)
        ok = res["total_ok"] and (res["chain_ok"] is not False)
        if period and not res["dates_in_period"]:
            ok = False                      # a day/month swap keeps the sums right and the dates wrong
            res["reason"] = "dates outside the statement period"
        res["status"] = "VERIFIED" if ok else "DISCREPANCY"
        return res
    except Exception as e:      # a malformed script result must never crash the host
        res["reason"] = f"{type(e).__name__}: {e}"
        return res
