lines = [r[0] for r in doc_cells(0)]
# Layout (read off the text): a row starts with dd/mm/yy; narration wraps over following lines; the amount, the value
# date and the running balance sit somewhere in the block as "<dd/mm/yy> <amount> <balance>". There is one amount
# column in the text, so debit/credit comes from how the running balance moved.
blocks = []
for ln in lines:
    if re.match(r'\d\d/\d\d/\d\d ', ln):
        blocks.append([ln])
    elif ln.startswith('Page No') or ln.startswith('MR. ') or ln.startswith('Statement of'):
        break
    elif blocks:
        blocks[-1].append(ln)
rows = []
prev = ledger_opening()
for b in blocks:
    txt = ' '.join(b)
    m = re.search(r'(\d\d/\d\d/\d\d) ([\d,]+\.\d\d) ([\d,]+\.\d\d)\b', txt)
    amount = money(m.group(2))
    balance = money(m.group(3))
    desc = (txt[:m.start()] + ' ' + txt[m.end():]).strip()
    desc = re.sub(r'\b\d{15,16}\b', '', desc)       # reference column
    desc = ' '.join(desc.split())
    if prev is None:
        debit, credit = None, None
    elif balance == prev - amount:
        debit, credit = amount, 0
    elif balance == prev + amount:
        debit, credit = 0, amount
    else:
        debit, credit = None, None                 # amount does not explain the balance move
    rows.append({'date': to_iso(b[0][:8]), 'desc': desc, 'debit': debit, 'credit': credit, 'balance': balance, 'amount': amount})
    prev = balance
good = [r for r in rows if r['debit'] is not None]
{'accounts': [{'name': 'hdfc', 'opening': ledger_opening(), 'closing': rows[-1]['balance'], 'rows': good, 'unexplained': len(rows) - len(good)}]}
