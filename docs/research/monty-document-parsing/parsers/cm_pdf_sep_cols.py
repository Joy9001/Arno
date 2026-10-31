from typing import Any

info = await doc_info()
accts: list[dict[str, Any]] = []
cur: dict[str, Any] = {}
title = 'account'
prev_text = ''
cols: dict[str, Any] = {}   # header-derived: narr_x0, ref_x0, debit_x1, credit_x1, balance_x1
for pg in range(info['pages']):
    words = await doc_words(page=pg)
    for ln in lines_of(words):
        t = line_text(ln)
        if t.startswith('Account No'):
            title = prev_text
        prev_text = t
        if t.startswith('Opening Balance') and money(ln[-1]['text']) is not None:
            if cur:
                accts.append(cur)
            cur = {'name': title, 'opening': money(ln[-1]['text']), 'closing': None, 'rows': []}
        elif t.startswith('Closing Balance') and money(ln[-1]['text']) is not None and cur:
            cur['closing'] = money(ln[-1]['text'])
        elif ln[0]['text'] == 'Date' and 'Debit' in t and 'Credit' in t:
            h = {w['text']: w for w in ln}
            cols = {'narr': h['Narration']['x0'], 'ref': h['Chq/Ref']['x0'],
                    'deb': h['Debit']['x1'], 'cre': h['Credit']['x1'], 'bal': h['Balance']['x1']}
        elif cols and cur and to_iso(ln[0]['text']):
            amt: dict[str, Any] = {'deb': None, 'cre': None, 'bal': None}
            desc_words: list[str] = []
            ref_words: list[str] = []
            for w in ln[1:]:
                m = money(w['text'])
                if m is not None and w['x0'] > cols['ref'] + 40 and '.' in w['text']:
                    best = 'deb'
                    for k in ('cre', 'bal'):
                        if abs(w['x1'] - cols[k]) < abs(w['x1'] - cols[best]):
                            best = k
                    amt[best] = m
                elif w['x0'] >= cols['ref'] - 2:
                    ref_words.append(w['text'])
                else:
                    desc_words.append(w['text'])
            cur['rows'].append({'date': to_iso(ln[0]['text']), 'desc': ' '.join(desc_words), 'ref': ' '.join(ref_words),
                                'debit': amt['deb'] or 0, 'credit': amt['cre'] or 0, 'balance': amt['bal']})
if cur:
    accts.append(cur)
for a in accts:
    a['check'] = await check_statement(rows=a['rows'], opening=a['opening'], closing=a['closing'])
{'accounts': accts}
