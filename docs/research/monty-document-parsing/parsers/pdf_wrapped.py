info = doc_info()
accts = []
cur = None
title = 'account'
prev_text = ''
cols = None
last_top = None
for pg in range(info['pages']):
    last_top = None
    for ln in lines_of(doc_words(pg)):
        t = line_text(ln)
        if t.startswith('Account No'):
            title = prev_text
        prev_text = t
        if t.startswith('Opening Balance') and money(ln[-1]['text']) is not None:
            if cur is not None:
                accts.append(cur)
            cur = {'name': title, 'opening': money(ln[-1]['text']), 'closing': None, 'rows': []}
            last_top = None
        elif t.startswith('Closing Balance') and money(ln[-1]['text']) is not None:
            cur['closing'] = money(ln[-1]['text'])
            last_top = None
        elif ln[0]['text'] == 'Date' and 'Debit' in t and 'Credit' in t:
            h = {w['text']: w for w in ln}
            cols = {'narr': h['Narration']['x0'], 'ref': h['Chq/Ref']['x0'],
                    'deb': h['Debit']['x1'], 'cre': h['Credit']['x1'], 'bal': h['Balance']['x1']}
            last_top = None
        elif cols and cur is not None and to_iso(ln[0]['text']):
            amt = {'deb': None, 'cre': None, 'bal': None}
            desc_words = []
            ref_words = []
            for w in ln[1:]:
                m = money(w['text'])
                if m is not None and w['x0'] > cols['ref'] + 40 and '.' in w['text']:
                    best = 'deb'
                    for k in ('cre', 'bal'):
                        if abs(w['x1'] - cols[k]) < abs(w['x1'] - cols[best]):
                            best = k
                    amt[best] = m
                elif w['x0'] >= cols['ref'] - 2 and w['text'].isdigit():
                    ref_words.append(w['text'])
                else:
                    desc_words.append(w['text'])
            cur['rows'].append({'date': to_iso(ln[0]['text']), 'desc': ' '.join(desc_words), 'ref': ' '.join(ref_words),
                                'debit': amt['deb'] or 0, 'credit': amt['cre'] or 0, 'balance': amt['bal']})
            last_top = ln[0]['top']
        elif cols and cur is not None and cur['rows'] and last_top is not None and ln[0]['top'] - last_top < 16 and ln[0]['x0'] >= cols['narr'] - 2:
            # wrapped narration: a line with no date, directly under the previous row line
            cur['rows'][-1]['desc'] = cur['rows'][-1]['desc'] + ' ' + t
            last_top = ln[0]['top']
if cur is not None:
    accts.append(cur)
for a in accts:
    a['check'] = check_statement(a['rows'], a['opening'], a['closing'])
{'accounts': accts}
