"""Part B: language/stdlib probe. Each snippet runs in CPython (reference) and in Monty; results compared."""
import json
import sys
import traceback

from pydantic_monty import Monty

CASES = [
    # --- re
    ("re named groups", "import re\nm = re.search(r'(?P<d>\\d{2})/(?P<m>\\d{2})/(?P<y>\\d{4})', 'on 05/01/2026 ok')\nresult = m.groupdict()"),
    ("re lookbehind", "import re\nresult = re.findall(r'(?<=Rs\\.)\\d+(?:,\\d+)*', 'Rs.1,00,000 and Rs.250')"),
    ("re negative lookbehind", "import re\nresult = re.findall(r'(?<!\\d)\\d{3}(?!\\d)', 'a 123 b 4567 c 890')"),
    ("re MULTILINE ^$", "import re\nresult = re.findall(r'^\\d+ (\\w+)$', '1 abc\\n2 def\\nx 3 ghi', re.MULTILINE)"),
    ("re finditer + span", "import re\nresult = [(m.group(0), m.start(), m.end(), m.span()) for m in re.finditer(r'\\d+', 'a12b345')]"),
    ("re sub string", "import re\nresult = re.sub(r'\\s+', ' ', 'a   b \\n c')"),
    ("re sub backrefs \\1", "import re\nresult = re.sub(r'(\\d+)-(\\d+)', r'\\2/\\1', '12-34')"),
    ("re sub \\g<name>", "import re\nresult = re.sub(r'(?P<a>\\d+)-(?P<b>\\d+)', r'\\g<b>/\\g<a>', '12-34')"),
    ("re sub callable", "import re\nresult = re.sub(r'\\d+', lambda m: str(int(m.group(0)) * 2), 'a1 b22')"),
    ("re compile IGNORECASE", "import re\np = re.compile(r'debit', re.I)\nresult = bool(p.search('DEBIT'))"),
    ("re.VERBOSE", "import re\nresult = bool(re.search(r'''\\d+  # digits\n''', '12', re.VERBOSE))"),
    ("re split", "import re\nresult = re.split(r'[,;]\\s*', 'a, b;c')"),
    ("re fullmatch", "import re\nresult = bool(re.fullmatch(r'[\\d,]+\\.\\d{2}', '1,00,000.00'))"),
    ("re match indexing m[0]", "import re\nm = re.match(r'(a)(b)', 'ab')\nresult = (m[0], m[1])"),
    ("re m.groups()", "import re\nresult = re.match(r'(a)(b)?', 'a').groups()"),
    ("re m.lastindex", "import re\nresult = re.match(r'(a)(b)?', 'ab').lastindex"),
    ("re unicode \\w Devanagari", "import re\nresult = re.findall(r'\\w+', '\u0938\u094d\u0935\u093f\u0917\u0940 UPI')"),
    ("re backreference \\1", "import re\nresult = bool(re.search(r'(\\w)\\1', 'hello'))"),
    ("re lazy quantifier", "import re\nresult = re.search(r'<(.+?)>', '<a><b>').group(1)"),
    ("re subn", "import re\nresult = re.subn('a', 'b', 'aaa')"),
    ("re.escape", "import re\nresult = re.escape('a.b*c')"),
    # --- datetime
    ("date ctor+iso", "import datetime\nd = datetime.date(2026, 1, 5)\nresult = (d.isoformat(), d.year, d.month, d.day, d.weekday())"),
    ("date.fromisoformat", "import datetime\nresult = str(datetime.date.fromisoformat('2026-02-28'))"),
    ("timedelta add", "import datetime\nresult = str(datetime.date(2026, 1, 31) + datetime.timedelta(days=30))"),
    ("date diff .days", "import datetime\nresult = (datetime.date(2026, 3, 1) - datetime.date(2026, 2, 1)).days"),
    ("strftime %d %b %Y", "import datetime\nresult = datetime.date(2026, 1, 5).strftime('%d %b %Y')"),
    ("strftime %B %y %A", "import datetime\nresult = datetime.date(2026, 1, 5).strftime('%B %y %A')"),
    ("datetime.strptime %d/%m/%Y", "import datetime\nresult = str(datetime.datetime.strptime('05/01/2026', '%d/%m/%Y').date())"),
    ("datetime.strptime %d-%b-%Y", "import datetime\nresult = str(datetime.datetime.strptime('05-Jan-2026', '%d-%b-%Y').date())"),
    ("datetime.strptime %d %B %Y", "import datetime\nresult = str(datetime.datetime.strptime('5 January 2026', '%d %B %Y').date())"),
    ("datetime.strptime %d.%m.%y", "import datetime\nresult = str(datetime.datetime.strptime('05.01.26', '%d.%m.%y').date())"),
    ("datetime.strptime with time", "import datetime\nresult = str(datetime.datetime.strptime('2026-01-05 10:15:00', '%Y-%m-%d %H:%M:%S'))"),
    ("datetime.strptime %I:%M %p", "import datetime\nresult = str(datetime.datetime.strptime('05 Jan 2026 02:15 PM', '%d %b %Y %I:%M %p'))"),
    ("date.strptime (3.14)", "import datetime\nresult = str(datetime.date.strptime('05/01/2026', '%d/%m/%Y'))"),
    ("strptime bad day 31/02", "import datetime\ntry:\n    datetime.datetime.strptime('31/02/2026', '%d/%m/%Y')\n    result = 'accepted'\nexcept ValueError:\n    result = 'ValueError'"),
    ("date(2026,2,30) raises", "import datetime\ntry:\n    datetime.date(2026, 2, 30)\n    result = 'accepted'\nexcept ValueError:\n    result = 'ValueError'"),
    ("date.fromordinal", "import datetime\nresult = str(datetime.date.fromordinal(739000))"),
    ("excel serial -> date", "import datetime\nresult = str(datetime.date(1899, 12, 30) + datetime.timedelta(days=46027))"),
    ("sorted dates", "import datetime\nds = [datetime.date(2026, 2, 1), datetime.date(2026, 1, 5)]\nresult = [str(d) for d in sorted(ds)]"),
    ("datetime.now()/today()", "import datetime\nresult = type(datetime.date.today()).__name__"),
    # --- json / math
    ("json loads/dumps", "import json\nd = json.loads('{\"a\": [1, 2.5, null, true], \"b\": {\"c\": \"x\"}}')\nresult = json.dumps(d, sort_keys=True)"),
    ("json loads float amounts", "import json\nresult = json.loads('{\"amt\": 1234.56}')['amt'] * 100"),
    ("json dumps indent", "import json\nresult = json.dumps({'a': 1}, indent=2)"),
    ("json big", "import json\nresult = json.loads('[' + ','.join(str(i) for i in range(2000)) + ']')[1999]"),
    ("math floor/ceil/isclose/fsum", "import math\nresult = (math.floor(-1.5), math.ceil(1.2), math.isclose(0.1 + 0.2, 0.3), math.fsum([0.1] * 10))"),
    ("math round half-even", "result = (round(0.5), round(1.5), round(2.5), round(2.675, 2))"),
    # --- str
    ("str split/strip/replace", "result = ('  a,b , c '.strip().split(','), 'x-y'.replace('-', '+'), 'a b  c'.split(), 'a:b:c'.rsplit(':', 1))"),
    ("str isdigit/partition/startswith/zfill", "result = ('123'.isdigit(), 'a=b=c'.partition('='), 'a=b=c'.rpartition('='), 'UPI/x'.startswith(('NEFT', 'UPI')), '5'.zfill(3), '12,3'.isdigit())"),
    ("str misc", "result = ('abc'.upper(), 'ABC'.lower(), 'a b'.title(), 'abc'.find('c'), 'abcabc'.rfind('b'), 'aaa'.count('a'), 'a\\nb\\r\\nc'.splitlines(), 'x'.ljust(3, '.'), 'x'.rjust(3), 'x'.center(5, '*'))"),
    ("str maketrans/translate", "result = 'a,b'.translate(str.maketrans(',', ';'))"),
    ("str isdecimal/isalpha/isalnum/isspace", "result = ('12'.isdecimal(), 'ab'.isalpha(), 'a1'.isalnum(), ' '.isspace(), '\\u0967\\u0968'.isdigit())"),
    ("str unicode \u20b9 ops", "s = 'Rs\\u20b9 500'\nresult = (len(s), s.replace('\\u20b9', ''), s.encode('utf-8'), ord('\\u20b9'))"),
    ("str casefold/removeprefix", "result = ('ABC'.casefold(), 'prefix_x'.removeprefix('prefix_'), 'x.csv'.removesuffix('.csv'))"),
    ("unicodedata.normalize", "import unicodedata\nresult = unicodedata.normalize('NFKC', '\\uff11\\uff12')"),
    ("bytes decode cp1252", "result = b'caf\\xe9'.decode('cp1252')"),
    ("bytes decode utf-8", "result = b'caf\\xc3\\xa9'.decode('utf-8')"),
    # --- sorted / comp / etc
    ("sorted key kw", "result = sorted(['bb', 'a', 'ccc'], key=lambda s: -len(s))"),
    ("sorted positional key", "result = sorted(['bb', 'a'], lambda s: len(s))"),
    ("sorted reverse tuples", "result = sorted([(2, 'a'), (1, 'b'), (2, 'c')], key=lambda t: (t[0], t[1]), reverse=True)"),
    ("list.sort key", "x = [3, 1, 2]\nx.sort(key=lambda v: -v)\nresult = x"),
    ("min/max key", "result = (min(['bb', 'a'], key=len), max([1, 5, 3]))"),
    ("comprehensions", "result = ([x * 2 for x in range(3)], {k: v for k, v in zip('ab', [1, 2])}, {c for c in 'aab'} == {'a', 'b'}, sum(x for x in range(4)))"),
    ("nested comp + cond", "result = [[(i, j) for j in range(2) if j != i] for i in range(2)]"),
    ("walrus + match", "x = 5\nif (n := x * 2) > 5:\n    result = n\nmatch result:\n    case 10:\n        result = 'ten'\n    case _:\n        result = 'other'"),
    ("tuple unpack/starred", "a, *b, c = [1, 2, 3, 4]\nresult = (a, b, c)"),
    # --- classes
    ("simple class", "class P:\n    def __init__(self, a):\n        self.a = a\n    def double(self):\n        return self.a * 2\n    def __repr__(self):\n        return f'P({self.a})'\nresult = (P(2).double(), repr(P(3)))"),
    ("dataclass", "from dataclasses import dataclass\n@dataclass\nclass R:\n    d: str\n    amt: int = 0\nresult = (R('x').amt, R('x', 5) == R('x', 5))"),
    ("class inheritance", "class A:\n    pass\nclass B(A):\n    pass\nresult = isinstance(B(), A)"),
    ("class __lt__ in sorted", "class V:\n    def __init__(self, v):\n        self.v = v\n    def __lt__(self, o):\n        return self.v < o.v\nresult = [x.v for x in sorted([V(2), V(1)])]"),
    ("namedtuple", "from collections import namedtuple\nP = namedtuple('P', 'a b')\nresult = P(1, 2).b"),
    ("defaultdict/Counter", "from collections import defaultdict, Counter\nd = defaultdict(list)\nd['a'].append(1)\nresult = (dict(d), Counter('aab').most_common(1))"),
    ("itertools/functools", "import itertools, functools\nresult = (list(itertools.groupby([1, 1, 2]))[0][0], functools.reduce(lambda a, b: a + b, [1, 2, 3]), list(itertools.islice(itertools.count(), 3)))"),
    # --- errors
    ("try/except/else/finally", "r = []\ntry:\n    int('x')\nexcept ValueError as e:\n    r.append('VE')\nelse:\n    r.append('else')\nfinally:\n    r.append('fin')\nresult = r"),
    ("except multi + raise from", "try:\n    try:\n        1 / 0\n    except ZeroDivisionError as e:\n        raise ValueError('bad') from e\nexcept (TypeError, ValueError) as e:\n    result = str(e)"),
    ("custom exception class", "class MyErr(Exception):\n    pass\ntry:\n    raise MyErr('x')\nexcept MyErr:\n    result = 'caught'"),
    ("int('1,000') error", "try:\n    int('1,000')\nexcept ValueError as e:\n    result = type(e).__name__"),
    ("float('abc') error", "try:\n    float('abc')\nexcept ValueError:\n    result = 'VE'"),
    # --- f-strings / formatting
    ("f-string formats", "x = 1234567.891\nresult = (f'{x:,.2f}', f'{5:>6}|', f'{5:06d}', f'{0.256:.1%}', f'{255:x}', f'{x=}'[:6])"),
    ("str.format", "result = '{:>8}|{:<5}|{:,}'.format('a', 'b', 12345)"),
    ("lakh grouping fn", "def inr(p):\n    r, c = divmod(p, 100)\n    s = str(r)\n    if len(s) > 3:\n        h, t = s[:-3], s[-3:]\n        parts = []\n        while len(h) > 2:\n            parts.insert(0, h[-2:])\n            h = h[:-2]\n        if h:\n            parts.insert(0, h)\n        s = ','.join(parts) + ',' + t\n    return f'{s}.{c:02d}'\nresult = inr(12345678)"),
    # --- paise arithmetic / float pitfalls
    ("paise parse int(str)", "s = '1,00,000.00'\nw, _, f = s.replace(',', '').partition('.')\nresult = int(w) * 100 + int((f + '00')[:2])"),
    ("big ints", "result = (10 ** 30 + 1, (10 ** 30) // 7, -7 // 2, -7 % 3, divmod(-7, 2))"),
    ("float pitfalls", "result = (0.1 + 0.2, int(1.15 * 100), round(1.15 * 100), int(round(1234.565 * 100)), 2.675 * 100, 1234.56 * 100)"),
    ("float sum drift", "t = 0.0\nfor _ in range(10):\n    t += 0.1\nresult = (t, t == 1.0, round(t * 100))"),
    ("float to paise safe", "x = 1234.5600000000002\nresult = int(round(x * 100))"),
    # --- other stdlib
    ("decimal module", "from decimal import Decimal\nresult = str(Decimal('1234.56') * 100)"),
    ("csv module", "import csv\nresult = list(csv.reader(['a,\"b,c\",d']))"),
    ("hashlib sha256", "import hashlib\nresult = hashlib.sha256(b'abc').hexdigest()[:12]"),
    ("os.environ / getenv", "import os\nresult = os.getenv('HOME')"),
    ("open file", "result = open('/etc/hosts').read()[:10]"),
    ("import socket", "import socket"),
    ("import subprocess", "import subprocess"),
    ("import urllib.request", "import urllib.request"),
    ("import time; time.time()", "import time\nresult = type(time.time()).__name__"),
    ("recursion depth", "def f(n):\n    return 0 if n == 0 else 1 + f(n - 1)\nresult = f(500)"),
    ("global/nonlocal", "x = 1\ndef f():\n    global x\n    x += 1\nf()\nresult = x"),
    ("del statement", "x = [1, 2]\ndel x[0]\nresult = x"),
    ("generator function", "def g():\n    yield 1\nresult = list(g())"),
    ("generator expr", "result = list(i for i in range(3))"),
    ("lambda/closure", "def mk(n):\n    return lambda x: x + n\nresult = mk(2)(3)"),
    ("enumerate/zip/map/filter", "result = (list(enumerate('ab', 1)), list(zip([1, 2], 'ab')), list(map(str, [1, 2])), list(filter(None, [0, 1])))"),
    ("isinstance/type checks", "result = (isinstance(1, int), isinstance(1.0, (int, float)), type('x') is str, isinstance(True, int))"),
    ("assert", "assert 1 + 1 == 2\nresult = 'ok'"),
    ("str * and slicing", "s = 'abcdef'\nresult = (s[::-1], s[1:-1], s[-2:], 'ab' * 2)"),
    ("dict methods", "d = {'a': 1}\nd.setdefault('b', []).append(2)\nresult = (list(d.items()), d.get('z', 0), {**d, 'c': 3}.keys() == {'a', 'b', 'c'}, d.pop('a'))"),
]


def cpython_eval(code):
    g = {}
    try:
        exec(code, g)
        return ("ok", g.get("result"))
    except BaseException as e:
        return ("err", f"{type(e).__name__}: {e}"[:140])


def norm(v):
    if isinstance(v, (tuple, list)):
        return [norm(x) for x in v]
    if isinstance(v, dict):
        return {str(k): norm(x) for k, x in v.items()}
    if isinstance(v, (set, frozenset)):
        return sorted(norm(x) for x in v)
    if isinstance(v, float):
        return repr(v)
    if hasattr(v, "isoformat"):
        return str(v)
    return v


def main():
    out = []
    with Monty() as pool:
        for name, code in CASES:
            ref = cpython_eval(code)
            try:
                with pool.checkout() as s:
                    got = ("ok", s.feed_run(code + "\nresult"))
            except BaseException as e:
                got = ("err", f"{type(e).__name__}: {str(e).splitlines()[-1] if str(e) else ''}"[:160])
            if ref[0] == "ok" and got[0] == "ok":
                verdict = "SAME" if norm(ref[1]) == norm(got[1]) else "DIFFERENT"
            elif ref[0] == "err" and got[0] == "err":
                verdict = "SAME (both error)"
            elif ref[0] == "ok":
                verdict = "MONTY FAILS"
            else:
                verdict = "MONTY ACCEPTS (CPython errors)"
            out.append(dict(name=name, verdict=verdict, cpython=str(ref[1])[:100], monty=str(got[1])[:140]))
    json.dump(out, open(sys.argv[1] if len(sys.argv) > 1 else "b_results.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    from collections import Counter
    print(Counter(o["verdict"] for o in out))
    for o in out:
        if o["verdict"] != "SAME":
            print(f"[{o['verdict']}] {o['name']}\n    cpython: {o['cpython']}\n    monty:   {o['monty']}")


if __name__ == "__main__":
    main()
