info = doc_info()
opening = None
closing = None
rows = []
for si in range(len(info['sheets'])):
    for row in doc_cells(si):
        for cell in row:
            m = re.search(r'Opening Balance:\s*([^\d]*)([\d,]+\.\d\d)', cell)
            if m:
                opening = money(m.group(2))
            m = re.search(r'Closing Balance:\s*([^\d]*)([\d,]+\.\d\d)', cell)
            if m:
                closing = money(m.group(2))
        if len(row) == 5 and to_iso(row[0]) and row[2] in ('DR', 'CR'):
            amt = money(row[3])
            rows.append({'date': to_iso(row[0]), 'desc': row[1], 'debit': amt if row[2] == 'DR' else 0,
                         'credit': amt if row[2] == 'CR' else 0, 'balance': money(row[4])})
{'accounts': [{'name': 'main', 'opening': opening, 'closing': closing, 'rows': rows}]}
