info = doc_info()
holdings = 0
total = None
for ln in lines_of(doc_words(0)):
    t = line_text(ln)
    if re.match(r'.* \d+/\d+ .*', t) and len(ln) > 6:
        holdings += 1
    if t.startswith('Total market value'):
        total = money(ln[-1]['text'])
{'not_a_statement': 'mf_cas', 'fields': {'holdings': holdings, 'total_value': total}}
