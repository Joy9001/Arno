lines = [r[0] for r in doc_cells(0)]


def amt(s):
    return money(s.replace(',', '.'))


def bal_field(v):
    m = re.match(r'([CD])(\d{6})([A-Z]{3})([\d,]+)', v.strip())
    a = amt(m.group(4))
    return -a if m.group(1) == 'D' else a


accts = []
cur = None      # one account block per :20: message
row = None
for ln in lines:
    if ln.startswith(':20:'):
        cur = {'name': ln[4:], 'opening': None, 'closing': None, 'rows': []}
        accts.append(cur)
        row = None
    elif cur is None:
        continue
    elif ln.startswith(':60F:') or ln.startswith(':60M:'):
        if cur['opening'] is None:
            cur['opening'] = bal_field(ln[5:])
        row = None
    elif ln.startswith(':62F:') or ln.startswith(':62M:'):
        cur['closing'] = bal_field(ln[5:])     # the last 62 in the message wins (62M if the statement continues)
        row = None
    elif ln.startswith(':61:'):
        m = re.match(r':61:(\d{2})(\d{2})(\d{2})(\d{4})?\s*(R?[CD])[A-Z]?(\d+,\d*)', ln)
        a = amt(m.group(6))
        is_credit = m.group(5) in ('C', 'RD')
        row = {'date': '20' + m.group(1) + '-' + m.group(2) + '-' + m.group(3), 'desc': '', 'debit': 0 if is_credit else a,
               'credit': a if is_credit else 0, 'balance': None}
        cur['rows'].append(row)
    elif ln.startswith(':86:'):
        if row is not None:
            row['desc'] = ln[4:]
    elif row is not None and not ln.startswith(':') and ln != '-':
        row['desc'] = row['desc'] + ln          # field 86 (and 61 supplementary lines) continue on following lines
{'accounts': accts}
