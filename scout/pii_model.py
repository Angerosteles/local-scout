"""Bounded local PII candidates and hashed context scoring; no runtime ML dependency."""
from contextvars import ContextVar
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import re

KINDS = ('phone', 'card', 'ssn', 'address')
DIM = 2048
MASK_CANDIDATE_LIMIT = 512
_CANDIDATE_BUDGET = ContextVar('pii_candidate_budget', default=None)


class CandidateBudgetExceeded(ValueError):
    """The caller must withhold the complete unscanned result."""


MODEL_PATH = Path(__file__).with_name('models') / 'pii_context.json'
WORDS = re.compile(r'[a-zA-Z]{2,24}')
NUMERIC = re.compile(r'(?<![\w])(?:\+|\()?\d(?:[\d \t()./\-–]{0,36}\d)?(?:\s*(?:x|ext\.?|extension)\s*\d{1,6})?(?!\w)', re.I)
NINO = re.compile(r'(?i)(?<!\w)[A-CEGHJ-PR-TW-Z]{2}[ \t]?\d{2}[ \t]?\d{2}[ \t]?\d{2}[ \t]?[A-Z](?!\w)')
SSN_WORLD = re.compile(r'(?<!\w)\d{3}[ \t]\d{3}[ \t]\d{3}(?!\w)')
SSN = re.compile(r'(?<![\w\d])\d{3}[ \t.\-–]?\d{2}[ \t.\-–]?\d{4}(?![\w\d])')
IDENTIFIER = re.compile('(?:'+SSN.pattern+')|(?:'+SSN_WORLD.pattern+')|(?:'+NINO.pattern.removeprefix('(?i)')+')', re.I)
# Conventional USPS and Faker suffixes, bounded by token count and token length.
SUFFIXES = ('street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|court|ct|parkway|pkwy|highway|hwy|way|circle|cir|terrace|ter|place|pl|alley|aly|annex|anx|arcade|arc|bayou|byu|beach|bch|bend|bnd|bluff|bluffs|blf|bottom|btm|branch|br|bridge|brg|brook|brooks|brk|burg|burgs|bypass|byp|camp|canyon|cape|causeway|center|centers|centre|cliff|cliffs|club|common|commons|corner|corners|course|cove|coves|creek|crescent|crest|crossing|crossroad|curve|dale|dam|divide|estate|estates|expressway|extension|extensions|fall|falls|ferry|field|fields|flat|flats|ford|fords|forest|forge|forges|fork|forks|fort|freeway|garden|gardens|gateway|glen|glens|green|greens|grove|groves|harbor|harbors|haven|heights|hill|hills|hollow|inlet|island|islands|isle|junction|junctions|key|keys|knoll|knolls|lake|lakes|land|landing|light|lights|loaf|lock|locks|lodge|loop|mall|manor|manors|meadow|meadows|mews|mill|mills|mission|motorway|mount|mountain|mountains|neck|orchard|oval|overpass|park|parks|pass|passage|path|pike|pine|pines|plain|plains|plaza|point|points|port|ports|prairie|radial|ramp|ranch|rapid|rapids|rest|ridge|ridges|rise|river|roads|route|row|rue|run|shoal|shoals|shore|shores|skyway|spring|springs|spur|spurs|square|squares|station|stravenue|stream|streets|summit|trace|track|trafficway|trail|trailer|tunnel|turnpike|underpass|union|unions|valley|valleys|viaduct|view|views|village|villages|ville|vista|walk|walks|wall|well|wells|block')
ADDRESS = re.compile(r'(?i)(?<!\w)\d{1,6}(?:/\d{1,4})?[ \t]+(?:[A-Z0-9][A-Z0-9.\'’&-]{0,24}[ \t]+){1,5}(?:' + SUFFIXES + r')\b\.?(?:[ \t]+(?:apt\.?|apartment|suite|ste\.?|unit|#)[ \t]*[A-Z0-9-]+)?(?:,?[ \t]+[A-Z][A-Za-z.\'’ -]{1,32},?[ \t]+[A-Z]{2}[ \t]+\d{5}(?:-\d{4})?)?')
LABEL_ADDRESS = re.compile(r'(?i)\b(?:address|ship(?:ping)?[ \t]+to|residence|located[ \t]+at)\s*[:#=]?\s*(?P<value>\d{1,6}[ \t]+(?:[A-Z][A-Za-z0-9.\'’&/-]{0,24}[ \t]+){1,6}[A-Z][A-Za-z0-9.\'’&/-]{0,24})')
WIDE_ADDRESS = re.compile(r"(?i)(?<!\w)(?:H\.No\.[ \t]*|Flat[ \t]+)?\d{1,6}(?:/\d{1,4})?,?[ \t]+[A-Z][A-Za-z.'’&-]{1,24}(?:[ \t]+[A-Z][A-Za-z.'’&-]{1,24}){1,4}(?:,[ \t]*[A-Z][A-Za-z.'’&-]{1,24}(?:[ \t]+[A-Z][A-Za-z.'’&-]{1,24}){0,3})?")
POLISH = re.compile(r'(?i)\bul\.?\s+[\wąćęłńóśźż -]{1,60}\s+\d+[A-Za-z]?(?:/\d+)?\s*,\s*\d{2}-\d{3}\s+[\wąćęłńóśźż -]{1,40}')
KEYS = {
 'phone': r'\b(?:phone|telephone|tel|mobile|call|contact|fax|telefon|cell|reach|dial)\b',
 'card': r'\b(?:card|credit|debit|visa|mastercard|amex|payment|cc|pan)\b',
 'ssn': r'\b(?:ssn|social|security|taxpayer|ssns)\b',
 'address': r'\b(?:address|street|shipping|ship|residence|located|live|lives|mailing|delivery|home)\b',
}
KEY_PATTERNS = {k: re.compile(v, re.I) for k,v in KEYS.items()}
NEGATIVE = re.compile(r'\b(?:order|invoice|reference|ticket|tracking|version|build|timestamp|serial|product|isbn|count|size|seed|checksum|example|sample|dummy|fake|placeholder|test|account|routing|zip|postal)\b', re.I)


def digits(value):
    return ''.join(ch for ch in value if ch in '0123456789')


def luhn(value):
    ds = [int(c) for c in digits(value)]
    return 13 <= len(ds) <= 19 and sum((2*d-9 if d>4 else 2*d) if (len(ds)-i)%2 == 0 else d for i,d in enumerate(ds)) % 10 == 0


def ssn_valid(value):
    d = digits(value)
    return len(d) == 9 and d[:3] not in ('000', '666') and d[0] != '9' and d[3:5] != '00' and d[5:] != '0000'


def candidates(text):
    """Yield bounded spans; validators become features, then safety gates apply."""
    if not re.search(r'\d',text): return []
    found = set()
    budget = _CANDIDATE_BUDGET.get()
    for m in NUMERIC.finditer(text):
        a,b = m.span(); value = m.group(); d = digits(value)
        n = len(d)
        if 'x' in value.lower():
            base = re.split(r'\s*(?:x|ext\.?|extension)\s*', value, flags=re.I)[0]
            n = len(digits(base))
        if 7 <= n <= 15: found.add((a,b,'phone'))
        if 12 <= n <= 19: found.add((a,b,'card'))
        if budget is not None and len(found)>budget: raise CandidateBudgetExceeded('PII candidate budget exceeded')
    for m in IDENTIFIER.finditer(text):
        found.add((*m.span(), 'ssn'))
        if budget is not None and len(found)>budget: raise CandidateBudgetExceeded('PII candidate budget exceeded')
    for pat in (ADDRESS, LABEL_ADDRESS, WIDE_ADDRESS, POLISH):
        for m in pat.finditer(text):
            found.add((*(m.span('value') if pat is LABEL_ADDRESS else m.span()), 'address'))
            if budget is not None and len(found)>budget: raise CandidateBudgetExceeded('PII candidate budget exceeded')
    # Keep the widest nested candidate of each type. This avoids classifying
    # an address repeatedly and counting its components as separate identities.
    kept=[]
    for kind in KINDS:
        end=-1
        for a,b,k in sorted((c for c in found if c[2]==kind),key=lambda c:(c[0],-c[1])):
            if b>end:
                kept.append((a,b,k));end=b
    international=[(a,b) for a,b,k in kept if k=='phone' and text[a:b].startswith('+')]
    from bisect import bisect_right
    starts=[a for a,b in international]
    result=[]
    for a,b,k in kept:
        index=bisect_right(starts,a)-1
        if k=='ssn' and index>=0 and international[index][0]<a and b<=international[index][1]:continue
        result.append((a,b,k))
    return sorted(result)


def features(text, a, b, kind):
    value = text[a:b]; d = digits(value)
    before = text[max(0,a-80):a]; after = text[b:b+60]
    ctx = before+' '+after
    f = {'bias':1., 'length':min(len(value),160)/80., 'digits':len(d)/20.,
         'keyword':float(bool(KEY_PATTERNS[kind].search(ctx))),
         'keyword_before':float(bool(KEY_PATTERNS[kind].search(before[-40:]))),
         'negative':float(bool(NEGATIVE.search(ctx))),
         'plus':float(value.startswith('+')), 'paren':float('(' in value),
         'hyphen':float('-' in value), 'space':float(' ' in value),
         'dot':float('.' in value), 'comma':float(',' in value),
         'luhn':float(luhn(value)), 'ssn_valid':float(ssn_valid(value)),
         'nino':float(bool(NINO.fullmatch(value))),
         'world_ssn':float(bool(SSN_WORLD.fullmatch(value))),
         'strict_address':float(bool(ADDRESS.fullmatch(value) or POLISH.fullmatch(value))),
         'strong_card':float(bool(re.search(r'(?i)credit[ -]?card|debit[ -]?card|card[ _]?(?:number|no)|payment|visa|mastercard|amex',ctx))),
         'nanp':float(len(d) in (10,11) and (len(d)==10 or d[0]=='1') and d[-10] in '23456789' and d[-7] in '23456789'),
         'zip':float(bool(re.search(r'\b\d{5}(?:-\d{4})?\b', value))),
         'standalone':float(not (before.strip() or after.strip())),
         'same_digits':float(bool(d) and len(set(d))==1)}
    for n in (7,8,9,10,11,12,13,14,15,16,17,18,19): f['n'+str(n)] = float(len(d)==n)
    for side, window in (('b',before),('a',after)):
        for word in WORDS.findall(window.lower()): f[side+':'+word] = 1.
    # Candidate words are useful for street suffixes; numeric tokens are excluded.
    if kind == 'address':
        for word in WORDS.findall(value.lower()): f['v:'+word] = 1.
    out = {}
    for name, weight in f.items():
        if weight:
            index = int.from_bytes(hashlib.blake2s(name.encode(), digest_size=4).digest(), 'little') % DIM
            out[index] = out.get(index,0.)+weight
    return out


@lru_cache(maxsize=1)
def model():
    if MODEL_PATH.stat().st_size>1000000: raise ValueError('Oversize PII context artifact')
    data = json.loads(MODEL_PATH.read_text(encoding='utf-8'))
    if data['dimension'] != DIM or data['schema'] != 1 or set(data['models']) != set(KINDS):
        raise ValueError('Invalid PII context artifact')
    for item in data['models'].values():
        weights=item['weights']
        if not isinstance(weights,list) or len(weights)!=DIM:
            raise ValueError('Invalid PII coefficient count')
        numbers=weights+[item['intercept'],item['threshold']]
        if any(type(v) not in (float,int) or not math.isfinite(v) for v in numbers) or not 0<=item['threshold']<=1:
            raise ValueError('Invalid PII coefficients')
    return data


def probability(text, a, b, kind):
    item = model()['models'][kind]
    score = item['intercept'] + sum(item['weights'][i]*v for i,v in features(text,a,b,kind).items())
    return 1./(1.+math.exp(-max(-40.,min(40.,score))))


def eligible(text, a, b, kind):
    value = text[a:b]
    if kind == 'ssn':
        return ssn_valid(value) or bool(NINO.fullmatch(value) or SSN_WORLD.fullmatch(value))
    if kind == 'card' and not luhn(value):
        ctx = text[max(0,a-80):a]+' '+text[b:b+60]
        return bool(re.search(r'(?i)credit[ -]?card|debit[ -]?card|card[ _]?(?:number|no)|payment|visa|mastercard|amex',ctx))
    return True


DIRECT_PHONE = re.compile(r"""(?i)\b(?:tel(?:ephone)?|telefon|mobile|phone(?:[ _]number)?|call|contact|fax)\s*["']?\s*[:=#]?\s*["']?\s*$""")
DIRECT_ADDRESS = re.compile(r'(?i)(?:address|ship(?:ping)? to|located at|residence)\s*[:#=]?\s*$')
DIRECT_SSN = re.compile(r'(?i)\b(?:ssn|social security(?: number)?)\s*[:=#]?\s*$')
DATE_IP = re.compile(r'(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{4}|(?:\d{1,3}\.){3}\d{1,3})')
NANP_FORMAT = re.compile(r'\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}')
POLISH_FORMAT = re.compile(r'\d{3}-\d{3}-\d{3}')
ZIP = re.compile(r'\b\d{5}(?:-\d{4})?\b')


def accepted(text, a, b, kind):
    value = text[a:b]
    if not eligible(text,a,b,kind): return False
    before = text[max(0,a-60):a]
    if kind == 'address':
        if POLISH.fullmatch(value) or ADDRESS.fullmatch(value) and (DIRECT_ADDRESS.search(before[-40:]) or ZIP.search(value)):
            return True
    elif kind == 'phone':
        if SSN.fullmatch(value) and ssn_valid(value) and DIRECT_SSN.search(before[-45:]): return False
        if DATE_IP.fullmatch(value): return False
        d = digits(value)
        if DIRECT_PHONE.search(before[-40:]) and 7<=len(d)<=15: return True
        standalone = not before.strip() and not text[b:b+60].strip() and not text[:a].strip() and not text[b:].strip()
        nanp = len(d)==10 and d[0] in '23456789' and d[3] in '23456789'
        if (standalone and value.startswith('+') and 7<=len(d)<=15 or
            nanp and (standalone or NANP_FORMAT.fullmatch(value)) or
            standalone and POLISH_FORMAT.fullmatch(value)):
            if not NEGATIVE.search(before+' '+text[b:b+40]): return True
    elif kind == 'ssn':
        if ssn_valid(value) and DIRECT_SSN.search(before[-45:]): return True
        ctx=before+' '+text[b:b+40]
        if NEGATIVE.search(ctx) and not KEY_PATTERNS[kind].search(ctx): return False
    return probability(text,a,b,kind) >= model()['models'][kind]['threshold']


def spans(text):
    return [(a,b,k) for a,b,k in candidates(text) if accepted(text,a,b,k)]
