"""Bounded local PII candidates and hashed context scoring; no runtime ML dependency."""
from contextvars import ContextVar
from bisect import bisect_right
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import re
from . import language

KINDS = ('phone', 'card', 'ssn', 'address')
DIM = 2048
MASK_CANDIDATE_LIMIT = 512
_CANDIDATE_BUDGET = ContextVar('pii_candidate_budget', default=None)


class CandidateBudgetExceeded(ValueError):
    """The caller must withhold the complete unscanned result."""


MODEL_PATH = Path(__file__).with_name('models') / 'pii_context.json'
WORDS = re.compile(r'[a-zA-Z]{2,24}')
_PHONE_EXTENSION = r'[ \t]{0,8}(?:extension|ext\.?|wew\.?|x)[ \t]{0,8}[0-9]{1,6}'
PHONE_EXTENSION = re.compile(_PHONE_EXTENSION + r'$', re.I)
NUMERIC = re.compile(r'(?<![\w])(?:\+|\()?\d(?:[\d \t()./\-–]{0,58}\d)?(?:' + _PHONE_EXTENSION + r')?(?!\w)', re.I)
# ITU-T E.164 country/territory calling codes; shared numbering plans use one code.
COUNTRY_CALLING_CODES = frozenset('''
1 7 20 27 30 31 32 33 34 36 39 40 41 43 44 45 46 47 48 49
51 52 53 54 55 56 57 58 60 61 62 63 64 65 66 81 82 84 86 90 91 92 93 94 95 98
211 212 213 216 218 220 221 222 223 224 225 226 227 228 229 230 231 232 233 234
235 236 237 238 239 240 241 242 243 244 245 246 247 248 249 250 251 252 253 254
255 256 257 258 260 261 262 263 264 265 266 267 268 269 290 291 297 298 299
350 351 352 353 354 355 356 357 358 359 370 371 372 373 374 375 376 377 378
380 381 382 383 385 386 387 389 420 421 423
500 501 502 503 504 505 506 507 508 509 590 591 592 593 594 595 596 597 598 599
670 672 673 674 675 676 677 678 679 680 681 682 683 685 686 687 688 689 690 691 692
850 852 853 855 856 880 886 960 961 962 963 964 965 966 967 968 970 971 972 973
974 975 976 977 992 993 994 995 996 998
'''.split())
INTERNATIONAL_PHONE = re.compile(r'\+[0-9](?:[ \t().-]{0,3}[0-9]){6,14}')


def international_phone(value):
    if not INTERNATIONAL_PHONE.fullmatch(value):
        return False
    number = digits(value)
    return any(number[:n] in COUNTRY_CALLING_CODES and 6 <= len(number) - n <= 12
               for n in (1, 2, 3))


_BOX_WORD = r"(?:[A-Z][a-z'’\-]{1,24}|[A-Z]{2,24})"
_BOX_CITY = _BOX_WORD + r'(?:[ \t]{1,3}' + _BOX_WORD + r'){0,3}'
_BOX_POST = r'(?:[A-Z]{1,2}[0-9][A-Z0-9]?[ \t]?[0-9][A-Z]{2}|[0-9]{5}(?:-[0-9]{4})?)(?![\w-])'
_BOX_LOCALITY = _BOX_CITY + r'(?:,?[ \t]{1,4}[A-Z]{2})?'
_BOX_COUNTRY = r'(?:,[ \t]{0,4}(?:UK|USA|US|United Kingdom|United States))?'
_BOX_POST_GAP = r'(?:,?[ \t]{1,4}|,?[ \t]{0,4}\r?\n[ \t]{0,4})'
PO_BOX = re.compile(r'(?<!\w)(?i:P\.?[ \t]?O\.?[ \t]+Box|Post[ \t]+Office[ \t]+Box)[ \t]{1,4}[0-9]{1,8}(?!\w)'
                    r'(?:(?:[ \t]{0,4}[,/][ \t]{0,4}|[ \t]{1,4})' + _BOX_LOCALITY
                    + r'(?:' + _BOX_POST_GAP + _BOX_POST + r')?' + _BOX_COUNTRY
                    + r'|[ \t]{0,4}\r?\n[ \t]{0,4}' + _BOX_LOCALITY
                    + r'(?:' + _BOX_POST_GAP + _BOX_POST + _BOX_COUNTRY
                    + r'|(?=[ \t]{0,4}(?:\r?\n|$|[;.]))))?')
NINO = re.compile(r'(?i)(?<!\w)[A-CEGHJ-PR-TW-Z]{2}[ \t]?\d{2}[ \t]?\d{2}[ \t]?\d{2}[ \t]?[A-Z](?!\w)')
SSN_WORLD = re.compile(r'(?<!\w)\d{3}[ \t]\d{3}[ \t]\d{3}(?!\w)')
SSN = re.compile(r'(?<![\w\d])\d{3}[ \t.\-–]?\d{2}[ \t.\-–]?\d{4}(?![\w\d])')
IDENTIFIER = re.compile('(?:'+SSN.pattern+')|(?:'+SSN_WORLD.pattern+')|(?:'+NINO.pattern.removeprefix('(?i)')+')', re.I)
# Conventional USPS and Faker suffixes, bounded by token count and token length.
SUFFIXES = ('street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|court|ct|parkway|pkwy|highway|hwy|way|circle|cir|terrace|ter|place|pl|alley|aly|annex|anx|arcade|arc|bayou|byu|beach|bch|bend|bnd|bluff|bluffs|blf|bottom|btm|branch|br|bridge|brg|brook|brooks|brk|burg|burgs|bypass|byp|camp|canyon|cape|causeway|center|centers|centre|cliff|cliffs|club|common|commons|corner|corners|course|cove|coves|creek|crescent|crest|crossing|crossroad|curve|dale|dam|divide|estate|estates|expressway|extension|extensions|fall|falls|ferry|field|fields|flat|flats|ford|fords|forest|forge|forges|fork|forks|fort|freeway|garden|gardens|gateway|glen|glens|green|greens|grove|groves|harbor|harbors|haven|heights|hill|hills|hollow|inlet|island|islands|isle|junction|junctions|key|keys|knoll|knolls|lake|lakes|land|landing|light|lights|loaf|lock|locks|lodge|loop|mall|manor|manors|meadow|meadows|mews|mill|mills|mission|motorway|mount|mountain|mountains|neck|orchard|oval|overpass|park|parks|pass|passage|path|pike|pine|pines|plain|plains|plaza|point|points|port|ports|prairie|radial|ramp|ranch|rapid|rapids|rest|ridge|ridges|rise|river|roads|route|parade|quadrant|quay|row|rue|run|shoal|shoals|shore|shores|skyway|spring|springs|spur|spurs|square|squares|station|stravenue|stream|streets|summit|trace|track|trafficway|trail|trailer|tunnel|turnpike|underpass|union|unions|valley|valleys|viaduct|view|views|village|villages|ville|vista|walk|walks|wall|well|wells|block')
ADDRESS = re.compile(r'(?i)(?<![\w.])\d{1,6}[A-Z]?(?:/\d{1,4})?[ \t]+(?:\d{1,4}[ \t]+|(?:(?!\d+[ \t])[A-Z0-9][A-Z0-9.\'’&-]{0,24}[ \t]+){1,5}?)(?:' + SUFFIXES + r')\b\.?(?:[ \t]+(?:NW|NE|SW|SE)\b)?(?:[ \t]+(?:apt\.?|apartment|suite|ste\.?|unit|#)[ \t]*[A-Z0-9-]+)?(?:,?[ \t]+[A-Z][A-Za-z.\'’ -]{1,32},?[ \t]+(?-i:[A-Z]{2})[ \t]+\d{5}(?:-\d{4})?)?')
_ADDRESS_LABEL = r'(?:address(?:/billing)?|ship(?:ping)?[ \t]+to|delivered[ \t]+to|residence|located[ \t]+at)'
_ADDRESS_ABBREVIATIONS = frozenset('h no apt st ave blvd rd dr mt ste fl rm n s e w ne nw se sw mr mrs ms gen col ft pt ct ln pl sq ter cir bldg dept prof rev jr sr lt capt pkwy hwy'.split())
LABEL_ADDRESS = re.compile(r'(?i)\b' + _ADDRESS_LABEL + r'\s*[:#=-]?\s*(?P<value>\d{1,6},?[ \t]+(?:(?-i:[A-ZĄĆĘŁŃÓŚŹŻ0-9])[A-Za-ząćęłńóśźż0-9.\'’&/-]{0,24}[ \t]+){1,6}(?-i:[A-ZĄĆĘŁŃÓŚŹŻ0-9])[A-Za-ząćęłńóśźż0-9.\'’&/-]{0,24})')
WIDE_ADDRESS = re.compile(r"(?i)(?<!\w)(?:H\.No\.[ \t]*|Flat[ \t]+)?\d{1,6}(?:/\d{1,4})?,?[ \t]+[A-ZĄĆĘŁŃÓŚŹŻ][A-Za-ząćęłńóśźż.'’&-]{0,24}(?:[ \t]+[A-ZĄĆĘŁŃÓŚŹŻ][A-Za-ząćęłńóśźż.'’&-]{0,24}){1,4}(?:,[ \t]*[A-ZĄĆĘŁŃÓŚŹŻ][A-Za-ząćęłńóśźż.'’&-]{0,24}(?:[ \t]+[A-ZĄĆĘŁŃÓŚŹŻ][A-Za-ząćęłńóśźż.'’&-]{0,24}){0,3})?")
_PL_WORD = r'[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+(?:-[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)?'
_PL_CITY = _PL_WORD + r'(?:[ \t]+' + _PL_WORD + r'){0,3}'
_PL_STREET = r'(?:' + _PL_WORD + r'|\d{1,2}|[IVX]{1,5})(?:[ \t]+(?:' + _PL_WORD + r'|[IVX]{1,5})){0,5}'
_PL_NUMBER = r'\d{1,5}[A-Za-z]?(?:/\d{1,4})?(?:[ \t]+(?i:m\.?|lok\.?|mieszkanie|lokal)[ \t]*\d{1,4}[A-Za-z]?)?'
_PL_POST = r',?[ \t]*(?:\r?\n[ \t]*)?\d{2}-\d{3}[ \t]+' + _PL_CITY
POLISH = re.compile(r'\b(?i:ul\.?|ulica|al\.?|aleja|aleje|pl\.?|plac|os\.?|osiedle)[ \t]+' + _PL_STREET + r'[ \t]+' + _PL_NUMBER + r'(?:' + _PL_POST + r')?')
POLISH_FULL = re.compile(r'\b' + _PL_STREET + r'[ \t]+' + _PL_NUMBER + _PL_POST)
POLISH_PREP = re.compile(r'\b(?i:na|przy)[ \t]+(?P<value>[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]*ej[ \t]+' + _PL_NUMBER + r'(?:' + _PL_POST + r')?)')
POLISH_PLACE = re.compile(r'\b(?i:adres(?:[ \t]+(?:dostawy|zamieszkania))?|wieś|miejscowość)[ \t]*[:=]?[ \t]+(?P<value>' + _PL_CITY + r'[ \t]+' + _PL_NUMBER + r'(?:' + _PL_POST + r')?)')
POLISH_POST = re.compile(r'(?<![\w-])\d{2}-\d{3}[ \t]+' + _PL_CITY)
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


def candidates(text, lang='both'):
    """Yield bounded spans; validators become features, then safety gates apply."""
    if not re.search(r'\d',text): return []
    found = set()
    budget = _CANDIDATE_BUDGET.get()
    for m in NUMERIC.finditer(text):
        a,b = m.span(); value = PHONE_EXTENSION.sub('', m.group()); d = digits(value)
        n = len(d)
        if 7 <= n <= 15: found.add((a,b,'phone'))
        if 12 <= n <= 19: found.add((a,b,'card'))
        if budget is not None and len(found)>budget: raise CandidateBudgetExceeded('PII candidate budget exceeded')
    for m in IDENTIFIER.finditer(text):
        found.add((*m.span(), 'ssn'))
        if budget is not None and len(found)>budget: raise CandidateBudgetExceeded('PII candidate budget exceeded')
    # Explicit street syntax remains protected inside a paragraph in another language.
    patterns = (PO_BOX, ADDRESS, POLISH, POLISH_FULL, POLISH_PREP, POLISH_PLACE, POLISH_POST, LABEL_ADDRESS, WIDE_ADDRESS)
    for pat in patterns:
        for m in pat.finditer(text):
            a, b = m.span('value') if 'value' in pat.groupindex else m.span()
            if pat is WIDE_ADDRESS or pat is LABEL_ADDRESS:
                if b < len(text) and text[b].isalnum():
                    continue
                # Classify fallback spans before pruning: rejected prose must not hide a street.
                labelled = DIRECT_ADDRESS.search(text[max(0,a-60):a])
                if not labelled and a and text[a-1] in '.-/:':
                    continue
                for boundary in re.finditer(r'\.[ \t]+[A-Z]', text[a:b]):
                    word = re.search(r'[A-Za-z]+$', text[a:a+boundary.start()])
                    if not word or word[0].lower() not in _ADDRESS_ABBREVIATIONS:
                        if labelled and re.fullmatch(r'[ \t]+[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+', text[a+boundary.start()+1:b]) and (b == len(text) or text[b] not in ':='):
                            continue
                        b = a + boundary.start()
                        break
                value = text[a:b]
                titled = re.match(r'(?:H\.No\.[ \t]*|Flat[ \t]+)?\d{1,6}(?:/\d{1,4})?,?[ \t]+[A-ZĄĆĘŁŃÓŚŹŻ]', value)
                if labelled and pat is WIDE_ADDRESS and not titled:
                    continue
                if not labelled and (not titled or re.match(r'(?:19|20)\d{2},', value)):
                    continue
                street = ADDRESS.match(value)
                if street and (not labelled or re.match(r'[ \t]+(?:in|and|to|for|please|today|will|is|has)\b', value[street.end():])):
                    continue
                if not labelled and not accepted(text,a,b,'address',lang):
                    continue
            found.add((a,b,'address'))
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
    if kind == 'card' and re.search(r'(?i)\bContent-(?:Length|Range|Size)\s*:\s*$', text[max(0,a-40):a]):
        return False
    if kind == 'ssn':
        return ssn_valid(value) or bool(NINO.fullmatch(value) or SSN_WORLD.fullmatch(value))
    if kind == 'card' and not luhn(value):
        ctx = text[max(0,a-80):a]+' '+text[b:b+60]
        return bool(re.search(r'(?i)credit[ -]?card|debit[ -]?card|card[ _]?(?:number|no)|payment|visa|mastercard|amex',ctx))
    return True


DIRECT_PHONE = re.compile(r"""(?i)\b(?:tel(?:ephone)?\.?|telefon(?:[ \t]+kontaktowy)?|(?:numer|nr)[ \t]+telefonu|mobile|phone(?:[ _]number)?|call(?:[ \t]+(?:me|us))?(?:[ \t]+at)?|customer[ \t]+service[ \t]+at|contact|kontakt|fax|(?:my|moj|mój)[ \t]+(?:number|numer)|(?:ping|reach|ring)(?:[ \t]+me)?[ \t]+(?:at|on)|zadzw[oó]ń(?:[ \t]+(?:pod|na(?:[ \t]+numer)?))?)(?:[ \t]+(?:ws\.|w[ \t]+sprawie|do|for|regarding|about)[ \t]+(?:zamówienia|faktury|przesyłki|order|invoice|tracking))?(?:[ \t]+(?:is|to))?\s*["']?\s*[:=#-]?\s*["']?\s*$""")
DIRECT_ADDRESS = re.compile(r'''(?i)\b''' + _ADDRESS_LABEL + r'''["'*]*\s*[:#=-]?\s*["'*]*\s*$''')
DIRECT_SSN = re.compile(r'''(?i)\b(?:ssns?(?:/pesel)?(?:[ \t]+(?:number|no\.?))?|pesel|social security(?:[ \t]+(?:number|no\.?))?|taxpayer id(?:[ \t]*\(tin\))?|national insurance number|nino)["'*]*\s*(?:is\b\s*)?[#]?\s*[:=#-]?\s*["'*]*\s*$''')
DATE_IP = re.compile(r'(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{4}|(?:\d{1,3}\.){3}\d{1,3})')
NANP_FORMAT = re.compile(r'\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}')
POLISH_FORMAT = re.compile(r'(?:\d{3}-\d{3}-\d{3}|[2-8]\d{2} \d{3} \d{3})')
POLISH_MONEY = re.compile(r'\s*(?:[,.]\d{1,2}\s*)?(?:zł(?:otych|ote|oty)?|PLN|USD|EUR|GBP)\b', re.I)
PHONE_ID = re.compile(r'(?i)\b(?:nip|regon|pesel|zamówieni[ae]|faktura|faktury|przesyłki|order|invoice|tracking|kwota|kwoty|koszt|budżet)(?:[ \t]+(?:nr|numer|number))?[ \t]*[:=#]?[ \t]*$')
NINO_ID = re.compile(r'(?i)\b(?:order|invoice|reference|ticket|tracking|serial|product)(?:[ \t]+(?:id|nr|no|number))?[ \t]*[:=#-]?[ \t]*$')
POLISH_PHONE = re.compile(r'(?:\+48[ \t]*|0048[ \t]*)?(?:[4-8]\d{2}[ \t-]*\d{3}[ \t-]*\d{3}|(?:\(\d{2}\)|\d{2})[ \t-]+\d{3}[ \t-]+\d{2}[ \t-]+\d{2})')
ZIP = re.compile(r'\b\d{5}(?:-\d{4})?\b')


def accepted(text, a, b, kind, lang='both'):
    value = PHONE_EXTENSION.sub('', text[a:b]) if kind == 'phone' else text[a:b]
    if not eligible(text,a,b,kind): return False
    before = text[max(0,a-60):a]
    direct_phone = bool(DIRECT_PHONE.search(before))
    international_prefix = value.startswith('+') or value.startswith('00') and len(digits(value)) >= 9
    polish_prefix = value.startswith(('+48', '0048'))
    direct_ssn = bool(DIRECT_SSN.search(before))
    nino = bool(NINO.fullmatch(value))
    explicit_ssn = direct_ssn or nino and not NINO_ID.search(before)
    if kind in ('phone', 'ssn') and POLISH_MONEY.match(text[b:b+24]) and re.fullmatch(r'\d{1,3}(?:[ \t]\d{3})+', value) and not (direct_phone or international_prefix or explicit_ssn):
        return False
    if kind == 'address':
        if PO_BOX.fullmatch(value):
            return True
        if DIRECT_ADDRESS.search(before):
            return True
        if re.fullmatch(r'(?i)\d{3} (?:Bad Gateway|Not Found|Service Unavailable|Internal Server Error)', value):
            return False
        if POLISH.fullmatch(value) or POLISH_FULL.fullmatch(value) or POLISH_POST.fullmatch(value) or POLISH_PREP.search(text[max(0,a-8):b]) or POLISH_PLACE.search(text[max(0,a-30):b]):
            return True
        if ADDRESS.fullmatch(value):
            street = re.match(r'\d+[A-Za-z]?(?:/\d+)?\s+(\S+)', value)
            if street and re.fullmatch(r'\d+(?:st|nd|rd|th)?', street[1], re.I):
                suffix = value[street.end():].lstrip().split()[0].rstrip('.')
                return bool(ZIP.search(value) or suffix[0].isupper() and
                            re.fullmatch(r'(?i:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|court|ct|parkway|pkwy|highway|hwy|way|circle|cir|terrace|ter|place|pl)', suffix) and
                            not re.search(r'(?i)\b(?:stage|chapter|version|round|race|track|score|in)[ \t]*$', before))
            if street and (street[1][0].isupper() or ZIP.search(value)):
                return True
            return False
    elif kind == 'phone':
        if explicit_ssn: return False
        if DATE_IP.fullmatch(value): return False
        if value.startswith('+'):
            return international_phone(value)
        d = digits(value)
        if (direct_phone or polish_prefix) and 7<=len(d)<=15: return True
        if PHONE_ID.search(before) or re.search(r'[A-Za-z][A-Za-z0-9_]*-$', before): return False
        standalone = not text[:a].strip() and not text[b:].strip()
        if re.fullmatch(r'\d{2} \d{3} \d{2} \d{2}', value) and (lang == 'en' or re.search(r'(?i)\b(?:dimensions|amounts|page numbers)\s*:\s*$', before)) and not KEY_PATTERNS['phone'].search(before+' '+text[b:b+40]):
            return False
        if POLISH_PHONE.fullmatch(value) and (lang != 'en' or '-' in value or '(' in value or
                KEY_PATTERNS['phone'].search(before+' '+text[b:b+40])):
            if not NEGATIVE.search(before+' '+text[b:b+40]): return True
        if re.fullmatch(r'[2-9]\d{2}-\d{4}', value):
            local_context = standalone or KEY_PATTERNS['phone'].search(before[-40:]) or re.search(r'(?i)\b(?:ping|reach|ring)(?:[ \t]+me)?[ \t]+(?:at|on)[ \t]*$', before)
            return bool(local_context and not NEGATIVE.search(before+' '+text[b:b+40]))
        if re.fullmatch(r'\d{1,3}(?:[ \t]\d{3})+', value) and not POLISH_FORMAT.fullmatch(value):
            return False
        standalone = not before.strip() and not text[b:b+60].strip() and not text[:a].strip() and not text[b:].strip()
        nanp = len(d)==10 and d[0] in '23456789' and d[3] in '23456789'
        if (standalone and value.startswith('+') and 7<=len(d)<=15 or
            nanp and (standalone or NANP_FORMAT.fullmatch(value)) or
            standalone and POLISH_FORMAT.fullmatch(value)):
            if not NEGATIVE.search(before+' '+text[b:b+40]): return True
    elif kind == 'ssn':
        if explicit_ssn: return True
        if nino and NINO_ID.search(before): return False
        ctx=before+' '+text[b:b+40]
        if NEGATIVE.search(ctx) and not KEY_PATTERNS[kind].search(ctx): return False
    return probability(text,a,b,kind) >= model()['models'][kind]['threshold']


def spans(text):
    paragraphs = list(language.paragraphs(text))
    starts = [offset for offset, _ in paragraphs]
    langs = [language.detect(paragraph) for _, paragraph in paragraphs]
    items = candidates(text)
    budget = _CANDIDATE_BUDGET.get()
    if budget is not None and len(items) > budget:
        raise CandidateBudgetExceeded('PII candidate budget exceeded')
    return [(a,b,k) for a,b,k in items if accepted(text,a,b,k,langs[bisect_right(starts,a)-1])]
