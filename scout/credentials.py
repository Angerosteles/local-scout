"""Password-like values that have no known format (``use IkXzYx33@$``).

Every whitespace token gets a confidence from its shape (character classes, length, entropy) and nearby cue
words (``password``, ``pwd``, ``pin``, ``unlock`` …, tolerant of one typo). Fixed exclusions keep URLs,
domains, paths, hashes, UUIDs, versions, dates and command-line flags out. Thresholds depend on the source:
terminal output is full of identifiers, so it needs more evidence than a prompt or an e-mail.
Code syntax and labelled business identifiers are excluded; weak shapes need a nearby credential label.
Cues stay within their field, and identifier fragments count as cues only in assignment labels.

* score >= high: masked.
* low <= score < high: masked only when a local basal engine is registered and says the line holds a usable
  credential; then up to ``NEIGHBOURS`` lines above and below are asked too, because a credential block
  ("login: …" / "password: …") often spans lines.
* Without basal, or when it fails or the request's time budget is spent, only the high threshold applies.

The guards set ``CONTEXT`` (source, confirm, state) and ``MEMO``; everything else gets the deterministic text
thresholds. A JSON field name reaches the detectors as a "name: " prefix added by ``privacy.mask_local_details``.
Nothing here stores or logs text.
"""
import bisect
import math
import re
import time
from contextvars import ContextVar

CONTEXT = ContextVar('local_scout_credential_context', default=None)
# Remembered basal answers for one message or tool result (set by the guards): a message scanned as a whole and
# block by block gets the same answers, while another message with the same value is judged in its own context.
MEMO = ContextVar('local_scout_credential_memo', default=None)

# (low, high), chosen on the Gretel validation split and checked on TechQA and tool-call results (see docs/EVIDENCE.md).
THRESHOLDS = {'prompt': (0.45, 0.55), 'text': (0.50, 0.60), 'terminal': (0.65, 0.75)}
CONFIRM_AT = 0.5        # basal probability that confirms a line
NEIGHBOURS = 1          # lines above and below asked after the engine confirmed a line
MAX_CALLS = 6           # basal calls per text; then deterministic only
TIME_BUDGET = 8.0       # seconds of basal calls per tool result or prompt (shared deadline, see basal.credential_checker)
WINDOW = 300            # characters of context around a candidate sent to the engine
MIN_LEN, MAX_LEN = 6, 20

STRONG = ('password', 'passwd', 'passphrase', 'passcode', 'pwd', 'pass', 'pin', 'haslo', 'hasło', 'credentials',
          'credential', 'login', 'secret', 'pword', 'token')
MEDIUM = ('code', 'key', 'access', 'otp', 'unlock')
ACTION = ('use', 'enter', 'sign', 'log', 'open', 'try')

# Tokens stop at whitespace, quotes, angle brackets (XML tags) and JSON escapes (\\n, \\u00b0 …).
_TOKEN = re.compile(r'(?:\\(?![nrtu"\\/])|[^\s"<>\\])+')
_WORD = re.compile(r"[^\W\d_][\w]*", re.UNICODE)
_LEAD = '\'"`([{<:='
_TRAIL = '\'"`.,;:}]'
_WEAK_SYMBOLS = set("'-_./,")      # word punctuation; does not make a token password-like
# Digits-only values, by their norms: one-time / 2FA codes are 6 or 8 digits (TOTP/HOTP, RFC 6238/4226; SMS codes
# 4-8), PINs 4-6. They get their own placeholders, [CODE] and [PIN]; a bare "code" is only uncertain.
OTP_CUES = ('otp', '2fa', 'mfa', 'totp', 'hotp', 'verification', 'one-time', 'onetime', 'authenticator', 'passcode')
# Count only together with "code": "Social Security Number: 144917" is not a one-time code.
OTP_WEAK_CUES = ('verify', 'authentication', 'auth', 'login', 'signin', 'sign-in', 'security', 'confirmation',
                 'confirm', 'sms', 'access', 'unlock', 'door', 'entry')
PIN_CUES = ('pin',)
NUMERIC_PASSWORD_CUES = ('password', 'passwd', 'pwd', 'haslo', 'hasło')
NOT_SECRET_CODE = ('error', 'status', 'exit', 'return', 'response', 'http', 'zip', 'postal', 'post', 'area', 'country',
                   'product', 'promo', 'promotional', 'discount', 'coupon', 'voucher', 'material', 'reason', 'event',
                   'item', 'tracking', 'sku', 'hs', 'tariff', 'customs', 'iso', 'swift', 'bic', 'sort', 'routing',
                   'branch', 'department', 'course', 'class', 'room', 'gate', 'flight', 'order', 'invoice',
                   'reference', 'ref', 'case', 'ticket', 'zone', 'region', 'colour', 'color', 'qr', 'bar', 'barcode',
                   'source', 'program', 'sample', 'example', 'test', 'dummy')
_FILLER = ('is', 'was', 'the', 'your', 'my', 'a', 'an', 'number', 'no', 'of', 'this', 'new', 'here', 'below', 'to',
           'for', 'use', 'enter', 'with', 'as', 'it', 's')
_CONTEXT_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")
_EXCLUDE = [
    re.compile(r'(?i)://|^www\.|\.(?:com|org|net|io|pl|de|uk|eu|gov|edu|dev|app|ai|co|info|biz|me|us)(?:[/:?#]|$)'),
    re.compile(r'(?i)^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$'),          # e-mail (its own detector)
    re.compile(r'^(?:[~.]{0,2}/|[A-Za-z]:\\)|[/\\].*[/\\]'),           # paths
    re.compile(r'(?i)^(?:sha\d*:|md5:)?[0-9a-f]{7,}$'),                 # hashes, git SHAs, digests
    re.compile(r'(?i)^[0-9a-f]{2}(?:[:-][0-9a-f]{2}){3,}$'),            # MAC-like
    re.compile(r'(?i)^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$'),   # UUID
    re.compile(r'^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}.*)?$|^\d{1,2}:\d{2}(?::\d{2})?'),  # dates, times
    re.compile(r'^v?\d+(?:\.(?:\d+|x|\*))+[a-z0-9-]*$'),                # versions (1.2.3, 1.1.5.x)
    re.compile(r'^(?:CVE|CWE|GHSA|RHSA|USN|DSA|KB)-|^[A-Z]{2,10}-\d+(?:-\d+)*$'),  # advisories, ticket IDs
    re.compile(r'^-{1,2}[A-Za-z]'),                                     # command-line flags
    re.compile(r'^\[[A-Z_]+\](?:#\d+)?$'),                              # Scout placeholders
    re.compile(r'^\$\{?\w+\}?$|^%\w+%$'),                               # variables
    re.compile(r'^[A-Za-z][\w.-]*/\d|^[A-Za-z]+/[A-Za-z]+$'),            # Mozilla/5.0, Read/Write
    re.compile(r'^\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?'),                    # IPv4[:port]
    re.compile(r'^0x[0-9A-Fa-f]+$'),                                     # hex literals
    re.compile(r'^\+?\(?\d{1,4}\)?[\d\-.()]{6,}(?:x\d+)?$'),                # phone numbers (their own detector)
    re.compile(r'^[\w-]+\.[A-Za-z][A-Za-z0-9]{0,4}$'),                   # file.ext
    re.compile(r'\]\(|!\['),                                            # markdown links and images
    re.compile(r'&(?:#\d+|[a-zA-Z]+);'),                                  # HTML entities
    re.compile(r'^[A-Z][A-Za-z0-9]{0,5}/[A-Z][A-Za-z0-9]{0,5}$'),          # L2TP/IPSec, TCP/IP, I/O
    re.compile(r'^[\d\W_]+$'),                                          # digits and punctuation only
    re.compile(r'=$|\w\([^)]*=|^[~$]?\{\w|\(\?[:=!<]'),                   # open assignments, keyword calls, f-strings, regex groups
    re.compile(r'^~?\d+(?:\.\d+)?/[A-Za-z]+$'),                           # rates: ~6/sec
    re.compile(r'(?:^|[\s(\[.,=+\-*/:])[a-z][a-z0-9_]{2,}[\[(]|[a-z_]{2,}\.[a-z_]{2,}\(|^(?=.*[a-z_]{3,}).*[)\]][.\[(+\-*/,][A-Za-z_0-9(]'),  # code expressions: item[0], x).encode(, 0)+value
]
# Code notation. Applied only to tokens with fewer than three distinct symbols: a call such as foo(bar1) has one or
# two kinds, while a generated password packs many (#pt(1hy)hW_3BF+1N2).
_NOTATION = [
    re.compile(r'%[0-9A-Fa-f]{2}'),                                      # URL encoding
    re.compile(r'\w\([^()]*\)|\w\[\w*\]|->|::|=>'),                       # calls with arguments, indexing, operators
    re.compile(r'^[A-Za-z_][\w.]*[\[(]|^[A-Za-z_][\w.]*[)\]]+$'),         # incomplete calls, type annotations, closing syntax
    re.compile(r'\{\{?\w+\}\}?|%[sdif]\b|\$\d|\$\{'),                       # templates and format strings
    re.compile(r'``|:[a-z]+:`'),                                         # joined Markdown spans and reStructuredText roles
    re.compile(r'\\[xuUnrt0]|\w\|\w|^[~$]?\{\w|%[-+ 0#]*\d*(?:\.\d+)?[sdifgxXeEc%]'),  # escapes, regex alternation, f-strings, formats
    re.compile(r'\w\([^)]*=|^[A-Za-z0-9_.]+/[A-Za-z0-9_.]+$'),             # calls with keyword arguments, word/word (argon2/bcrypt)
]

# Product names and labels (Windows11, Python3.11, A320neo, COVID-19): count only next to a strong cue.
_SOFT = re.compile(r'^[A-Za-z][A-Za-z+-]*\d+(?:\.\d+)*[A-Za-z]{0,3}$|^[A-Za-z]+-\d+$|^\d+-[A-Za-z]+$'
                   r'|^[A-Z]+(?:-[A-Z0-9]+)+$|^[A-Z0-9]{4,}\??$'
                   r'|^(?=[A-Za-z0-9]*_)[A-Za-z0-9_]+$'                # identifiers: tempSensor_001, JaneDoe_456
                   r'|^~?[A-Za-z0-9]+(?:-[A-Za-z0-9.]+)+$')             # hyphenated labels: 18-char, 1M-context

# Field boundaries keep a credential cue from leaking into the next JSON member, table cell or sentence.
_CONTEXT_BOUNDARY = re.compile(r'[,;|{}\n]|[.!?]\s+')
_FIELD = re.compile(r'''["']?([A-Za-z_][\w.-]*)["']?\s*[:=]\s*["'`]*$''')
_ID_LABEL = re.compile(
    r'(?i)\b(?:employee(?:[ _-]+id)?|customer(?:[ _-]+id)?|user[ _-]*name|user|'
    r'medical[ _-]+record[ _-]+number|record[ _-]+id|order[ _-]+id|ticket[ _-]+id|'
    r'alert[ _-]+id|postcode|credit[ _-]+card(?:[ _-]+number)?|ccn)\s*["\'`:=#-]*\s*$')
_BUSINESS_ID = re.compile(r"^(?:[A-Z]{1,12}\d+[A-Z]?|[A-Za-z]{1,8}-\d+(?:-[A-Za-z0-9]+)*|"
                          r"[A-Z]\d+(?:-[A-Za-z0-9]+)+)(?:'s)?$")
_PASSWORD_LABEL = ('password', 'passwd', 'passphrase', 'pwd', 'haslo', 'hasło', 'pword')
_SECRET_LABEL = _PASSWORD_LABEL + ('secret',)
_AUTH_TOKEN_LABEL = re.compile(r'(?i)\b(?:auth(?:orization|entication)?|access|refresh|session|bearer|api) '
                               r'(?:token|key)\s*$')
_SYNTAX = re.compile(r'^[A-Za-z_][\w.]*[\[(][\w.\[\](),:-]*$|^\d+[)\]:][\w.()\[\]:-]*$'
                     r'|^0x[0-9A-Fa-f]+[)\]]+$')
_INLINE_CODE = re.compile(r'^[([{]*(`{1,3})([^`]+)\1[.,;:)\]}\u2026]*$')
_FIELD_NAME_END = re.compile(r'''["']\s*:''')


def _near_context(before):
    """Bounded context from the current field; split identifiers only when they label an assigned value."""
    before = re.sub(r''',\s*(["'`]*)\s*$''', r' \1', before[-80:])
    before = _CONTEXT_BOUNDARY.split(before)[-1]
    field = _FIELD.search(before)
    if field:
        return before[:field.start()] + field[1].replace('_', ' ').replace('-', ' ')
    return before


def _direct_cue(before, cues):
    """Weak identifier-like shapes need a credential label, not a distant mention of credentials."""
    words = [w.lower() for w in _WORD.findall(before) if w.lower() not in _FILLER]
    return bool(words and _is_cue(words[-1], cues))


_LETTERS = 'abcdefghijklmnopqrstuvwxyz'


def _one_edit(word):
    """Every string one deletion, transposition, substitution or insertion away from word."""
    splits = [(word[:i], word[i:]) for i in range(len(word) + 1)]
    out = {a + b[1:] for a, b in splits if b}
    out |= {a + b[1] + b[0] + b[2:] for a, b in splits if len(b) > 1}
    out |= {a + c + b[1:] for a, b in splits if b for c in _LETTERS}
    out |= {a + c + b for a, b in splits for c in _LETTERS}
    return out


# Cues that also start compound words naming the same thing (passwordHash, secretKey, credentialsFile).
_COMPOUND = ('password', 'passwd', 'passphrase', 'passcode', 'credential', 'secret')


_PLURAL_CUES = {'password', 'passphrase', 'passcode', 'credential', 'secret', 'code', 'pin'}


def _vocabulary(cues):
    """Exact cues and their plurals, plus one-typo variants of cues with five or more letters (built once)."""
    # One-typo variants only for cues of six or more letters: five-letter ones collide with ordinary words
    # (token/taken, login/logic, pword/word) and passwd with "passed". Plurals only where they keep the meaning
    # ("tokens" in LLM text counts context, not credentials).
    exact = set(cues) | {c + 's' for c in cues if c in _PLURAL_CUES}
    fuzzy = set().union(*(_one_edit(c) for c in cues if len(c) >= 6 and c != 'passwd')) if cues else set()
    prefixes = {c for c in cues if c in _COMPOUND}
    return exact | fuzzy, prefixes, sorted({len(c) for c in prefixes})


_VOCAB = {}


def _is_cue(word, cues):
    vocab = _VOCAB.get(cues)
    if vocab is None:
        vocab = _VOCAB[cues] = _vocabulary(cues)
    known, prefixes, lengths = vocab
    word = word.lower()
    return word in known or any(word[:n] in prefixes for n in lengths if len(word) > n)


def _cue_score(near):
    words = _WORD.findall(near[-48:])
    best = 0.0
    for word in words:
        if _is_cue(word, STRONG):
            return 0.40
        if _is_cue(word, MEDIUM):
            best = max(best, 0.25)
        elif word.lower() in ACTION:
            best = max(best, 0.12)
    return best


def _clean(text, start, end):
    """Strip wrapping quotes/brackets, markdown emphasis and trailing sentence punctuation; returns the inner span."""
    inline = _INLINE_CODE.fullmatch(text[start:end])
    if inline:
        return start + inline.start(2), start + inline.end(2)
    for _ in range(3):
        while start < end and text[start] in _LEAD:
            start += 1
        while end > start and text[end - 1] in _TRAIL:
            end -= 1
        if text.startswith('**', start) or text.startswith('__', start):
            start += 2
        if end - start >= 2 and text[end - 2:end] in ('**', '__'):
            end -= 2
        if end - start >= 3 and text[end - 2:end].lower() in ("'s", '`s', '’s'):
            end -= 2                                   # possessive: CUST2150050's
        if end - start >= 3 and text[start] == '*' and text[end - 1] == '*':
            start, end = start + 1, end - 1            # *emphasis*
    if end > start and text[end - 1] in ')]}' and text[start - 1:start] in ('(', '[', '{'):
        end -= 1
    return start, end


def _split_assignment(text, start, end):
    """``KEY=value`` / ``pwd:value``: return (value start, cue text from the key)."""
    token = text[start:end]
    for sep in ('=', ':'):
        if sep in token:
            key, _, value = token.partition(sep)
            if key and value and re.fullmatch(r'[A-Za-z_][\w.-]*', key):
                return start + len(key) + 1, key.replace('_', ' ')
    return start, ''


def shape_score(value):
    """0 for ordinary words, None for excluded forms, else a shape score (classes, entropy, interleaving)."""
    lower = any(c.islower() for c in value)
    upper = any(c.isupper() for c in value)
    digit = any(c.isdigit() for c in value)
    # A symbol counts when it is not word punctuation and not only a trailing mark ("userID?", "Done)").
    symbol = any(not c.isalnum() and c not in _WEAK_SYMBOLS for c in value[:-1])
    classes = lower + upper + digit + symbol
    if classes >= 3:
        score = 0.35 + 0.10 * (classes - 3)
    elif classes == 2 and digit and (lower or upper):
        score = 0.20
    elif classes == 2 and symbol and (lower or upper):
        score = 0.10
    else:
        return 0.0
    if any(rule.search(value) for rule in _EXCLUDE):
        return None
    if _SYNTAX.fullmatch(value):
        return None
    if len({c for c in value if not c.isalnum() and c not in _WEAK_SYMBOLS}) < 3 and any(
            rule.search(value) for rule in _NOTATION):
        return None
    counts = {c: value.count(c) for c in set(value)}
    bits = -sum(n * math.log2(n / len(value)) for n in counts.values())
    if bits >= 30:
        score += 0.10
    if re.search(r'[A-Za-z]\d+[A-Za-z]|\d[A-Za-z]+\d', value):
        score += 0.10
    return score


_INTERESTING = re.compile(r'[^\W\d_]').search


def _code(value, before):
    """(kind, score) for a digits-only value by its context and length norms, or None."""
    if not 4 <= len(value) <= 8:
        return None
    before = _near_context(before)
    if _ID_LABEL.search(before):
        return None
    words = [w.lower() for w in _CONTEXT_WORD.findall(before[-60:]) if w.lower() not in _FILLER][-4:]
    if not words or any(w in NOT_SECRET_CODE for w in words[-3:]):
        return None
    near = words[-3:]
    if any(_is_cue(w, PIN_CUES) for w in near) and len(value) <= 6:
        return 'pin', 0.85
    if any(_is_cue(w, NUMERIC_PASSWORD_CUES) for w in near):
        return 'secret', 0.85
    if any(_is_cue(w, OTP_CUES) for w in words) or (
            any(w in ('code', 'codes', 'kod') for w in words) and any(_is_cue(w, OTP_WEAK_CUES) for w in words)):
        return 'code', 0.85 if len(value) in (6, 8) else 0.65
    if words[-1] in ('code', 'kod', 'codes'):
        return 'code', 0.50          # uncertain: "code 4821" can be anything; basal decides
    return None


def _policy(value, cue):
    """Typical password rules (8+ characters, three character classes): values that miss them lose some
    confidence, unless a strong cue stands right next to them. Meeting them adds nothing (IDs meet them too)."""
    classes = (any(c.islower() for c in value) + any(c.isupper() for c in value) + any(c.isdigit() for c in value)
               + any(not c.isalnum() and c not in _WEAK_SYMBOLS for c in value[:-1]))
    if len(value) >= 8 and classes >= 3:
        return 0.0
    if cue >= 0.40:
        return 0.0
    return -0.10


def candidates(text):
    """Yield (start, end, score, line_number, kind) for password-like tokens; kind is secret, code or pin."""
    line_starts = None
    for match in _TOKEN.finditer(text):
        token = match.group()
        # Fast path (C-level checks): plain words, short tokens and long blobs cannot score.
        if token.isalpha() or len(token) < 4 or len(token) > MAX_LEN + 8:
            continue
        digits_only = token.strip(_LEAD + _TRAIL).isdigit()
        if not digits_only and not _INTERESTING(token):
            continue
        start, end = _clean(text, *match.span())
        if text[start - 1:start] in ('"', "'") and _FIELD_NAME_END.match(text, end):
            continue
        value_start, _ = _split_assignment(text, start, end)
        if value_start > start:            # "key:`value`": clean the value part as well
            value_start, end = _clean(text, value_start, end)
        value = text[value_start:end]
        if line_starts is None:
            line_starts = [0] + [m.end() for m in re.finditer('\n', text)]
        line = bisect.bisect_right(line_starts, match.start()) - 1
        before = text[line_starts[line]:value_start]
        if value.isdigit():
            found = _code(value, before)
            if found:
                yield value_start, end, found[1], line, found[0]
            continue
        if not MIN_LEN <= len(value) <= MAX_LEN or value.isalpha():
            continue
        shape = shape_score(value)
        if shape is None or shape == 0.0:
            continue
        near = _near_context(before)
        if _ID_LABEL.search(near) and all(c.isalnum() or c in _WEAK_SYMBOLS for c in value):
            continue
        if _BUSINESS_ID.fullmatch(value) and not (
                _direct_cue(near, _SECRET_LABEL) or _AUTH_TOKEN_LABEL.search(near)):
            continue
        cue = _cue_score(near)
        if _SOFT.match(value):
            if cue < 0.40 or not _direct_cue(near, STRONG):
                continue
            shape = min(shape, 0.20)
        yield value_start, end, round(max(0.0, min(1.0, shape + cue + _policy(value, cue))), 4), line, 'secret'


def _best_token(text, lo, hi):
    """Most password-like token on a line that basal flagged without a candidate (minimal shape)."""
    best = None
    for start, end, _, _, kind in candidates(text[lo:hi]):
        if kind != 'secret':
            continue
        shape = shape_score(text[lo + start:lo + end])
        if shape and shape >= 0.20 and (best is None or shape > best[2]):
            best = (lo + start, lo + end, shape)
    return best


def spans(text):
    """Spans (start, end, kind) to mask, using the source and optional basal check from CONTEXT."""
    if len(text) < MIN_LEN - 2:
        return []
    context = CONTEXT.get() or ('text', None)
    source, confirm = context[0], context[1]
    state = context[2] if len(context) > 2 else None    # {'degraded': bool}: basal failed or ran out of budget
    low, high = THRESHOLDS.get(source, THRESHOLDS['text'])
    found = list(candidates(text))
    if not found:
        return []
    result = [(a, b, kind) for a, b, score, _, kind in found if score >= high]
    band = [(a, b, line, kind) for a, b, score, line, kind in found if low <= score < high]
    if confirm is None or not band:
        return result
    lines = text.split('\n')
    starts = [0]
    for item in lines[:-1]:
        starts.append(starts[-1] + len(item) + 1)
    budget = [MAX_CALLS]
    deadline = time.monotonic() + TIME_BUDGET
    asked = MEMO.get()
    if not isinstance(asked, dict):
        asked = getattr(confirm, 'memo', None)        # one tool result: the checker's own memo
    if not isinstance(asked, dict):
        asked = {}

    def probability(segment, key=None):
        """Engine probability for one piece of text (cached); None when the budget is spent or it failed."""
        key = key or ('line', segment)
        if key in asked:
            if asked[key] is None and state is not None:
                state['degraded'] = True     # remembered failure: the result must not be cached either
            return asked[key]
        if not segment.strip():
            return None
        if budget[0] <= 0 or time.monotonic() >= deadline:
            if state is not None:
                state['degraded'] = True
            return None
        if ('segment', segment) in asked:      # two candidates in the same window: one question
            asked[key] = asked[('segment', segment)]
            return asked[key]
        budget[0] -= 1
        try:
            asked[key] = asked[('segment', segment)] = confirm(segment)
        except Exception:
            budget[0] = 0      # engine failed: deterministic only for the rest of this text
            asked[key] = asked[('segment', segment)] = None
            if state is not None:
                state['degraded'] = True
        return asked[key]

    def line_span(index):
        return starts[index], starts[index] + len(lines[index])

    confirmed_lines = set()
    for a, b, line, kind in band:
        lo, hi = line_span(line)
        # The engine sees the candidate with up to WINDOW characters around it, never past its own line.
        p = probability(text[max(lo, a - WINDOW):min(hi, b + WINDOW)], ('value', text[a:b]))
        if p is not None and p >= CONFIRM_AT:
            result.append((a, b, kind))
            confirmed_lines.add(line)
    for line in sorted(confirmed_lines):
        for other in range(max(0, line - NEIGHBOURS), min(len(lines), line + NEIGHBOURS + 1)):
            if other in confirmed_lines:
                continue
            lo, hi = line_span(other)
            p = probability(text[lo:min(hi, lo + 2 * WINDOW)])
            if p is not None and p >= CONFIRM_AT:
                pick = [(a, b, kind) for a, b, l, kind in band if l == other]
                if not pick:
                    best = _best_token(text, lo, hi)
                    pick = [(best[0], best[1], 'secret')] if best else []
                result.extend(pick)
    return result
