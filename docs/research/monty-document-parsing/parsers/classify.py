info = doc_info()
text = ''
dated_lines = 0
has_table_head = False
for pg in range(info.get('pages', 0)):
    for ln in lines_of(doc_words(pg)):
        t = line_text(ln)
        text = text + '\n' + t
        if to_iso(ln[0]['text']):
            dated_lines += 1
        low = t.lower()
        if ('debit' in low and 'credit' in low and 'balance' in low) or ('withdrawal' in low and 'deposit' in low and 'balance' in low) \
                or ('amount' in low and 'balance' in low and 'date' in low):
            has_table_head = True
low = text.lower()
signals = {'dated_lines': dated_lines, 'table_head': has_table_head, 'opening_balance': 'opening balance' in low}
kind = 'unknown'
if has_table_head and dated_lines >= 3 and ('opening balance' in low or 'previous balance' in low):
    kind = 'bank_statement'
elif dated_lines >= 3 and 'previous balance' in low and 'total amount due' in low:
    kind = 'card_statement'
elif 'payslip' in low and 'net pay' in low:
    kind = 'payslip'
elif 'sanction letter' in low and 'rate of interest' in low:
    kind = 'loan_sanction_letter'
elif 'consolidated account statement' in low and 'folio' in low and 'units' in low:
    kind = 'mf_cas'
{'kind': kind, 'fields': {'signals': signals}}
