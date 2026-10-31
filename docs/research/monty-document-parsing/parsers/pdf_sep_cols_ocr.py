info = doc_info()


def ocr_money(s):
    # OCR confuses ',' and '.', so: the last two digits are paise, every other separator is dropped
    m = re.fullmatch(r'[0-9.,]*[0-9][.,][0-9]{2}', s)
    if not m:
        return None
    return int(re.sub(r'[^0-9]', '', s[:-3]) or '0') * 100 + int(s[-2:])


accts = []
cur = None
title = 'account'
prev_text = ''
cols = None   # header-derived: narr_x0, ref_x0, debit_x1, credit_x1, balance_x1
for pg in range(info['pages']):
    for ln in lines_of(doc_words(pg)):
        t = line_text(ln)
        if t.startswith('Account No'):
            title = prev_text
        prev_text = t
        if t.startswith('Opening Balance') and ocr_money(ln[-1]['text']) is not None:
            if cur is not None:
                accts.append(cur)
            cur = {'name': title, 'opening': ocr_money(ln[-1]['text']), 'closing': None, 'rows': []}
        elif t.startswith('Closing Balance') and ocr_money(ln[-1]['text']) is not None:
            cur['closing'] = ocr_money(ln[-1]['text'])
        elif ln[0]['text'] == 'Date' and 'Debit' in t and 'Credit' in t:
            h = {w['text']: w for w in ln}
            cols = {'narr': h['Narration']['x0'], 'ref': h['Chq/Ref']['x0'],
                    'deb': h['Debit']['x1'], 'cre': h['Credit']['x1'], 'bal': h['Balance']['x1']}
        elif cols and cur is not None and to_iso(ln[0]['text']):
            amt = {'deb': None, 'cre': None, 'bal': None}
            desc_words = []
            ref_words = []
            for w in ln[1:]:
                m = ocr_money(w['text'])
                if m is not None and w['x0'] > cols['ref'] + 40:
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
if cur is not None:
    accts.append(cur)
for a in accts:
    a['check'] = check_statement(a['rows'], a['opening'], a['closing'])
{'accounts': accts}
