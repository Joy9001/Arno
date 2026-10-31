info = doc_info()
f = {}
for ln in lines_of(doc_words(0)):
    t = line_text(ln)
    if f.get('employer') is None and t.isupper() and 'LIMITED' in t:
        f['employer'] = t
    m = re.search(r'month of ([A-Za-z]+) (\d{4})', t)
    if m:
        f['month'] = m.group(2) + '-' + str(MONTHS[m.group(1)[:3].lower()]).zfill(2)
    if t.startswith('Gross Earnings'):
        f['gross'] = money(ln[2]['text'])
    if t.startswith('NET PAY'):
        f['net'] = money(ln[-1]['text'])
    if 'Total Deductions' in t:
        f['deductions'] = money(ln[-1]['text'])
{'not_a_statement': 'payslip', 'fields': f}
