lines = [r[0] for r in doc_cells(0)]
opening = None
closing = None
rows = []
for ln in lines:
    if ln.startswith('Opening Balance'):
        opening = money(ln.partition(':')[2])
    elif ln.startswith('Closing Balance'):
        closing = money(ln.partition(':')[2])
    elif re.match(r'\d\d/\d\d/\d{4}  ', ln):
        # right-aligned numeric columns end at fixed character offsets (read off the data rows): debit 66, credit 80, balance 96
        deb = cre = bal = None
        desc_end = len(ln)
        for m in re.finditer(r'[\d,]+\.\d\d', ln[50:]):
            e = m.end() + 50
            v = money(m.group(0))
            if e <= 67:
                deb = v
            elif e <= 81:
                cre = v
            else:
                bal = v
            desc_end = min(desc_end, m.start() + 50)
        rows.append({'date': to_iso(ln[:10]), 'desc': ln[12:desc_end].strip(), 'debit': deb or 0, 'credit': cre or 0, 'balance': bal})
{'accounts': [{'name': 'main', 'opening': opening, 'closing': closing, 'rows': rows}]}
