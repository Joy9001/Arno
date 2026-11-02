"""The versioned helper block prepended to every parser, on submit and on replay.

Saved scripts store `helper_version`, so a change here cannot silently change an old parser.
The functions return JSON-native values and use integer paise.
"""

from __future__ import annotations

HELPER_VERSION = "1"

PRELUDE = '''import re
import datetime

MONTHS = {'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
          'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12}


def money(s):
    """'1,00,000.00' / '-1,234.5' / '(1,234.00)' / 'Rs. 5' -> integer paise, or None."""
    t = str(s).replace(',', '').replace('\\u20b9', '').replace('Rs.', '').replace('Rs', '')
    t = t.replace('INR', '').strip()
    neg = False
    if t.startswith('(') and t.endswith(')'):
        neg = True
        t = t[1:-1].strip()
    if t.startswith('-'):
        neg = True
        t = t[1:].strip()
    elif t.startswith('+'):
        t = t[1:].strip()
    whole, dot, frac = t.partition('.')
    if whole == '':
        whole = '0'
    if not whole.isdigit() or (frac and not frac.isdigit()) or len(frac) > 2:
        return None
    v = int(whole) * 100 + int((frac + '00')[:2])
    return -v if neg else v


def money_float(x):
    """A spreadsheet float -> paise, rounded once at the end."""
    return int(round(x * 100))


def to_iso(s):
    """dd/mm/yyyy, dd-mm-yy, dd.mm.yyyy, dd-Mon-yyyy, dd Mon yyyy -> ISO; day first."""
    m = re.fullmatch(r'(\\d{1,2})[/\\-. ]([A-Za-z]{3,9}|\\d{1,2})[/\\-. ](\\d{4}|\\d{2})',
                     str(s).strip())
    if not m:
        return None
    d = int(m.group(1))
    mo = m.group(2)
    if mo.isdigit():
        mo = int(mo)
    else:
        mo = MONTHS.get(mo[:3].lower())
        if mo is None:
            return None
    y = int(m.group(3))
    if y < 100:
        y += 2000
    try:
        return datetime.date(y, mo, d).isoformat()
    except ValueError:
        return None


def serial_to_iso(v):
    return (datetime.date(1899, 12, 30) + datetime.timedelta(days=int(v))).isoformat()


def date_any(v):
    """ISO text from the host, dd/mm/yyyy text, or a spreadsheet serial."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return serial_to_iso(v) if 20000 < v < 80000 else None
    s = str(v).strip()
    if re.fullmatch(r'\\d{4}-\\d{2}-\\d{2}', s):
        return s
    return to_iso(s)


def lines_of(words, tol=None):
    """Group positioned words into lines, each sorted left to right."""
    if not words:
        return []
    if tol is None:
        hs = sorted([w['bottom'] - w['top'] for w in words])
        tol = 0.6 * hs[len(hs) // 2]
    ws = sorted(words, key=lambda w: (w['top'], w['x0']))
    out, cur, cy = [], [], None
    for w in ws:
        yc = (w['top'] + w['bottom']) / 2
        if cy is not None and abs(yc - cy) <= tol:
            cur.append(w)
        else:
            if cur:
                out.append(sorted(cur, key=lambda a: a['x0']))
            cur, cy = [w], yc
    if cur:
        out.append(sorted(cur, key=lambda a: a['x0']))
    return out


def join(line, lo=-1e9, hi=1e9):
    return ' '.join([w['text'] for w in line if lo <= w['x0'] < hi])


def line_text(line):
    return ' '.join([w['text'] for w in line])


def find_col(row, names):
    """The first cell whose lowercase text contains any of names; -1 if none."""
    for i, c in enumerate(row):
        cl = str(c).lower()
        for n in names:
            if n in cl:
                return i
    return -1
'''

__all__ = ["HELPER_VERSION", "PRELUDE"]
