import json
doc = json.loads('\n'.join([r[0] for r in doc_cells(0)]))
acct = doc['account']


def amt(v):
    if isinstance(v, (int, float)):
        return money_float(v)
    return money(v)


rows = []
for t in acct['transactions']['transaction']:
    a = amt(t['amount'])
    is_debit = t['type'] == 'DEBIT'
    rows.append({'date': t['valueDate'], 'desc': t['narration'], 'debit': a if is_debit else 0, 'credit': 0 if is_debit else a,
                 'balance': amt(t['currentBalance'])})
s = acct['summary']
{'accounts': [{'name': acct['maskedAccNumber'], 'opening': amt(s['openingBalance']) if 'openingBalance' in s else None,
               'closing': amt(s['currentBalance']), 'rows': rows}]}
