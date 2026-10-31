info = doc_info()
f = {}
for ln in lines_of(doc_words(0)):
    t = line_text(ln)
    if f.get('lender') is None and t.isupper() and 'LIMITED' in t:
        f['lender'] = t
    if t.startswith('Loan Amount Sanctioned'):
        f['amount'] = money(t.partition(':')[2])
    if t.startswith('Rate of Interest'):
        m = re.search(r'([\d.]+)%', t)
        f['rate'] = m.group(1)
    if t.startswith('Tenure'):
        f['tenure'] = int(re.search(r'(\d+) months', t).group(1))
    if t.startswith('EMI'):
        f['emi'] = money(t.partition(':')[2])
{'not_a_statement': 'loan_sanction_letter', 'fields': f}
