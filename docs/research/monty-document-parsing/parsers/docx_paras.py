info = doc_info()
lines = [r[0] for r in doc_cells('paragraphs')]
opening = None
closing = None
rows = []
pat = re.compile(r'(\d\d/\d\d/\d{4})\s+(.*?)\s+([\d,]+\.\d\d) (Dr|Cr)\s+Bal ([\d,]+\.\d\d)')
for ln in lines:
    m = pat.fullmatch(ln.strip())
    if m:
        amt = money(m.group(3))
        rows.append({'date': to_iso(m.group(1)), 'desc': m.group(2), 'debit': amt if m.group(4) == 'Dr' else 0,
                     'credit': amt if m.group(4) == 'Cr' else 0, 'balance': money(m.group(5))})
    elif ln.startswith('Opening Balance'):
        opening = money(ln.partition(':')[2])
    elif ln.startswith('Closing Balance'):
        closing = money(ln.partition(':')[2])
{'accounts': [{'name': 'main', 'opening': opening, 'closing': closing, 'rows': rows}]}
