info = doc_info()
f = {}
for ln in lines_of(doc_words(0)):
    t = line_text(ln)
    if t.startswith('Paid to'):
        f['merchant'] = t[len('Paid to '):]
    elif t.startswith('₹') or t.startswith('Rs'):
        f['amount'] = money(ln[0]['text'])
    elif re.fullmatch(r'\d{12}', t):
        f['ref'] = t
    m = re.match(r'(\d{1,2}) ([A-Za-z]{3}) (\d{4})', t)
    if m:
        f['date'] = to_iso(m.group(1) + ' ' + m.group(2) + ' ' + m.group(3))
{'not_a_statement': 'single_payment_screenshot', 'fields': f}
