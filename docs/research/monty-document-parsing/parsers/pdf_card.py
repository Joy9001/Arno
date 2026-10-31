info = doc_info()
rows = []
prev_bal = None
total_due = None
in_table = False
for pg in range(info['pages']):
    for ln in lines_of(doc_words(pg)):
        t = line_text(ln)
        if t.startswith('Previous Balance'):
            prev_bal = money(ln[-1]['text'])
        elif t.startswith('Total Amount Due'):
            total_due = money(ln[4]['text'])
        elif ln[0]['text'] == 'Date' and 'Transaction' in t:
            in_table = True
        elif in_table and to_iso(ln[0]['text']):
            ws = ln[1:]
            credit_flag = ws[-1]['text'] == 'Cr'
            if credit_flag:
                ws = ws[:-1]
            amt = money(ws[-1]['text'])
            desc = ' '.join([w['text'] for w in ws[:-1]])
            rows.append({'date': to_iso(ln[0]['text']), 'desc': desc, 'debit': 0 if credit_flag else amt,
                         'credit': amt if credit_flag else 0, 'balance': None})
# a card is a liability: show dues as negative balances so opening + credits - debits == closing
opening = -prev_bal
closing = -total_due
acct = {'name': 'card', 'opening': opening, 'closing': closing, 'rows': rows, 'check': check_statement(rows, opening, closing)}
{'accounts': [acct]}
