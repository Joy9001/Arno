text = '\n'.join([r[0] for r in doc_cells(0)])


def unesc(s):
    return s.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')


def bal(code):
    m = re.search(r'<Bal>(?:(?!</Bal>).)*?<Cd>' + code + r'</Cd>.*?<Amt Ccy="[A-Z]{3}">([\d.]+)</Amt><CdtDbtInd>(CRDT|DBIT)', text, re.S)
    if not m:
        return None
    v = money(m.group(1))
    return -v if m.group(2) == 'DBIT' else v


rows = []
for blk in re.findall(r'<Ntry>(.*?)</Ntry>', text, re.S):
    a = money(re.search(r'<Amt Ccy="[A-Z]{3}">([\d.]+)</Amt>', blk).group(1))
    dbit = re.search(r'<CdtDbtInd>(CRDT|DBIT)</CdtDbtInd>', blk).group(1) == 'DBIT'
    d = re.search(r'<BookgDt><Dt>(\d{4}-\d{2}-\d{2})', blk).group(1)
    info_m = re.search(r'<AddtlNtryInf>(.*?)</AddtlNtryInf>', blk, re.S)
    rows.append({'date': d, 'desc': unesc(info_m.group(1)) if info_m else '', 'debit': a if dbit else 0,
                 'credit': 0 if dbit else a, 'balance': None})
{'accounts': [{'name': 'camt053', 'opening': bal('OPBD'), 'closing': bal('CLBD'), 'rows': rows}]}
