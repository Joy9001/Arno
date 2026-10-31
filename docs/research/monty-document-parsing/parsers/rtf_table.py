lines = [r[0] for r in doc_cells(0)]
opening = None
rows = []
for ln in lines:
    if ln.startswith('Opening Balance'):
        opening = money(ln.partition(':')[2])
        continue
    cells = ln.split('\t')
    if len(cells) >= 5 and to_iso(cells[0]):
        dr = money(cells[2]) if cells[2] else 0
        cr = money(cells[3]) if cells[3] else 0
        rows.append({'date': to_iso(cells[0]), 'desc': cells[1], 'debit': dr, 'credit': cr, 'balance': money(cells[4])})
{'accounts': [{'name': 'rtf', 'opening': opening, 'closing': None, 'rows': rows}]}
