info = doc_info()
accts = []
cur = None
title = 'account'
prev_text = ''
cols = None
for pg in range(info['pages']):
    for ln in lines_of(doc_words(pg)):
        t = line_text(ln)
        if t.startswith('Account No'):
            title = prev_text
        prev_text = t
        if t.startswith('Opening Balance') and money(ln[-1]['text']) is not None:
            if cur is not None:
                accts.append(cur)
            cur = {'name': title, 'opening': money(ln[-1]['text']), 'closing': None, 'rows': []}
        elif t.startswith('Closing Balance') and money(ln[-1]['text']) is not None:
            cur['closing'] = money(ln[-1]['text'])
        elif ln[0]['text'] == 'Date' and 'Amount' in t and 'Balance' in t:
            h = {w['text']: w for w in ln}
            cols = {'narr': h['Particulars']['x0'], 'ref': h['Ref']['x0'], 'amt': h['Amount']['x0']}
        elif cols and cur is not None and to_iso(ln[0]['text']):
            desc_words = []
            ref_words = []
            nums = []   # (paise, suffix)
            ws = ln[1:]
            i = 0
            while i < len(ws):
                w = ws[i]
                m = money(w['text'])
                if m is not None and '.' in w['text'] and w['x0'] > cols['amt'] - 60:
                    suffix = ''
                    if i + 1 < len(ws) and ws[i + 1]['text'] in ('Dr', 'Cr'):
                        suffix = ws[i + 1]['text']
                        i += 1
                    nums.append((m, suffix))
                elif w['x0'] >= cols['ref'] - 2 and w['text'].isdigit():
                    ref_words.append(w['text'])
                else:
                    desc_words.append(w['text'])
                i += 1
            amount, asuf = nums[0]
            bal, bsuf = nums[1]
            if bsuf == 'Dr':
                bal = -bal
            if asuf == 'Dr' or amount < 0:
                debit, credit = abs(amount), 0
            else:
                debit, credit = 0, abs(amount)
            cur['rows'].append({'date': to_iso(ln[0]['text']), 'desc': ' '.join(desc_words), 'ref': ' '.join(ref_words),
                                'debit': debit, 'credit': credit, 'balance': bal})
if cur is not None:
    accts.append(cur)
for a in accts:
    a['check'] = check_statement(a['rows'], a['opening'], a['closing'])
{'accounts': accts}
