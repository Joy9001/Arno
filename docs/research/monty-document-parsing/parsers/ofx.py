text = '\n'.join([r[0] for r in doc_cells(0)])


def tag(block, name):
    m = re.search('<' + name + r'>([^<\r\n]*)', block)
    if m:
        return m.group(1).strip().replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    return None


accts = []
for stmt in re.findall(r'<STMTRS>(.*?)</STMTRS>', text, re.S):
    rows = []
    for blk in re.findall(r'<STMTTRN>(.*?)</STMTTRN>', stmt, re.S):
        amt = money(tag(blk, 'TRNAMT'))
        d = tag(blk, 'DTPOSTED')[:8]
        rows.append({'date': d[:4] + '-' + d[4:6] + '-' + d[6:8], 'desc': tag(blk, 'MEMO') or tag(blk, 'NAME'),
                     'debit': -amt if amt < 0 else 0, 'credit': amt if amt > 0 else 0, 'balance': None})
    lb = re.search(r'<LEDGERBAL>.*?<BALAMT>([^<\r\n]*)', stmt, re.S)
    closing = money(lb.group(1)) if lb else None
    # OFX carries no opening balance and no running balances: the ledger must supply the opening
    accts.append({'name': tag(stmt, 'ACCTID'), 'opening': None, 'closing': closing, 'rows': rows})
{'accounts': accts}
