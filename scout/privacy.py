"""Local span detector. No matched values are persisted or logged."""
import re
from itertools import chain
from datetime import date

from . import credentials, language, pii_model, pseudonyms

# Regex detectors. Phones, cards, SSNs and addresses come from the context model in pii_model.
PATTERNS = {
    "pesel": re.compile(r"(?<!\d)\d{11}(?!\d)"),
    "email": re.compile(r"(?i)(?<![A-Z0-9._%+-])\b[A-Z0-9._%+-]{1,64}@(?=[A-Z0-9.-]{1,255}(?![A-Z0-9.-]))[A-Z0-9.-]{1,252}\.[A-Z]{2,63}\b"),
    "secret": re.compile(r"(?i)(?:\bsk-[\w-]{6,}|\b(?:api[ _-]?key|password|token|secret)\s*[:=]\s*(?!true\b|false\b|null\b|none\b|\d+\b)(?![^\s]*\()\S{6,})"
                        # Well-known credential formats (high-precision prefixes) and bearer tokens / private key blocks.
                        r"|\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}"
                        r"|\bgh[pousr]_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{40,}"
                        r"|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b|\bxox[abprs]-[A-Za-z0-9-]{10,}|\bAIza[0-9A-Za-z_-]{35,45}(?![0-9A-Za-z_-])"
                        r"|\bSG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{32,64}\b"
                        r"|\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
                        r"|(?i:\bbearer)\s+[^\s,;]{20,}"
                        r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"),
    # Card verification code: only next to an explicit keyword; the span covers the digits only.
    "cvv": re.compile(r"(?i)\b(?:cvv2?|cvc2?|cid|csc|security\s+code|card\s+verification(?:\s+(?:code|value))?)\b\s*(?:code|number|no\.?)?\s*[:#=]?\s*(?P<cvv>\d{3,4})\b")}


def _iban_valid(value):
    compact = re.sub(r"[\s./]+", "", value).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", compact):
        return False
    digits = "".join(str(ord(ch) - 55) if ch.isalpha() else ch for ch in compact[4:] + compact[:4])
    remainder = 0
    for char in digits:
        remainder = (remainder * 10 + int(char)) % 97
    return remainder == 1


# Match only the header: consuming a greedy BBAN swallowed the next IBAN header.
PATTERNS["iban"] = re.compile(r"(?<![A-Z0-9])(?P<iban>[A-Z]{2}\d{2})", re.I)
IBAN_LENGTHS = {'PL':28,'DE':22,'GB':22,'FR':27,'ES':24,'IT':27,'NL':18,'BE':16,'CH':21,'AT':20,'IE':22,'PT':25,'DK':18,'NO':15,'SE':24,'FI':18,'GR':27,'LU':20,'CZ':24,'HU':28,'RO':24,'TR':26,'SK':24,'AD':24,'BG':22,'CY':28,'EE':20,'GI':23,'HR':21,'IS':26,'LI':21,'LT':20,'LV':21,'MC':27,'MT':31,'SI':19,'SM':27,'VA':22,'UA':29}
_IBAN_BODIES = {country: re.compile(r'(?:[ \t./\r\n]{0,3}[A-Z0-9]){' + str(length - 4) + r'}(?![A-Z0-9])', re.I)
                for country, length in IBAN_LENGTHS.items()}


# an address span usually covers only the street line ("07570 Joanna Mountains"); extend it
# over the adjoining unit / postcode / city / state segments so the whole address is masked
# (Gretel test full coverage was 35.8%). Bounded: at most four segments, only after ", " (or a space
# before a postcode/state), Title-case place names of up to four words.
_ADDR_UNIT = r"(?:Apt\.?|Apartment|Suite|Ste\.?|Unit|Flat|Floor|Fl\.|Level|Sector|Room|Rm\.?|#|H\.?\s?No\.?|No\.)\s*[A-Za-z0-9/-]{1,8}"
_ADDR_POST = r"(?:[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}|[A-Z]\d[A-Z]\s?\d[A-Z]\d|\d{3,6}(?:-\d{4})?)(?![A-Za-z0-9-])"
_ADDR_FIELD_LABELS = r"(?!(?:Secret|Token|Password|Passcode|Card|Credit|Phone|Mobile|Email|E-mail|SSN|Name|Account|Number|Key|Api|IBAN|Pin|Dob|Date)\b)"
_ADDR_PLACE = _ADDR_FIELD_LABELS + r"(?:(?:[A-Z]{2,3}\s)?[A-Z][a-z'\-]+)(?:\s[A-Z][a-z'\-]+){0,3}(?:\s(?:[A-Z]{2,3})(?![A-Za-z0-9]))?(?:\s" + _ADDR_POST + r")?|[A-Z]{2,3}(?:\s+" + _ADDR_POST + r")?\b"
_ADDR_COUNTRY = r"(?:United[ \t]+Kingdom|United[ \t]+States(?:[ \t]+of[ \t]+America)?|UK|USA|Canada|Australia|India|Japan|Germany|France|Spain|Italy|Poland|Polska)(?![A-Za-z])"
_ADDR_LINE = r"[A-Z][a-z'\-]+(?:[ \t]+[A-Z][a-z'\-]+){0,3}(?:,?[ \t]+[A-Z]{2,3})?,?[ \t]+" + _ADDR_POST + r"(?:\r?\n[ \t]*" + _ADDR_COUNTRY + r")?"
_ADDR_NEXT = re.compile(r"(?:,[ \t]*(?:" + _ADDR_UNIT + "|" + _ADDR_POST + "|" + _ADDR_PLACE + r")|[ \t]+(?:" + _ADDR_UNIT + "|" + _ADDR_POST + r")|\r?\n[ \t]*(?:" + _ADDR_UNIT + "|" + _ADDR_LINE + "|" + _ADDR_COUNTRY + r"))")
_ADDR_NEXT_FIELD = re.compile(r"(?m)^[ \t]*[A-Za-z][A-Za-z _-]{0,31}[ \t]*[:=]")
_ADDR_PREV = re.compile(r"(?:" + _ADDR_UNIT + r"),?\s?$")
_ADDR_KEYWORD_PREV = re.compile(r"(?:Apt\.?|Apartment|Suite|Studio|Ste\.?|Unit|Flat|Room|H\.?\s?No\.?)\s?$")
_ADDR_TOKEN_TAIL = re.compile(r"[A-Za-z0-9]+(?:\s\d[A-Z]{2}\b)?")
_ADDR_UNIT_NUMBER = re.compile(r"\.?\s?(?:H\.?\s?No\.?\s?)?[A-Za-z0-9][A-Za-z0-9/\-]{0,7}")
_ADDR_ENDS_WITH_UNIT = re.compile(r"(?:Apt|Apartment|Suite|Studio|Unit|Flat|Room|H\.?No)\.?$")
_ADDR_STREET_PREV = re.compile(r"\d{1,6}\s[A-Z][a-z]+(?:\s[A-Za-z]+){0,3},\s?$")

# International street lines require a street marker, house number and postcode.
# A capitalised place or a generic labelled location alone is not an address.
_INTL_WORD = r"(?![\w'-]+[ \t]*[:=])[A-ZÀ-ÖØ-ÞĄĆĘŁŃÓŚŹŻ][A-Za-zÀ-ÖØ-öø-ÿąćęłńóśźż'’.-]{0,29}[A-Za-zÀ-ÖØ-öø-ÿąćęłńóśźż]"
_INTL_CITY = _INTL_WORD + r'(?:[ \t]+' + _INTL_WORD + r'){0,3}'
_INTL_SEP = r'(?:,[ \t]*|[ \t]*\r?\n[ \t]*)'
_INTL_STREET = (r'(?:Via|Viale|Corso|Piazza|Piazzale|Largo|Vicolo|Calle|Carrer|Avenida|Avinguda|Paseo|Passeig|'
                r'Plaza|Pla[cç]a|Gran[ \t]+V[ií]a|Rua|Rue|Avenue|Boulevard|Bd|Place|Quai|All[ée]e|Impasse|Chemin|'
                r'Cours|Am|An[ \t]+der|Im|Auf[ \t]+der)')
_INTL_COMPOUND = (r"[A-ZÀ-ÖØ-Þ][\wÀ-ÿ'’.-]*(?:stra(?:ß|ss)e|str\.|weg|allee|platz|ring|gasse|damm|ufer|"
                  r'gracht|straat|singel|laan|kade|plein|dijk|markt)')
_INTL_NUMBER = r'\d{1,4}[A-Za-z]?(?:[-/]\d{1,4})?'
_INTL_POST_CITY = (r'(?:\d{5}|\d{4}[ \t]?[A-Z]{2}|\d{4})(?![\w-])[ \t]+' + _INTL_CITY
                   + r'(?:[ \t]+(?i:cedex)(?:[ \t]+\d{1,2})?)?')
_CONTINENTAL_ADDRESS = re.compile(
    r'(?<![\w-])(?:' + _INTL_STREET + r"(?:[ \t]+[\wÀ-ÿ'’.-]{1,30}){1,6}[ \t]+" + _INTL_NUMBER
    + r'|' + _INTL_COMPOUND + r'[ \t]+' + _INTL_NUMBER
    + r'|' + _INTL_NUMBER + r'[ \t]+(?i:rue|avenue|boulevard|place|quai|all[ée]e|impasse|chemin|cours)'
    + r"(?:[ \t]+[\wÀ-ÿ'’.-]{1,30}){1,6})" + _INTL_SEP + _INTL_POST_CITY)
_INTL_PO_BOX = re.compile(
    r'(?<!\w)(?:Postfach|Postbus|Bo[iî]te[ \t]+[Pp]ostale|B\.?P\.|Apartado(?:[ \t]+de[ \t]+[Cc]orreos)?|'
    r'Casella[ \t]+[Pp]ostale|C\.P\.)[ \t]+\d{1,6}(?![\w-])'
    + r'(?:' + _INTL_SEP + _INTL_POST_CITY + r')?')
_JAPANESE_ADDRESS = re.compile(
    r'(?<![\w-])\d{1,4}-\d{1,4}(?:-\d{1,4})?[ \t]+' + _INTL_WORD
    + r'(?:' + _INTL_SEP + _INTL_WORD + r'-(?:ku|shi|cho|machi|gun))?'
    + _INTL_SEP + _INTL_WORD + r'[ \t]+\d{3}-\d{4}(?![\w-])(?:' + _INTL_SEP + r'Japan\b)?')


def _extend_address(text, start, stop):
    window = text[max(0, start - 24):start]
    match = _ADDR_PREV.search(window) or _ADDR_KEYWORD_PREV.search(window)
    if match:
        start = start - (len(window) - match.start())
    window = text[max(0, start - 48):start]
    match = _ADDR_STREET_PREV.search(window)  # street line before a postcode/city span
    if match:
        start = start - (len(window) - match.start())
    if 0 < stop < len(text) and text[stop - 1].isalnum() and text[stop].isalnum():
        tail = _ADDR_TOKEN_TAIL.match(text, stop)  # span ended mid-token, e.g. "DH" of "DH8 1UH"
        if tail:
            stop = tail.end()
    for _ in range(4):
        if _ADDR_ENDS_WITH_UNIT.search(text[max(0, stop - 10):stop]):
            unit = _ADDR_UNIT_NUMBER.match(text, stop)
            if unit:
                stop = unit.end()
                continue
        match = _ADDR_NEXT.match(text, stop)
        if not match:
            break
        field = _ADDR_NEXT_FIELD.search(text, stop, min(len(text), match.end() + 35))
        if field and field.start() < match.end():
            stop = field.start()
            break
        stop = match.end()
    while stop > start and text[stop - 1] in '.,; \t\r\n':
        stop -= 1
    return start, stop


_PLACEHOLDER_PREFIX_RE = re.compile(r'(?i)^(?:(?:api[ _-]?key|password|token|secret)\s*[:=]\s*|bearer\s+|sk-)')
_PLACEHOLDER_BODY_RE = re.compile(
    r'YOUR_[A-Z_]+|\**(?i:REDACTED|X{3,})\**|\*{3,}'
    r'|<[A-Z_][A-Z_ -]*>|<[a-z][a-z _-]{0,40}>'
    r'|<(?i:token|api[_-]?key|password|secret|value|redacted)>'
    r'|\$\{(?:[A-Z_]+|[a-z_]+)\}|\{\{(?:[A-Z_]+|[a-z_]+)\}\}')


def _secret_placeholder(value):
    """Reject explicit template syntax; ordinary password-like values remain protected."""
    value = _PLACEHOLDER_PREFIX_RE.sub('', value).strip('"\'`')
    return bool(_PLACEHOLDER_BODY_RE.fullmatch(value))


_SPLIT_KEY = re.compile(r"(?P<quote>[\"'])(?P<head>(?:sk-|gh[pousr]_)[A-Za-z0-9_-]{1,256})[ \t]*\r?\n"
                        r"[ \t]*(?P<tail>[A-Za-z0-9_-]{1,256})(?P=quote)")
_SECRET_VALUE = r'''"(?:\\.|[^"\\\r\n])+"|'(?:\\.|[^'\\\r\n])+'|`[^`\r\n]+`|[^\s,;{}"'`]+'''
_SECRET_ASSIGNMENT = re.compile(
    r'''(?<![\w.-])(?P<quote>["'`]?)(?P<label>[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż][A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż0-9_-]{0,63})(?P=quote)[ \t]*[:=][ \t]*(?:\r?\n[ \t]*)?'''
    r'(?=(?P<value>' + _SECRET_VALUE + r'))')
_SECRET_FORM = re.compile(
    r'(?m)^[ \t]*(?P<label>password|passwd|pwd|hasło|haslo|hasto|api[_-]?(?:key|t0ken|token))[ \t]*\r?\n[ \t]*'
    r'(?=(?P<value>' + _SECRET_VALUE + r'))', re.I)
_SECRET_PROSE = re.compile(
    r'(?i)\b(?:api[ _-]?keys?|klucz[ \t]+api(?:[ \t]+dostawcy)?)(?:[ \t]*[:=,][ \t]*|[ \t]+)'
    r'(?:(?:is|to)[ \t]+|for[ \t]+instance,[ \t]*|\(e\.g\.,[ \t]*)?'
    r'(?P<value>' + _SECRET_VALUE + r')')
_SECRET_LIST_JOIN = re.compile(r'(?:,[ \t]*(?:and[ \t]+)?|[ \t]+and[ \t]+)(?P<value>' + _SECRET_VALUE + r')')
_URL_PASSWORD = re.compile(r'(?i)\b[a-z][a-z0-9+.-]{1,20}://[^\s/:@]{1,128}:(?P<value>[^\s/@]+)@(?=[a-z0-9\[])')
_SECRET_LABEL_RE = re.compile(r'api|api_?keys?|password|passwd|passphrase|pwd|token|secret|credential|has[łl]o|hasto|(?:sekret|has[łl]o|hasto|cred)_[a-z0-9_]+|[a-z0-9_]+_(?:key|keys|token|secret|password|pass|credential)')
_IPV4_RE = re.compile(r'\d{1,3}(?:\.\d{1,3}){3}')
_TOKEN_WORD_RE = re.compile(r'(?i)token|key|value|secret')
_DIGIT_RE = re.compile(r'\d')
_ALPHA_RE = re.compile(r'[A-Za-z]')
_SECRET_HINTS = ('sk-', 'api_key', 'api-key', 'api key', 'apikey', 'password', 'token', 'secret', '_live_', '_test_', 'ghp_', 'gho_', 'ghu_', 'ghs_', 'ghr_', 'github_pat_', 'akia', 'asia', 'xox', 'aiza', 'sg.', 'eyj', 'bearer', 'private key')
_LABEL_SECRET_HINTS = ('api', 'api_key', 'api-key', 'apikey', 'password', 'pwd', 'token', 't0ken', 'secret', 'credential', 'cred_', 'haslo', 'hasło', 'hasto', 'sekret', '_pass', '_key', '-key')
_CVV_HINTS = ('cvv', 'cvc', 'cid', 'csc', 'security', 'verification')
_RETINA_ASSET = re.compile(r'(?i)[^@\s]+@[2-4]x\.(?:png|jpe?g|gif|webp|svg|avif)')
_ASSET_CONTEXT = re.compile(r'(?i)\b(?:assets?|images?|graphics?|webpack|grafik\w*|graficzn\w*)\b')
_EMAIL_LABEL = re.compile(r'(?i)\b(?:e-?mail|mailbox|skrzynk\w*)\s*[:=]?\s*$')


def _labelled_secrets(text, lowered=None):
    if lowered is None:
        lowered = text.lower()
    if any(h in lowered for h in _LABEL_SECRET_HINTS):
        for match in chain(_SECRET_ASSIGNMENT.finditer(text), _SECRET_FORM.finditer(text)):
            label = re.sub(r'([a-z])([A-Z])', r'\1_\2', match['label']).lower().replace('-', '_').replace('0', 'o').replace('1', 'l')
            if not _SECRET_LABEL_RE.fullmatch(label):
                continue
            if '\n' in match.group() and _SECRET_ASSIGNMENT.match(text, match.start('value')):
                continue
            value = match['value'].strip('\"\'`')
            if value.startswith('<') and not value.endswith('>'):
                continue
            # A hash of a password is not the password, and an IP address is not a secret (labelling rules).
            if 'hash' in label or 'public' in label or _IPV4_RE.fullmatch(value):
                continue
            # A word-only value naming the field ("sas_token_value", "authTokenSecure") is a placeholder, not a token.
            if not label.endswith(('password', 'pwd')) and not _DIGIT_RE.search(value) \
                    and _TOKEN_WORD_RE.search(value):
                continue
            minimum = 4 if label.endswith(('credential', 'password', 'pwd')) and value.isdigit() else 6
            if len(value) < minimum or _secret_placeholder(value) or value.lower() in ('true', 'false', 'null', 'none', 'password', 'passwd', 'secret', 'token', 'changeme', 'example') or ('(' in value and ')' in value):
                continue
            if label == 'api' and not (len(value) >= 16 and _ALPHA_RE.search(value) and _DIGIT_RE.search(value)):
                continue
            # Generic *_key fields also name indexes and sorting keys. Require a token shape.
            if label.endswith('_key') and 'api' not in label.split('_') and 'secret' not in label.split('_'):
                if not (len(value) >= 16 and _ALPHA_RE.search(value) and _DIGIT_RE.search(value)):
                    continue
            a, b = match.span('value')
            quoted = text[a] in '\"\'`'
            yield a + quoted, b - quoted, 'secret'
    if 'api' in lowered:
        for match in _SECRET_PROSE.finditer(text):
            for _ in range(3):
                a, b = match.span('value')
                value = match['value'].strip('"\'`').rstrip('.')
                if (len(value) < 8 or not _ALPHA_RE.search(value) or not _DIGIT_RE.search(value)
                        or _secret_placeholder(value) or _IPV4_RE.fullmatch(value)
                        or re.search(r'[()=]', value)):
                    break
                quoted = text[a] in '"\'`'
                yield a + quoted, a + quoted + len(value), 'secret'
                match = _SECRET_LIST_JOIN.match(text, b)
                if match is None:
                    break
    if '://' in text:
        for match in _URL_PASSWORD.finditer(text):
            if not _secret_placeholder(match['value']):
                yield *match.span('value'), 'secret'


_CHECKSUM_NUMBER = re.compile(r'(?<!\w)\d(?:[\d \t./\r\n-]{0,76}\d)?(?!\w)')
_CHECKSUM_GATE_RE = re.compile(r'\d[ \t-]*[./\r\n]')
_ALT_SEP_RE = re.compile(r'[./\r\n]')
_STRIP_SEP_RE = re.compile(r'[\s./-]')
_SPLIT_GROUP_RE = re.compile(r'[ \t./-]+')
_LINE_BREAK_RE = re.compile(r'[ \t]*\r?\n[ \t]*')
_PESEL_LABEL_RE = re.compile(r'(?i)\bpesel\s*[:=#]?\s*$')


def _checksum_spans(text):
    """Only validated values may bridge lines or use alternative numeric separators."""
    for match in _CHECKSUM_NUMBER.finditer(text):
        if match.end() - match.start() < 12:
            continue
        start = pii_model._list_value_start(text, *match.span())
        raw = text[start:match.end()]
        if not _ALT_SEP_RE.search(raw):
            continue
        value = _STRIP_SEP_RE.sub('', raw)
        if len(value) not in (11, 15, 16):
            continue
        # Real grouping only: one separator kind, card groups 4-4-4-4 / 4-6-5 (or two halves of a contiguous
        # value), PESEL whole or in two halves. A line break may stand for a separator or split a group.
        # Versions, timestamps, floats and hex dumps happen to pass checksums once their digits are joined.
        def shaped(candidate):
            separators = {c for c in candidate if c in './-'}
            if len(separators) > 1:
                return False
            groups = [len(g) for g in _SPLIT_GROUP_RE.split(candidate.strip()) if g]
            halves = len(groups) == 2
            if len(value) == 11:
                return groups == [11] or halves
            return groups in ([15], [16], [4, 4, 4, 4], [4, 6, 5]) or halves
        if not any(shaped(_LINE_BREAK_RE.sub(glue, raw)) for glue in (' ', '')):
            continue
        if len(value) == 11:
            # Reuse the calendar and checksum gates of the ordinary PESEL detector.
            probe = 'PESEL: ' if _PESEL_LABEL_RE.search(text[max(0, start-24):start]) else ''
            if any(c == 'pesel' for _, _, c in spans(probe + value)):
                yield start, match.end(), 'pesel'
        elif pii_model.luhn(value):
            canonical = text[:start] + value + text[match.end():]
            if pii_model.accepted(canonical, start, start + len(value), 'card'):
                yield start, match.end(), 'card'


def spans(text):
    result = []
    has_digit = bool(_DIGIT_RE.search(text))
    lowered = text.lower()
    has_secret_hint = any(hint in lowered for hint in _SECRET_HINTS)
    for category, pattern in PATTERNS.items():
        if category == 'email' and '@' not in text or category in ('pesel', 'cvv', 'iban') and not has_digit:
            continue
        if category == 'cvv' and not any(h in lowered for h in _CVV_HINTS):
            continue
        if category == 'secret' and not has_secret_hint:
            continue
        for match in pattern.finditer(text):
            if category == 'secret' and '\n' in match.group():
                header = re.match(r'(?i)(?:api[ _-]?key|password|token|secret)\s*[:=]\s*', match.group())
                if header and _SECRET_ASSIGNMENT.match(text, match.start()+header.end()):
                    continue
            if category == 'email' and _RETINA_ASSET.fullmatch(match.group()):
                before = text[max(0,match.start()-80):match.start()]
                if _ASSET_CONTEXT.search(before) and not _EMAIL_LABEL.search(before):
                    continue
            if category == 'secret' and _secret_placeholder(match.group()):
                continue
            if category == 'secret' and re.fullmatch(r'(?i)(?:token|password|secret|api[ _-]?key)\s*[:=]\s*<[^<>,\s]+,', match.group()):
                if re.fullmatch(r'(?i)token\s*=\s*<token,', match.group()) and re.match(r'[ \t]*see[ \t]+\[RFC\d+\](?:,[ \t]*Section[ \t]+[\d.]+)?[ \t]*>', text[match.end():]):
                    continue  # RFC ABNF production, rather than a credential assignment.
                tail = re.match(r'[^<>\r\n]{1,128}>', text[match.end():match.end()+129])
                result.append((match.start(), match.end() + (tail.end() if tail else 0), category))
                continue
            if category == "iban":
                country = match.group('iban')[:2].upper()
                body_pat = _IBAN_BODIES.get(country)
                if not body_pat:
                    continue
                body = body_pat.match(text, match.end(), match.end() + 4 * IBAN_LENGTHS[country])
                if not body or not _iban_valid(match.group('iban') + body.group()):
                    continue
                match_span = (match.start('iban'), body.end())
            elif category == "cvv":
                match_span = (match.start('cvv'), match.end('cvv'))
            else:
                match_span = (match.start(), match.end())
            if category == "pesel":
                value = match.group()
                if (sum(int(d)*w for d,w in zip(value[:10], (1,3,7,9,1,3,7,9,1,3))) + int(value[10])) % 10:
                    continue
                # Calendar validation prevents arbitrary checksum-valid identifiers
                # from becoming PESEL. Explicit labels and legacy fixture dates
                # remain protected even when the source supplied a damaged date.
                month = int(value[2:4])
                century = {0:1900, 1:2000, 2:2100, 3:2200, 4:1800}.get(month//20)
                try:
                    if century is None:
                        raise ValueError('Invalid month')
                    date(century+int(value[:2]), month%20, int(value[4:6]))
                except ValueError:
                    if not value.startswith('000000') and not _PESEL_LABEL_RE.search(text[max(0,match.start()-24):match.start()]):
                        continue
            result.append((*match_span, category))
    if has_secret_hint and ('sk-' in lowered or 'gh' in lowered):
        for match in _SPLIT_KEY.finditer(text):
            joined = match['head'] + match['tail']
            if PATTERNS['secret'].fullmatch(joined) and not _secret_placeholder(joined):
                result.append((*match.span(), 'secret'))
    result.extend(_labelled_secrets(text, lowered))
    if has_digit and _CHECKSUM_GATE_RE.search(text):
        result.extend(_checksum_spans(text))
    result.extend(pii_model.spans(text))
    result = [(*_extend_address(text, s, e), c) if c == 'address' else (s, e, c) for s, e, c in result]
    if has_digit:
        for pattern in (_CONTINENTAL_ADDRESS, _INTL_PO_BOX, _JAPANESE_ADDRESS):
            result.extend((*match.span(), 'address') for match in pattern.finditer(text))
    merged = []
    for start, stop, category in sorted(set(result), key=lambda item: (item[2], item[0], item[1])):
        if merged and merged[-1][2] == category and (start <= merged[-1][1] or category == 'address' and re.fullmatch(r'[ \t,]{1,3}|[ \t,]*\r?\n[ \t]*',text[merged[-1][1]:start])):
            prev_start, prev_stop, _ = merged[-1]
            merged[-1] = (prev_start, max(prev_stop, stop), category)
        else:
            merged.append((start, stop, category))
    return merged


def local_spans(text, names=False, name_masking="basic", address_masking="on", other_languages="off"):
    """Resolve complete person spans inside addresses without reducing mask coverage."""
    if name_masking not in ("off", "basic", "deep") or address_masking not in ("off", "on") or other_languages not in ("off", "on"):
        raise ValueError("invalid masking setting")
    # Detectors can independently report the same credential; keep one physical mask.
    found = list(dict.fromkeys(spans(text) + credentials.spans(text)))
    found = [(s,e,c) for s,e,c in found if c != 'secret' or not _secret_placeholder(text[s:e])]
    if address_masking == 'off':
        found = [x for x in found if x[2] != 'address']
    elif other_languages == 'off':
        from . import language
        other = [(start, start + len(part)) for start, part in language.paragraphs(text)
                 if language.paragraph_language(part) == 'other']
        found = [(s, e, c) for s, e, c in found if c != 'address' or not any(a <= s < b for a, b in other)]
    if names and name_masking != 'off':
        from . import name_model
        addresses = [(s,e) for s,e,c in found if c == 'address']
        found += [(s,e,c) for s,e,c in name_model.prompt_spans(text, mode=name_masking, other_languages=other_languages)
                  if not any(a <= s and e <= b for a,b in addresses)]
    return found


def categories(text):
    return sorted({item[2] for item in spans(text)})


def prompt_warning(text):
    """Return category names only; the observer itself does not redact user prompts."""
    return categories(text)


def mask_local(text):
    masked, categories = mask_local_details(text)
    return masked, len(categories)


_ZERO_WIDTH = dict.fromkeys(map(ord, '\u200b\u200c\u200d\u200e\u200f\u2060\u2061\u2062\u2063\u2064\ufeff\u00ad'), None)
_HYPHENS = {ord(c): '-' for c in '\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d'}


class _OffsetMap(list):
    def __init__(self, starts, ends):
        super().__init__(starts)
        self.ends = ends


_ENCODED = re.compile(r'(?:%[0-9a-f]{2})+|&(?:\#[0-9]{1,7}|\#x[0-9a-f]{1,6}|[a-z][a-z0-9]{1,31});|[^%&]+|.', re.I | re.S)
_ENCODED_PROBE = re.compile(r'%[0-9a-f]{2}|&(?:\#[0-9]{1,7}|\#x[0-9a-f]{1,6}|[a-z][a-z0-9]{1,31});', re.I)
_SPECIAL_NORM = re.compile(r'[\u00ad\u200b-\u200f\u2010-\u2015\u2060-\u2064\u2212\ufe58\ufe63\ufeff\uff0dасеорхуАЕОРСХіІ]')
_HOMOGLYPH_RE = re.compile(r'[асеорхуАЕОРСХіІ]')
_HOMOGLYPHS = str.maketrans('асеорхуАЕОРСХіІ', 'aceopxyAEOPCXiI')
_MIXED_TOKEN = re.compile(r'[\w@.+%/-]+')


def _normalise_for_detection(text):
    """Decode once, retaining the complete source interval of every output character."""
    import unicodedata
    has_encoded = ('%' in text or '&' in text) and bool(_ENCODED_PROBE.search(text))
    if not has_encoded and (text.isascii() or unicodedata.is_normalized('NFKC', text) and not _SPECIAL_NORM.search(text)):
        return text, None
    import html
    out, starts, ends = [], [], []
    userinfo = [m.span('value') for m in _URL_PASSWORD.finditer(text)] if has_encoded and '://' in text else []
    userinfo_index = 0
    for match in _ENCODED.finditer(text):
        raw = match.group()
        m_start = match.start()
        units = [(ch, m_start + i, m_start + i + 1) for i, ch in enumerate(raw)]
        while userinfo_index < len(userinfo) and userinfo[userinfo_index][1] <= m_start:
            userinfo_index += 1
        protected = userinfo_index < len(userinfo) and userinfo[userinfo_index][0] <= m_start < userinfo[userinfo_index][1]
        if protected:
            pass  # Decoding reserved delimiters would change the URL userinfo boundary.
        elif raw.startswith('%') and len(raw) >= 3:     # a lone '%' is a character, not an escape
            try:
                decoded = bytes.fromhex(raw.replace('%', '')).decode('utf-8')
            except (ValueError, UnicodeDecodeError):
                pass
            else:
                units, cursor = [], m_start
                for ch in decoded:
                    stop = cursor + 3 * len(ch.encode('utf-8'))
                    units.append((ch, cursor, stop))
                    cursor = stop
        elif raw.startswith('&') and len(raw) > 1:
            decoded = html.unescape(raw)
            if decoded != raw:
                units = [(ch, m_start, match.end()) for ch in decoded]
        for ch, start, end in units:
            if ch.isascii():
                out.append(ch); starts.append(start); ends.append(end)
                continue
            if ord(ch) in _ZERO_WIDTH:
                continue
            ch = ch.translate(_HYPHENS)
            norm = unicodedata.normalize('NFKC', ch) if not ch.isascii() else ch
            for piece in norm:
                out.append(piece)
                starts.append(start)
                ends.append(end)
    clean = ''.join(out)
    if _HOMOGLYPH_RE.search(clean):
        def fold(match):
            token = match.group()
            if re.search(r'[A-Za-z]', token) and re.search(r'[\u0400-\u04ff]', token):
                return token.translate(_HOMOGLYPHS)
            return token
        clean = _MIXED_TOKEN.sub(fold, clean)
    return (clean, _OffsetMap(starts, ends)) if clean != text else (text, None)


def _field_probe(field):
    """'cvv: ' for a JSON field named cvv: the name becomes context for every detector, never part of a mask."""
    if not isinstance(field, str) or not field:
        return ''
    return ' '.join(re.sub(r'[^A-Za-z0-9 -]', ' ', field[:64]).split()) + ': '


def mask_local_details(text, names=False, field=None, name_masking="basic", address_masking="on", other_languages="off"):
    """Mask detected PII. names=True adds the person-name detector (used for user prompts; see name_model).
    field: name of the JSON field the text came from ({"cvv": "402"}, {"otp_code": 543918}); it is detection
    context only, so the field name itself is never masked or changed."""
    if name_masking not in ("off", "basic", "deep") or address_masking not in ("off", "on") or other_languages not in ("off", "on"):
        raise ValueError("invalid masking setting")
    token = pii_model._CANDIDATE_BUDGET.set(pii_model.MASK_CANDIDATE_LIMIT)
    cache_token = language.CACHE.set({}) if language.CACHE.get() is None else None
    try:
        # zero-width characters, full-width forms and Unicode hyphens hid e-mails,
        # cards and phones from every detector. Detect on a normalised copy, then map spans back to the original.
        clean, index = _normalise_for_detection(text)
        probe = _field_probe(field)
        work = probe + clean
        # password-like values without a known format (source thresholds, optional basal check: see credentials)
        # Field context must not push an otherwise supported value past the name limit.
        from . import name_model
        separate_names = names and bool(probe) and len(clean) <= name_model.MAX_TEXT < len(work)
        found = local_spans(work, names=names and not separate_names, name_masking=name_masking, address_masking=address_masking, other_languages=other_languages)
        if separate_names and name_masking != 'off':
            found += [(s + len(probe), e + len(probe), c) for s, e, c in name_model.prompt_spans(clean, mode=name_masking, other_languages=other_languages)]
        if probe:
            # a span reaching from the probe into the value ("cvv: 402") masks the value part only
            n = len(probe)
            found = [(max(s, n) - n, e - n, c) for s, e, c in found if e > n]
        if clean is not text:
            found = [(index[s], index.ends[e - 1], c) for s, e, c in found if e > s]
        matches = sorted(found, key=lambda item: (item[0], -(item[1]-item[0])))
    finally:
        if cache_token is not None:
            language.CACHE.reset(cache_token)
        pii_model._CANDIDATE_BUDGET.reset(token)
    # overlapping spans of different categories are UNITED, never
    # dropped — skipping a later overlapping span leaked the rest of an e-mail, card or secret. The region is
    # labelled with its longest span's category; every category in it is reported.
    regions = []
    for start, stop, category in matches:
        if regions and start < regions[-1][1]:
            region = regions[-1]
            region[1] = max(region[1], stop)
            region[2].append((stop - start, category))
        else:
            regions.append([start, stop, [(stop - start, category)]])
    out, end, categories = [], 0, []
    book = pseudonyms.CURRENT.get()
    for start, stop, members in regions:
        label = max(members, key=lambda item: item[0])[1]
        replacement = "[" + label.upper() + "]"
        if book is not None and all(category in pseudonyms.CATEGORIES for _, category in members):
            try:
                replacement = book.token(label, text[start:stop]) or replacement
            except Exception:
                pass  # Allocation failure keeps the same fully masked region.
        out.extend((text[end:start], replacement))
        end = stop
        for _, category in members:
            categories.append(category)
    out.append(text[end:])
    return "".join(out), tuple(categories)


_URL = re.compile(r"https?://", re.I)
_PATH_START = re.compile(
    r"(?:^|[\s'\"=(])(?:/(?:[\w.-]+/)*[\w.-]*|~(?:/[\w.-]+)*|\.\.?/(?:[\w.-]+/)*[\w.-]*|[A-Za-z]:[\\/][\w./\\-]+)"
)
_PATH_EXTENSION = re.compile(r"(?:^|[\s'\"=(])(?:[\w.-]+/)*[\w.-]+\.[A-Za-z]+(?:/[\w.-]+)*(?=$|[\s,;:)])")
_LONG_DIGITS = re.compile(r"\d{5,}")
_CONTROL = re.compile(r"[\x00-\x1f]")


def reason_issue(text):
    """Return a safe, specific reason rejection without echoing its contents."""
    if not isinstance(text, str):
        return "data type: text required"
    if len(text) < 1:
        return f"length {len(text)} < 1"
    if len(text) > 400:
        return f"length {len(text)} > 400"
    found = categories(text)
    if found:
        return "PII category: " + ", ".join(found)
    if _URL.search(text):
        return "URL"
    if _PATH_START.search(text) or _PATH_EXTENSION.search(text):
        return "file path syntax"
    if _LONG_DIGITS.search(text):
        return "sequence of at least 5 digits"
    if _CONTROL.search(text):
        return "control character"
    return None


def safe_reason(text):
    return reason_issue(text) is None


def fields(text):
    rules = {"issue": r"\b[A-Z][A-Z0-9]+-\d+\b", "commit": r"\b[0-9a-f]{7,40}\b",
             "version": r"\b\d+\.\d+\.\d+(?:[-+][\w.]+)?\b"}
    return sorted(key for key, pattern in rules.items() if re.search(pattern, text))
