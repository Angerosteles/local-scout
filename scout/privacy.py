"""Local span detector. No matched values are persisted or logged."""
import re
from datetime import date

from . import credentials, pii_model

# Regex detectors. Phones, cards, SSNs and addresses come from the context model in pii_model.
PATTERNS = {
    "pesel": re.compile(r"(?<!\d)\d{11}(?!\d)"),
    "email": re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    "secret": re.compile(r"(?i)(?:\bsk-[\w-]{6,}|\b(?:api[_-]?key|password|token|secret)\s*[:=]\s*(?!true\b|false\b|null\b|none\b|\d+\b)(?![^\s]*\()\S{6,})"
                        # Well-known credential formats (high-precision prefixes) and bearer tokens / private key blocks.
                        r"|\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}"
                        r"|\bgh[pousr]_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{40,}"
                        r"|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b|\bxox[abprs]-[A-Za-z0-9-]{10,}|\bAIza[0-9A-Za-z_-]{35}"
                        r"|\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
                        r"|(?i:\bbearer)\s+[A-Za-z0-9._~+/-]{20,}=*"
                        r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"),
    # Card verification code: only next to an explicit keyword; the span covers the digits only.
    "cvv": re.compile(r"(?i)\b(?:cvv2?|cvc2?|cid|csc|security\s+code|card\s+verification(?:\s+(?:code|value))?)\b\s*(?:code|number|no\.?)?\s*[:#=]?\s*(?P<cvv>\d{3,4})\b")}


def _iban_valid(value):
    compact = re.sub(r"\s+", "", value).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", compact):
        return False
    digits = "".join(str(ord(ch) - 55) if ch.isalpha() else ch for ch in compact[4:] + compact[:4])
    remainder = 0
    for char in digits:
        remainder = (remainder * 10 + int(char)) % 97
    return remainder == 1


PATTERNS["iban"] = re.compile(r"(?<![A-Z0-9])(?P<iban>[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30})", re.I)
IBAN_LENGTHS = {'PL':28,'DE':22,'GB':22,'FR':27,'ES':24,'IT':27,'NL':18,'BE':16,'CH':21,'AT':20,'IE':22,'PT':25,'DK':18,'NO':15,'SE':24,'FI':18,'GR':27,'LU':20,'CZ':24,'HU':28,'RO':24,'TR':26}


# an address span usually covers only the street line ("07570 Joanna Mountains"); extend it
# over the adjoining unit / postcode / city / state segments so the whole address is masked
# (Gretel test full coverage was 35.8%). Bounded: at most four segments, only after ", " (or a space
# before a postcode/state), Title-case place names of up to four words.
_ADDR_UNIT = r"(?:Apt\.?|Apartment|Suite|Ste\.?|Unit|Flat|Floor|Fl\.|Room|Rm\.?|#|H\.?\s?No\.?|No\.)\s*[A-Za-z0-9-]{1,8}"
_ADDR_POST = r"(?:\d{3,6}(?:-\d{4})?|[A-Z]\d[A-Z]\s?\d[A-Z]\d|[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2})"
_ADDR_FIELD_LABELS = r"(?!(?:Secret|Token|Password|Passcode|Card|Credit|Phone|Mobile|Email|E-mail|SSN|Name|Account|Number|Key|Api|IBAN|Pin|Dob|Date)\b)"
_ADDR_PLACE = _ADDR_FIELD_LABELS + r"(?:(?:[A-Z]{2,3}\s)?[A-Z][a-z'\-]+)(?:\s[A-Z][a-z'\-]+){0,3}(?:\s(?:[A-Z]{2,3}))?(?:\s" + _ADDR_POST + r")?|[A-Z]{2}\b"
_ADDR_NEXT = re.compile(r"(?:,\s?(?:" + _ADDR_UNIT + "|" + _ADDR_POST + "|" + _ADDR_PLACE + r")|\s(?:" + _ADDR_UNIT + "|" + _ADDR_POST + r"))")
_ADDR_PREV = re.compile(r"(?:" + _ADDR_UNIT + r"),?\s?$")
_ADDR_KEYWORD_PREV = re.compile(r"(?:Apt\.?|Apartment|Suite|Studio|Ste\.?|Unit|Flat|Room|H\.?\s?No\.?)\s?$")
_ADDR_TOKEN_TAIL = re.compile(r"[A-Za-z0-9]+(?:\s\d[A-Z]{2}\b)?")
_ADDR_UNIT_NUMBER = re.compile(r"\.?\s?(?:H\.?\s?No\.?\s?)?[A-Za-z0-9][A-Za-z0-9/\-]{0,7}")
_ADDR_ENDS_WITH_UNIT = re.compile(r"(?:Apt|Apartment|Suite|Studio|Unit|Flat|Room)\.?$")
_ADDR_STREET_PREV = re.compile(r"\d{1,6}\s[A-Z][a-z]+(?:\s[A-Za-z]+){0,3},\s?$")


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
        stop = match.end()
    while stop > start and text[stop - 1] in '.,; ':
        stop -= 1
    return start, stop


def spans(text):
    result = []
    has_digit = bool(re.search(r'\d', text))
    lowered = text.lower()
    has_secret_hint = any(hint in lowered for hint in ('sk-', 'api_key', 'api-key', 'apikey', 'password', 'token', 'secret', '_live_', '_test_', 'ghp_', 'gho_', 'ghu_', 'ghs_', 'ghr_', 'github_pat_', 'akia', 'asia', 'xox', 'aiza', 'eyj', 'bearer', 'private key'))
    for category, pattern in PATTERNS.items():
        if category == 'email' and '@' not in text or category == 'pesel' and not has_digit:
            continue
        if category == 'secret' and not has_secret_hint:
            continue
        for match in pattern.finditer(text):
            if category == "iban":
                country = match.group('iban')[:2].upper()
                length = IBAN_LENGTHS.get(country)
                raw = match.group('iban')
                if not length:
                    continue
                compact = re.sub(r'\s+', '', raw)[:length]
                if not _iban_valid(compact):
                    continue
                end = match.start('iban')
                consumed = 0
                while end < match.end('iban') and consumed < length:
                    if not text[end].isspace(): consumed += 1
                    end += 1
                match_span = (match.start('iban'), end)
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
                    if not value.startswith('000000') and not re.search(r'(?i)\bpesel\s*[:=#]?\s*$',text[max(0,match.start()-24):match.start()]):
                        continue
            result.append((*match_span, category))
    result.extend(pii_model.spans(text))
    result = [(*_extend_address(text, s, e), c) if c == 'address' else (s, e, c) for s, e, c in result]
    merged = []
    for start, stop, category in sorted(set(result), key=lambda item: (item[2], item[0], item[1])):
        if merged and merged[-1][2] == category and (start <= merged[-1][1] or category == 'address' and re.fullmatch(r'[ \t,]{1,3}',text[merged[-1][1]:start])):
            prev_start, prev_stop, _ = merged[-1]
            merged[-1] = (prev_start, max(prev_stop, stop), category)
        else:
            merged.append((start, stop, category))
    return merged


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


def _normalise_for_detection(text):
    """Return (normalised text, index map to the original). Unchanged ASCII-like text is returned as is."""
    if text.isascii():
        return text, None
    import unicodedata
    out, index = [], []
    for i, ch in enumerate(text):
        if ord(ch) in _ZERO_WIDTH:
            continue
        ch = ch.translate(_HYPHENS)
        norm = unicodedata.normalize('NFKC', ch) if not ch.isascii() else ch
        for piece in norm:
            out.append(piece); index.append(i)
    clean = ''.join(out)
    return (clean, index) if clean != text else (text, None)


def mask_local_details(text, names=False, field=None):
    """Mask detected PII. names=True adds the person-name detector (used for user prompts; see name_model).
    field: name of the JSON field the text came from, used only as context for password-like values."""
    token = pii_model._CANDIDATE_BUDGET.set(pii_model.MASK_CANDIDATE_LIMIT)
    field_token = credentials.FIELD.set(field if isinstance(field, str) and field else None)
    try:
        # zero-width characters, full-width forms and Unicode hyphens hid e-mails,
        # cards and phones from every detector. Detect on a normalised copy, then map spans back to the original.
        clean, index = _normalise_for_detection(text)
        # password-like values without a known format (source thresholds, optional basal check: see credentials)
        found = spans(clean) + credentials.spans(clean)
        if names:
            from . import name_model
            found = found + name_model.spans(clean)
        if clean is not text:
            found = [(index[s], index[e - 1] + 1, c) for s, e, c in found if e > s]
        matches = sorted(found, key=lambda item: (item[0], -(item[1]-item[0])))
    finally:
        pii_model._CANDIDATE_BUDGET.reset(token)
        credentials.FIELD.reset(field_token)
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
    for start, stop, members in regions:
        label = max(members, key=lambda item: item[0])[1]
        out.extend((text[end:start], "[" + label.upper() + "]"))
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
