"""Run the A-matrix: python run_matrix.py [case-prefix ...]   (each run is appended to attempts.jsonl)

CASES: id -> (doc file, parser file, password, ocr, desc-mode, rerun-doc or None)
"""
import sys as _s, pathlib as _p
_s.path.insert(0, str(_p.Path(__file__).resolve().parent))   # lets `python -I` (isolated mode) still import sibling modules
import json
import sys

from common import FIX
from runner import evaluate, PARSERS

PW = "ARJU0101"
CASES = {
    "A1": ("a1_sep_cols_jan.pdf", "pdf_sep_cols.py", None, False, "exact", "a1_sep_cols_feb.pdf"),
    "A2": ("a2_wrapped_jan.pdf", "pdf_wrapped.py", None, False, "nospace", "a2_wrapped_feb.pdf"),
    "A3": ("a3_drcr_jan.pdf", "pdf_drcr.py", None, False, "exact", "a3_drcr_feb.pdf"),
    "A3b": ("a3b_signed_jan.pdf", "pdf_drcr.py", None, False, "exact", "a3b_signed_feb.pdf"),
    "A4": ("a4_multipage_jan.pdf", "pdf_sep_cols.py", None, False, "exact", "a4_multipage_feb.pdf"),
    "A5": ("a5_password_jan.pdf", "pdf_sep_cols.py", PW, False, "exact", "a5_password_feb.pdf"),
    "A6a": ("a6a_landscape_jan.pdf", "pdf_sep_cols.py", None, False, "exact", "a6a_landscape_feb.pdf"),
    "A6b": ("a6b_rotate90_jan.pdf", "pdf_sep_cols.py", None, False, "exact", "a6b_rotate90_feb.pdf"),
    "A6c": ("a6c_landscape_rotated_upright_jan.pdf", "pdf_sep_cols.py", None, False, "exact", "a6c_landscape_rotated_upright_feb.pdf"),
    "A7": ("a7_card_jan.pdf", "pdf_card.py", None, False, "exact", "a7_card_feb.pdf"),
    "A8": ("a8_two_accounts_jan.pdf", "pdf_sep_cols.py", None, False, "exact", "a8_two_accounts_feb.pdf"),
    "A9": ("a9_devanagari_jan.pdf", "pdf_sep_cols.py", None, False, "exact", "a9_devanagari_feb.pdf"),
    "A10": ("a10_image_only_jan.pdf", "pdf_sep_cols.py", None, True, "fuzzy", "a10_image_only_feb.pdf"),
    "A11": ("a11_phone_photo_jan.jpg", "pdf_sep_cols_ocr.py", None, True, "fuzzy", None),
    "A11b": ("a11b_harsh_photo_jan.jpg", "pdf_sep_cols_ocr.py", None, True, "fuzzy", None),
    "A12u": ("a12_upi_screenshot.png", "upi_screenshot_fields.py", None, True, "none", None),
    "A12r": ("a12_receipt.jpg", "receipt_fields.py", None, True, "none", None),
    "A36": ("a36_salary_slip.pdf", "payslip_fields.py", None, False, "none", None),
    "A37": ("a37_loan_sanction_letter.pdf", "loan_letter_fields.py", None, False, "none", None),
    "A38": ("a38_mf_cas.pdf", "mf_cas_fields.py", None, False, "none", None),
    "A13": ("a13_xlsx_jan.xlsx", "sheet_header.py", None, False, "exact", "a13_xlsx_feb.xlsx"),
    "A14": ("a14_xls_biff_jan.xls", "sheet_header.py", None, False, "exact", "a14_xls_biff_feb.xls"),
    "A15": ("a15_html_as_xls_jan.xls", "sheet_header.py", None, False, "exact", "a15_html_as_xls_feb.xls"),
    "A16": ("a16_tsv_as_xls_jan.xls", "sheet_header.py", None, False, "exact", "a16_tsv_as_xls_feb.xls"),
    "A17": ("a17_ods_jan.ods", "sheet_header.py", None, False, "exact", "a17_ods_feb.ods"),
    "A19": ("a19_xlsx_password_jan.xlsx", "sheet_header.py", PW, False, "exact", "a19_xlsx_password_feb.xlsx"),
    "A20": ("a20_csv_bom_jan.csv", "sheet_header.py", None, False, "exact", "a20_csv_bom_feb.csv"),
    "A21": ("a21_csv_cp1252_jan.csv", "sheet_header.py", None, False, "exact", "a21_csv_cp1252_feb.csv"),
    "A22": ("a22_tsv_jan.tsv", "sheet_header.py", None, False, "exact", "a22_tsv_feb.tsv"),
    "A23": ("a23_fixed_jan.txt", "text_fixed.py", None, False, "exact", "a23_fixed_feb.txt"),
    "A24": ("a24_docx_table.docx", "sheet_header.py", None, False, "exact", None),
    "A25": ("a25_docx_paras.docx", "docx_paras.py", None, False, "exact", None),
    "A26": ("a26_html.html", "html_table.py", None, False, "exact", None),
    "A27": ("a27_ofx.ofx", "ofx.py", None, False, "exact", None),
    "A27b": ("a27_qfx_xml.qfx", "ofx.py", None, False, "exact", None),
    "A28": ("a28_mt940.sta", "mt940.py", None, False, "exact", None),
    "A29": ("a29_camt053.xml", "camt053.py", None, False, "exact", None),
    "A30": ("a30_json_aa.json", "json_aa.py", None, False, "exact", None),
    "A31": ("a31_rtf.rtf", "rtf_table.py", None, False, "exact", None),
    "A34p": ("a34_big_200p.pdf", "pdf_sep_cols.py", None, False, "exact", None),
    "A34c": ("a34_big_10k.csv", "sheet_header.py", None, False, "exact", None),
    "A35": ("a35_12month.pdf", "pdf_sep_cols.py", None, False, "exact", None),
    "A35x": ("a35_12month.xlsx", "sheet_header.py", None, False, "exact", None),
}

SHORT = ("status", "rows_right", "rows_total", "fields_right", "fields_total", "monty_s", "host_open_s", "error", "notes")


def run_case(cid, rerun=False, log=True):
    doc, parser, pw, ocr, dm, rerun_doc = CASES[cid]
    d = rerun_doc if rerun else doc
    if d is None:
        return None
    r = evaluate(str(FIX / d), str(PARSERS / parser), pw, ocr, dm, (cid + ("_rerun" if rerun else "")) if log else None, quiet=True)
    return r


if __name__ == "__main__":
    wanted = sys.argv[1:] or list(CASES)
    for cid in CASES:
        if not any(cid == w or (cid.startswith(w) and not w[-1:].isdigit()) for w in wanted):
            continue
        for rerun in (False, True):
            r = run_case(cid, rerun)
            if r is None:
                continue
            short = {k: r.get(k) for k in SHORT if r.get(k) not in (None, [], "")}
            print(f"{cid}{'*rerun' if rerun else '':7s}", json.dumps(short, ensure_ascii=False, default=str)[:420])
