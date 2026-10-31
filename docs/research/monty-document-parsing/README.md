# Can Monty run model-written parsers for every financial document?

**Verdict: yes for everything that has a text layer or cells, no for bad photos.** Monty ran a parser for every text-bearing format I could build
(PDF, xls/xlsx/ods, CSV/TSV/TXT, docx, HTML, RTF, OFX, MT940, CAMT, JSON), and the balance check caught every wrong amount and every missing row. **It does not catch wrong dates or wrong
narrations**: a day/month swap and an inserted column both came out VERIFIED (section F). Monty never touches the
file: the **host does most of the work** (sniffing, passwords, encodings, HTML/RTF/XML/ZIP, rotation, OCR). Clean scans and moderate phone photos worked
through host-side RapidOCR (OCR + check); a harsh photo returned nothing (route it to a vision model, which I could not test). `.doc` and `xlsb` statements were not tested.

**Limits of this study.** I wrote the fixtures and the parsers (after reading `doc_info`/`doc_words`/`doc_cells` output, not the generator), so the results are biased toward success.
Every parser also runs with a ~100-line helper block (`parsers/_prelude.py`: money, dates, line grouping) prepended, which I changed mid-spike; the scripts are less "model-written" than a fresh run would be.
Public samples (below) are text dumps, MT940/OFX/xlsb test files; I found no real Indian bank PDF or XLS. Vision models: NOT TESTED (no keys). Real statements: never touched.

## A. Documents (synthetic, two months per layout; "rerun" = the same script text on the second month in a fresh Monty, all passed)
Repairs = script/host edits before the first full pass (my own fixture bugs excluded). Time = host open + Monty run. Check = opening + credits − debits == closing, plus every running balance.
† relaxed row scoring (A2 ignores spaces; A10/A11 match narrations at >= 0.8 similarity). \* the file prints no opening/closing, so the opening came from the (simulated) ledger and/or the closing is the last row's balance.

| # | Host reader | Verdict | Rows right | Check | Repairs | s | Reason |
|---|---|---|---|---|---|---|---|
| 1 PDF debit/credit/balance cols | pypdfium2 | WORKS | 22/22 | VERIFIED | 0 | 0.1 | header-driven column bounds |
| 2 wrapped narrations | pypdfium2 | WORKS | 22/22† | VERIFIED | 0 | 0.1 | hard-wrapped words can't tell "Or"+"der" from "Order"; compared ignoring spaces |
| 3 Dr/Cr suffix, and signed (+/−) | pypdfium2 | WORKS | 22/22, 22/22 | VERIFIED | 0 | 0.1 | one script handles both |
| 4 multi-page, headers/footers, summary | pypdfium2 | WORKS | 120/120 | VERIFIED | 1 | 0.2 | summary block repeated "Opening Balance"; draft 1 got FAILED from the check |
| 5 password PDF | pypdfium2 | WORKS | 22/22 | VERIFIED | 0 | 0.1 | host opens it; wrong/missing password = host error code, Monty not involved |
| 6 landscape / rotated / sideways | pypdfium2 | WORKS WITH CHANGE | 22/22 ×3 | VERIFIED | 1 (host) | 0.1 | sideways page gave one-letter words until the host chose the reading rotation |
| 7 card, no running balance | pypdfium2 | WORKS | 19/19 | VERIFIED | 0 | 0.1 | dues as negatives; total check only (no chain) |
| 8 two accounts in one file | pypdfium2 | WORKS | 44/44 | VERIFIED | 1 | 0.1 | script must return a list of accounts, each checked alone |
| 9 Devanagari + ₹ | pypdfium2 | WORKS | 22/22 | VERIFIED | 0 | 0.1 | my reportlab font maps to Unicode; real-bank font maps untested |
| 10 image-only PDF | host RapidOCR | WORKS WITH CHANGE | 22/22† | VERIFIED | 1 (host) | 8 | try 1: 21/22 DISCREPANCY (check caught it); fix: scale-normalise OCR words |
| 11 phone photo (skew, noise) | host RapidOCR | WORKS WITH CHANGE | 22/22† | VERIFIED | 2 | 4.4 | OCR swapped "," and "."; fix: OCR-tolerant money parser. Harsh photo: 0 rows, **NOT FOR MONTY** |
| 12 UPI screenshot, receipt JPG | host RapidOCR | WORKS WITH CHANGE | 4/4, 4/4 fields | none possible | 0 | 1.3 | clean synthetic screens only; no balance to check |
| 13 xlsx merged cells, serials, floats | calamine | WORKS | 22/22 | VERIFIED | 0 | 0.04 | datetimes arrive as ISO text, bare serials as floats |
| 14 .xls BIFF | calamine | WORKS | 22/22 | VERIFIED | 0 | 0.06 | float drift (…0.7899999998) handled by rounding once |
| 15 .xls that is HTML | host html.parser | WORKS WITH CHANGE | 22/22 | VERIFIED | 1 | 0.03 | calamine can't open it; opening/closing sat in another table |
| 16 .xls that is TSV | host dialect sniff | WORKS WITH CHANGE | 22/22 | VERIFIED | 0 (host fix) | 0.03 | preamble lines fooled my first delimiter guess |
| 17 .ods | calamine | WORKS | 22/22 | VERIFIED | 0 | 0.03 | |
| 18 .xlsb | calamine | WORKS (reader only) | n/a | n/a | n/a | 0.001 | no xlsb writer, so no statement; reads calamine's public samples; chart sheet raises (host try/except) |
| 19 password xlsx | msoffcrypto + calamine | WORKS WITH CHANGE | 22/22 | VERIFIED | 0 | 0.5 | host decrypts; wrong password gives a host error |
| 20 CSV UTF-8 BOM | stdlib csv | WORKS WITH CHANGE | 22/22 | VERIFIED* | 0 | 0.03 | *file prints no opening balance: needs ledger opening |
| 21 CSV cp1252, `;`, preamble/footer | host decode + csv | WORKS WITH CHANGE | 22/22 | VERIFIED | 0 | 0.03 | Monty has no cp1252 codec (LookupError) |
| 22 TSV | stdlib csv | WORKS | 22/22 | VERIFIED* | 1 | 0.03 | my header match took "description" for "cr"; *ledger opening |
| 23 fixed-width TXT | host lines | WORKS | 22/22 | VERIFIED | 0 | 0.03 | offsets read off the data rows, not the header |
| 24 docx table | python-docx | WORKS | 22/22 | VERIFIED | 1 | 0.6 | label and number in one cell |
| 25 docx paragraphs | python-docx | WORKS | 22/22 | VERIFIED | 0 | 0.04 | regex per paragraph |
| 26 HTML net banking | host html.parser | WORKS | 22/22 | VERIFIED | 0 | 0.03 | `<script>` fake row ignored |
| 27 OFX v1 and QFX/XML | host lines | WORKS WITH CHANGE | 22/22, 22/22 | VERIFIED* | 0 | 0.03 | OFX has no opening or running balances: *ledger opening decides |
| 28 MT940 | host lines | WORKS | 22/22 | VERIFIED | 0 (2 on public) | 0.04 | public: 58 statements, 110 rows, all VERIFIED after multi-statement, funds-code and "0," fixes |
| 29 CAMT.053 | host lines + regex | WORKS WITH CHANGE | 22/22 | VERIFIED | 0 | 0.04 | Monty has no XML; regex fine on flat sample, fragile on `ns2:` prefixes/CDATA: host should parse XML (defusedxml) |
| 30 JSON (Account Aggregator shape) | host lines + `json` | WORKS | 22/22 | VERIFIED | 0 | 0.03 | |
| 31 .rtf / .doc | striprtf / none | WORKS WITH CHANGE / NOT TESTED | 22/22 (rtf) | VERIFIED\* | 1 (host) | 0.06 | striprtf mangled `\cellx`; .doc: no generator, no LibreOffice |
| 32 ZIP of PDFs | zipfile + host loop | WORKS WITH CHANGE | 44/44 | VERIFIED ×2 | 0 | 0.4 | 300 MB bomb refused in 3 ms; `../` member refused |
| 33 corrupt, truncated, empty, wrong ext | host sniff | WORKS WITH CHANGE | n/a | never VERIFIED | n/a | <0.1 | empty/truncated refused; NUL-damaged PDF gave 2 rows, FAILED; ext ignored (magic bytes) |
| 34 182 pages / 9,996 rows | pypdfium2 / csv | WORKS | 9,996/9,996 ×2 | VERIFIED (csv\*) | 0 | 5.6 / 0.4 | PDF: 3.0 host + 2.2 Monty; needs 184 host calls (see D) |
| 35 12-month file | pypdfium2 / calamine | WORKS | 480/480 ×2 | VERIFIED | 0 | 0.3 / 0.03 | |
| 36 salary slip | pypdfium2 | WORKS | 5/5 fields | n/a | 0 | 0.03 | statement script returns no accounts; classifier says `payslip` |
| 37 loan sanction letter | pypdfium2 | WORKS | 5/5 fields | n/a | 0 | 0.03 | same |
| 38 MF consolidated statement | pypdfium2 | WORKS | 2/2 fields | n/a | 0 | 0.03 | holdings, not transactions; classifier first called a card statement "unknown" (fixed) |

Public samples (each in its own directory, read with `python -I`, never copied here): `raptar231/indian-bank-statement-parser` (Apache-2.0) HDFC Apr-2026 text dump 13/13 rows and ICICI Jun-2026 12/12;
both have **one amount column, so debit/credit comes from the balance move and needs the ledger opening** (without it: FAILED, correct). `jseutter/ofxparse` (MIT) 3/3 and 3/3 rows (US OFX, no opening, ledger needed);
`wolph/mt940` (BSD-3) betterplace 97/97, ASNB 8/8, citi 5/5; `tafia/calamine` (MIT) xlsb samples read. Parsers are in `parsers/`.

## B. Language and stdlib (105 probes, each run in CPython and Monty: 84 identical, 2 differ, 17 fail, 2 error in both; `b_results.json`)
| Works | Differs or missing |
|---|---|
| `re` named groups, lookbehind/ahead, MULTILINE, finditer, sub (string, `\1`, `\g<n>`), split, fullmatch; `datetime` date, timedelta, strftime, `datetime.strptime` (`%d/%m/%Y`, `%d-%b-%Y`, `%B`, `%y`, `%I:%M %p`); `json`; `math`; `decimal`; `csv`; `collections`; str methods; `sorted(key=)`; comprehensions; simple classes, `@dataclass`; try/except/finally; f-strings; big ints | `re.sub` with a function, `re.VERBOSE`, `re.subn`, `lastindex`; `date.strptime`, `date.fromordinal`; `str.maketrans`; cp1252 decode; class inheritance, own exception classes, `__lt__`, `del`, generator functions, `__import__` |
| **`round(x, 2)` differs from CPython on 233 of 3,291 test inputs** (3-decimal values, many exactly on a half-cent) (`round(2.675, 2)` gives 2.68, CPython 2.67); `f"{x:.2f}"` and `int(round(x*100))` agree on all 3,291 | `decimal` exists and matched CPython on my 8 cases, contrary to `design.md`; `\w` keeps Devanagari vowel signs (better than CPython) |

## C. Limits (defaults: 30 s, 256 MiB, 1,000 suspensions, 100 nested calls in Code Mode)
| Measure | Result |
|---|---|
| A34 PDF, defaults | 5.6 s total (host word extraction 3.0 s); parse alone fits in **32 MiB** (16 MiB kills the worker); 2.2 s cached |
| Monty vs CPython | micro 1.1–2.6× slower; whole parser 2.9× (PDF, 60k words), 4.5× (10k-row CSV, includes 1 MB crossing) |
| Code Mode, 182 pages, one `doc_words` per page | **fails at 100 nested calls**; 400 works (184 calls); a `doc_words_range(first,last)` batch tool: 6 calls, 9,996 rows |
| Suspensions | raw Monty: 1 per host call, 1,000 per session (1,500 calls fail; two 900-call snippets in one session fail). Code Mode: ~2 per call, so ~500 nested calls per session |
| Largest host return | 100 MB string or bytes ok; 100k distinct word dicts (~8 MB JSON) ok, 200k killed the worker; 6M ints fail. Per-page returns (~300 words) are far below |
| Types crossing | raw: int, float, str, bytes, list, tuple, set, dict, date, datetime, timedelta, Decimal, namedtuple; not dataclass, UUID, Fraction, bytearray, range. Code Mode: tuple/set→list, date/datetime/Decimal/UUID→str, int keys→str, dataclass/pydantic→dict |

## D. Code Mode (FunctionModel, no LLM)
| Question | Answer |
|---|---|
| Nested `ModelRetry` | Script sees a plain `Exception` with the original message (catchable). Uncaught: `run_code` ends with "Runtime error" and the same message, and the model retries |
| Nested `ToolReturn` metadata | In `run_code`'s ToolReturnPart: `metadata['tool_returns'][call_id].metadata`; its `content` goes to the model as the next user part, not into the sandbox |
| Reaches `VercelAIAdapter`? | **No.** Stream has one `run_code` call (input code, output value). No nested calls, no metadata, no content. The app must emit cards itself |
| Replay in raw Monty | Yes in `AsyncMonty` with async host functions (22/22, 44/44). **Not** in sync `Monty` ("dict can't be awaited"); sync-style scripts fail in async. Code Mode also requires keyword-only args and runs a type checker that rejected valid draft code |
| Output size | A `run_code` that returns 10k rows puts 1.4M characters in the model's context: return counts, status and a sample |

## E. Host readers
pypdfium2 gave **identical word tokens to pdfplumber on 10 fixtures and 182 pages (60,677 words), about 10× faster** (3.0 s vs 29 s). I wrote ~60 lines of word grouping and reading-rotation detection;
pdfplumber would add ruling lines, table finding and font info, which no fixture needed. python-calamine read xls, xlsx, xlsb and ods (it lists hidden sheets, and raises on chart sheets).
Wheels on PyPI: python-calamine 0.9.0 has `cp314` for win_amd64 and manylinux_2_17 x86_64; pypdfium2, pydantic-monty-runtime and onnxruntime too. No Windows wheel was missing, so no container was used.

## F. Reuse
| Test | Result |
|---|---|
| Next month, fresh Monty | all 23 Jan/Feb pairs VERIFIED, same script text |
| Debit/Credit columns swapped | header-driven script adapts (22/22); hard-coded x-bounds script: DISCREPANCY, caught |
| New "Value Date" column | **both scripts VERIFIED with 0/22 narrations right** (value date glued into the text). Dedupe by narration hash would break silently |
| Day/month swap (document MM/DD, script DD/MM, days ≤ 12) | **VERIFIED with 17/18 dates wrong**; sums can't see dates. Statement-period containment (`check_statement(..., period=)`) flags it. With days up to 31 rows drop and the check fails |

## G. Security (`g_security.py`, `g_clock.py`)
`eval` and `exec` exist but stay inside the sandbox (`eval('1+1')` gave 2; `exec` of an `open('/etc/passwd')` was still blocked). Files, `/etc/passwd`, `pathlib`, `os.environ`/`getenv` (marker env var not visible), `socket`/`subprocess`/`urllib`/`ctypes`, `__import__`: all blocked. `while True` stopped at the 3 s limit; memory bombs (`'a'*10**10`, growing lists, `9**9**9`) stopped by `MemoryError`; recursion is a catchable `RecursionError`.
Regex: `(a+)+$` on 40 chars is instant (CPython: 7.5 s at 26 chars); backreference patterns hit "Max limit for backtracking count" in 0.03 s. Five hostile narrations (injected instructions, `os.system`, RTL/zero-width, 200 kB) came back as plain strings.
Found: raw Monty exposes the clock and `random` unless you set `os_policy`; `time.sleep(10)` ignores the feed limit unless `max_total_sleep_secs` is set (Code Mode blocks clock/random, allows sleep); host `csv` crashed on a 200 kB narration (fixed: field limit + `DocError`).

## What the design must change
1. **Host surface.** Four functions carried every format (text formats as one-line cells, docx/HTML tables as sheets). In practice two more were needed: `ledger_opening()` and a batch `doc_words_range`; results must be per account. Budget for a large host layer (about 400 lines here).
2. **Host functions return only JSON-native types** (str, int, float, bool, None, list, dict). Code Mode turns tuples, sets, dates and Decimals into lists and strings; raw Monty does not, so anything else makes a script see different values on server replay.
3. **One calling convention.** Code Mode scripts are `await` + keyword-only + type-checked; replay needs `AsyncMonty` with async functions. Code Mode also keeps variables between `run_code` calls, so only the fresh-session server rerun proves a script stands alone.
4. **Version the helper block.** Ship a fixed prelude (or make money/date/line-grouping host functions) and store its version with each saved script; my mid-spike change to `money()` silently changed every parser.
5. **VERIFIED is not enough.** Single-amount, OFX and CSV statements need the ledger opening. Add period containment, but take the period from an independent source (ledger continuity, month names in the header): if the script's own date parsing supplies it, a swapped format flips both and the check is circular.
   Add a saved column-header fingerprint so a new column fails instead of passing with dirty narrations. Note the weakening: a zero-row statement is VERIFIED when opening equals closing, so a script that drops every row passes then; require rows unless the header says there were none.
6. **Keep `run_code` results compact** (counts, status, a sample) and rows host-side: 10k rows is 1.4M characters of context, and it keeps payer notes out of the model's context, in line with the outside-text rule in `design.md`. Raise `max_tool_calls`/suspensions or batch.
7. Nested tool metadata is not streamed: emit cards from the `run_code` return (answers the open L2 question).
8. `design.md` is wrong that Monty has no `decimal`; keep integer paise anyway (`round(x, 2)` diverges). Set `os_policy` and sleep limits for replay.
9. OCR only as a checked fallback (RapidOCR 4-8 s/page CPU); weak photos and everything else to the vision path.
10. `check_real.py` PASS means "amounts balance" unless you give `--period`. Its bundled parsers key on my fixtures' exact header words, so on a real statement a FAIL usually means no bundled parser fits that layout; write a script and pass `--parser`.

## Not tested
Vision models; real Indian bank PDFs/XLS (none found); `.doc`; xlsb statements; PaddleOCR (no cp314 wheel for paddlepaddle); Tesseract (not installed); Linux run (wheels exist, never executed); password ZIP;
CAMT with prefixed namespaces; real-bank font maps; multi-column or ruled-table PDFs; Code Mode `eager`/`speculate`/remote sandbox; load and concurrency.

## Sources, versions, licences, rerun
Public files (each fetched into its own scratch directory, read with `python -I`, not copied here; URL = `https://github.com/<owner>/<repo>/blob/HEAD/<path>`): `raptar231/indian-bank-statement-parser` (Apache-2.0) `tests/fixtures/hdfc/hdfc_savings-april-2026.txt`,
`tests/fixtures/icici/icici_savings-modern-jun-2026.txt` (nine more fetched, unused); `jseutter/ofxparse` (MIT) `tests/fixtures/{bank_medium,checking,multiple_accounts}.ofx`; `wolph/mt940` (BSD-3-Clause) `mt940_tests/{ASNB/mt940.txt,citi/mt940.txt,betterplace/sepa_mt9401.sta}`; `tafia/calamine` (MIT) `tests/{any_sheets,date}.xlsb`.
Windows 11 10.0.26300, CPython 3.14.10, uv 0.13.6. pydantic-monty 1.4.0, pydantic-ai-slim 2.64.0, pydantic-ai-harness 0.64.0 (all MIT), pypdfium2 5.14.0 (BSD-3/Apache-2.0), python-calamine 0.9.0 (MIT), pdfplumber 0.11.10 (MIT), msoffcrypto-tool 6.0.0 (MIT), python-docx 1.2.0 (MIT), striprtf 0.0.35 (BSD-3),
olefile 0.47 (BSD), rapidocr 3.10.0 (Apache-2.0) + onnxruntime 1.31.1 (MIT), starlette 1.10.0 (BSD-3). Test-only: reportlab 5.1.0, openpyxl 3.1.5, xlwt 1.3.0, odfpy 1.4.1, Pillow 13.0.0, numpy 2.5.4.
Licence audit of all 65 installed distributions (`importlib.metadata`): no GPL/AGPL in anything I recommend. Exceptions to know: **odfpy** lists Apache plus GPL/LGPL classifiers (used only to write the `.ods` fixture; do not ship it);
**opencv-python-headless** (pulled in by rapidocr, Apache-2.0) redistributes FFmpeg under LGPL as `cv2/opencv_videoio_ffmpeg500_64.dll` (30 MB; confirmed in its `LICENSE-3RD-PARTY.txt`): LGPL, not GPL, but check before shipping; certifi and tqdm are MPL-2.0 (transitive).
```
uv venv --python 3.14 .venv && uv pip install "pydantic-ai-harness[code-mode]" pydantic-monty pypdfium2 python-calamine pdfplumber reportlab openpyxl xlwt python-docx Pillow msoffcrypto-tool odfpy striprtf rapidocr onnxruntime numpy starlette
set MONTY_SPIKE=<scratch dir>   # fixtures and downloads live there, never in the repo
python make_fixtures.py && python gen_scan.py && python gen_docs.py && python run_matrix.py   # then b_lang, b_extra, c_limits, d_codemode, d_replay, d_types, e_readers, f_reuse, g_security, g_clock, host_edge_cases
python check_real.py YOUR_FILE_OR_FOLDER [--password PW] [--ledger-opening PAISE] [--period START END] [--ocr]   # prints PASS/FAIL and counts only
```
