lines = [r[0] for r in doc_cells(0)]
rows = []
prev = ledger_opening()
for ln in lines:
    m = re.match(r'(\d+)\s+(\d\d\.\d\d\.\d{4})\s+([\d,]+\.\d\d)\s+([\d,]+\.\d\d)\s+(.*)', ln)
    if not m:
        continue
    amount = money(m.group(3))
    balance = money(m.group(4))
    if prev is None:
        debit, credit = None, None
    elif balance == prev - amount:
        debit, credit = amount, 0
    elif balance == prev + amount:
        debit, credit = 0, amount
    else:
        debit, credit = None, None
    rows.append({'date': to_iso(m.group(2)), 'desc': m.group(5).strip(), 'debit': debit, 'credit': credit, 'balance': balance})
    prev = balance
good = [r for r in rows if r['debit'] is not None]
{'accounts': [{'name': 'icici', 'opening': ledger_opening(), 'closing': rows[-1]['balance'], 'rows': good, 'unexplained': len(rows) - len(good)}]}
