info = doc_info()
f = {}
first = True
for ln in lines_of(doc_words(0)):
    t = line_text(ln)
    if first:
        f['merchant'] = t
        first = False
    m = re.search(r'Date:\s*(\d\d/\d\d/\d{4})', t)
    if m:
        f['date'] = to_iso(m.group(1))
    if t.startswith('TOTAL'):
        f['amount'] = money(ln[-1]['text'])
    m = re.search(r'Ref\s+(\d{12})', t)
    if m:
        f['ref'] = m.group(1)
{'not_a_statement': 'receipt', 'fields': f}
