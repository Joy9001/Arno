info = doc_info()


def amount(v):
    """cell -> paise or None; floats rounded once, strings parsed exactly."""
    if v is None or v == '':
        return None
    if isinstance(v, (int, float)):
        return money_float(v)
    return money(v)


def first_amount(row):
    for c in row[1:]:
        a = amount(c)
        if a is not None:
            return a
    m = re.search(r'balance\W+([\d,]+\.\d\d)', str(row[0]), re.I)   # label and number in one cell
    if m:
        return money(m.group(1))
    return None


def hit(cell, n):
    c = cell.strip(' .')
    if len(n) <= 2:
        return c == n          # 'dr' / 'cr' must be the whole header, not part of 'description'
    return n in c


NAMES = {
    'date': ['txn date', 'transaction date', 'date'],
    'desc': ['narration', 'particulars', 'description', 'details', 'remarks'],
    'ref': ['chq', 'ref'],
    'debit': ['withdrawal', 'debit', 'dr'],
    'credit': ['deposit', 'credit', 'cr'],
    'balance': ['balance'],
}
grids = [doc_cells(si) for si in range(len(info['sheets']))]
opening = None
closing = None
hdr_sheet = -1
hdr = -1
idx = {}
for si in range(len(grids)):
    grid = grids[si]
    for ri in range(len(grid)):
        row = grid[ri]
        first = str(row[0]).lower() if row else ''
        if hdr < 0 and first.startswith('opening balance') and opening is None:
            opening = first_amount(row)
        elif hdr < 0 and first.startswith('closing balance') and closing is None:
            closing = first_amount(row)
        if hdr < 0 and find_col(row, ['balance']) >= 0 and find_col(row, ['withdrawal', 'debit']) >= 0 and find_col(row, ['date']) >= 0:
            hdr = ri
            hdr_sheet = si
            low = [str(c).lower() for c in row]
            for k in NAMES:
                for i in range(len(row)):
                    ok = False
                    for n in NAMES[k]:
                        if hit(low[i], n):
                            ok = True
                    if k == 'date' and 'value' in low[i]:
                        ok = False
                    if ok and k not in idx:
                        idx[k] = i
accts = []
if hdr >= 0:
    rows = []
    grid = grids[hdr_sheet]
    for ri in range(hdr + 1, len(grid)):
        row = grid[ri]
        if len(row) <= idx['balance']:
            continue
        d = date_any(row[idx['date']])
        if d is None:
            continue
        dr = amount(row[idx['debit']]) if len(row) > idx['debit'] else None
        cr = amount(row[idx['credit']]) if len(row) > idx['credit'] else None
        rows.append({'date': d, 'desc': str(row[idx['desc']]).strip(), 'debit': dr or 0, 'credit': cr or 0,
                     'balance': amount(row[idx['balance']])})
    accts.append({'name': info['sheets'][hdr_sheet]['name'], 'opening': opening, 'closing': closing, 'rows': rows})
{'accounts': accts}
