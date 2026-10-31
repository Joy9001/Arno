info = doc_info()
rows = []
opening = None
closing = None
# First-draft style: column boundaries hard-coded from the one layout inspected (x in points)
for pg in range(info['pages']):
    for ln in lines_of(doc_words(pg)):
        t = line_text(ln)
        if t.startswith('Opening Balance') and money(ln[-1]['text']) is not None:
            opening = money(ln[-1]['text'])
        elif t.startswith('Closing Balance') and money(ln[-1]['text']) is not None:
            closing = money(ln[-1]['text'])
        elif to_iso(ln[0]['text']) and ln[0]['x0'] < 60:
            deb = money(join(ln, 340, 410)) if '.' in join(ln, 340, 410) else None
            cre = money(join(ln, 410, 480)) if '.' in join(ln, 410, 480) else None
            bal = money(join(ln, 480, 600))
            rows.append({'date': to_iso(ln[0]['text']), 'desc': join(ln, 80, 280), 'debit': deb or 0, 'credit': cre or 0, 'balance': bal})
{'accounts': [{'name': 'main', 'opening': opening, 'closing': closing, 'rows': rows, 'check': check_statement(rows, opening, closing)}]}
