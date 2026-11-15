"""Narrow non-person contexts for the confident English prompt path.

Only structural field labels, organization suffixes/lists, work context and
OEWN ethnogeographic adjectives are used; no person or entity gazetteer.
"""
from functools import lru_cache
import json, re
from pathlib import Path

from .name_model_g import classes
from .name_model import tokens

MECHANISMS = frozenset(('labels', 'companies', 'works', 'nationality'))
_SUFFIXES = frozenset('corp inc ltd llc gmbh partners group bank press labs systems holdings'.split())
_TITLES = re.compile(r'(?i)\b(?:mr|mrs|ms|dr|prof|sir|lady|lord|miss)\.?[ \t]+$')
_TITLED_START = re.compile(r'(?i)(?:mr|mrs|ms|dr|prof|sir|lady|lord|miss)\b')
_LIST = re.compile(r'[ \t]*(?:,[ \t]*(?:and[ \t]+)?|and[ \t]+)[ \t]*')
_FIRMS = re.compile(r'(?i)\b(?:firms|vendors|clients)(?:[ \t]+(?:including|such as))?[ \t:]+$')
_WORK = re.compile(r'(?i)\b(?:reading|read|translations?[ \t]+(?:of|for)|edition[ \t]+of|novel|book|film)[ \t]+$')
_WORK_AFTER = re.compile(r'(?i)^[ \t]+(?:novel|book|film|demo)\b|^[ \t]+is[ \t]+(?:a|an)[ \t]+(?:novel|book|film)\b')
_LABEL_RE = re.compile(r'(?m)^[ \t]*(?P<label>[^\W\d_][\w-]*(?:[ \t]+[^\W\d_][\w-]*)?)[ \t]*:')
_REPLY_RE, _VERB_MATCH_RE = re.compile(r'[ \t]*([A-Za-z]+)\b'), re.compile(r'[ \t]+([a-z]+)\b')
_GREET_LABEL_RE = re.compile(r'(?i)\b(?:dear|hi)[ \t]+(?P<label>[A-Za-z]+)[ \t]*,')
_OWNER_ROLE_RE = re.compile(r'(?im)^[ \t]*owner[ \t]*:[ \t]*(?P<role>[A-Z]{2,}[ \t]+On-Call)\b')
_RUN_GAP_RE, _CO_END_RE = re.compile(r'[ \t]+|[ \t]*&[ \t]*'), re.compile(r'(?i)&[ \t]*Co$')
_REPLY_WORDS = frozenset(('i', 'you', 'we', 'he', 'she', 'they', 'can', 'could', 'please', 'hi', 'hello'))
_COPULA_WORDS = frozenset(('is', 'are', 'was', 'were', 'has', 'have', 'had'))
_GREET_KEEP_SEM = frozenset(('noun.person', 'noun.animal', 'noun.plant', 'noun.time'))
_DET_WORDS = frozenset(('your', 'his', 'her', 'their', 'our', 'my', 'the', 'a', 'an'))


@lru_cache(maxsize=1)
def _nationalities():
    return frozenset(json.loads((Path(__file__).parent / 'models/name_nationality.json').read_text())['words'])


def _noun(word):
    return 'n' in classes(word.lower())[0]


def _person_verb(after):
    match = _VERB_MATCH_RE.match(after)
    return bool(match and match[1] not in _COPULA_WORDS
                and any(k.startswith('verb.') for k in classes(match[1])[1]))


def filter_spans(text, names, enabled=MECHANISMS):
    """Subtract only explicitly non-person ranges; preserve adjacent person tokens."""
    if not names or not enabled:
        return names
    toks = tokens(text)
    blocked = []
    if 'labels' in enabled:
        if ':' in text:
            for m in _LABEL_RE.finditer(text):
                ls, le = m.span('label')
                if not any(ls < ne and ns < le for ns, ne, _ in names):
                    continue
                words = m['label'].split()
                reply = _REPLY_RE.match(text[m.end():])
                if reply and (reply[1].lower() in _REPLY_WORDS or _person_verb(' ' + reply[1].lower())):
                    continue
                if all(_noun(w) or w.lower() in {'addr', 'attn', 'prev'} or '-' in w for w in words):
                    blocked.append((ls, le))
            for m in _OWNER_ROLE_RE.finditer(text):
                blocked.append(m.span('role'))
        if ',' in text:
            for m in _GREET_LABEL_RE.finditer(text):
                ls, le = m.span('label')
                if not any(ls < ne and ns < le for ns, ne, _ in names):
                    continue
                sem = classes(m['label'].lower())[1]
                nouns = {k: v for k, v in sem.items() if k.startswith('noun.')}
                if (nouns and sem.get('noun.act', 0) > max((v for k, v in nouns.items() if k != 'noun.act'), default=0)
                        and not _GREET_KEEP_SEM & sem.keys()):
                    blocked.append((ls, le))

    runs = []
    if 'companies' in enabled or 'works' in enabled:
        for s, e, word in toks:
            if not word[:1].isupper():
                continue
            previous = text[runs[-1][0]:runs[-1][1]].split()[-1] if runs else ''
            gap = text[runs[-1][1]:s] if runs else ''
            if runs and (_RUN_GAP_RE.fullmatch(gap)
                         or gap == '. ' and (len(previous) == 1 or _TITLES.fullmatch(previous+'. '))):
                runs[-1] = (runs[-1][0], e)
            else:
                runs.append((s, e))

    if 'companies' in enabled:
        def company(run):
            value = text[run[0]:run[1]]
            if _TITLED_START.match(value):
                return False
            words = tokens(value)
            return len(words) > 1 and words[-1][2].lower() in _SUFFIXES or bool(_CO_END_RE.search(value))
        i = 0
        while i < len(runs):
            j = i
            while j+1 < len(runs) and _LIST.fullmatch(text[runs[j][1]:runs[j+1][0]]):
                j += 1
            group = runs[i:j+1]
            if any(any(rs < ne and ns < re_ for ns, ne, _ in names) for rs, re_ in group):
                if bool(_FIRMS.search(text[max(0, group[0][0]-50):group[0][0]])) or any(company(r) for r in group):
                    for s, e in group:
                        if not _TITLES.search(text[max(0, s-12):s]) and not _TITLED_START.match(text[s:e]):
                            blocked.append((s, e))
            i = j+1

    if 'works' in enabled:
        for index, (s, e) in enumerate(runs):
            before, after = text[max(0, s-80):s], text[e:e+40]
            opener = before[-1:]
            closer = {'"': '"', '“': '”', '‘': '’', "'": "'", '*': '*', '_': '_'}.get(opener)
            quoted = bool(closer and after.startswith(closer))
            left, right = (before[:-1] if quoted else before), (after[1:] if quoted else after)
            if not _person_verb(right) and not _TITLES.search(before) and not _TITLED_START.match(text[s:e]) and (
                    _WORK.search(left) or _WORK_AFTER.match(right)):
                blocked.append((s, e))
                k = index + 1
                while k < len(runs) and _LIST.fullmatch(text[e:runs[k][0]]):
                    a, b = runs[k]
                    if _person_verb(text[b:]):
                        break
                    blocked.append((a, b))
                    e, k = b, k+1

    if 'nationality' in enabled:
        nat_set = _nationalities()
        for i, (s, e, word) in enumerate(toks[:-1]):
            if not word[:1].isupper() or word.lower() not in nat_set:
                continue
            a, b, following = toks[i+1]
            if (not text[e:a].isspace() or '\n' in text[e:a]
                    or _TITLES.search(text[max(0, s-12):s]) or _person_verb(text[b:])):
                continue
            pos, sem = classes(following.lower())
            if ('n' in pos and following.islower() and 'noun.person' not in sem
                    and not any(key.startswith('v') for key in pos)
                    or following.isupper() and len(following) > 1):
                if i and toks[i-1][2].lower() in _DET_WORDS and text[toks[i-1][1]:s].isspace():
                    s = toks[i-1][0]
                blocked.append((s, b))

    result = []
    for s, e, kind in names:
        pieces = [(s, e)]
        for a, b in blocked:
            pieces = [(c, d) for x, y in pieces
                      for c, d in ((x, min(a, y)), (max(b, x), y)) if c < d]
        for a, b in pieces:
            while a < b and text[a].isspace():
                a += 1
            while b > a and text[b-1].isspace():
                b -= 1
            if a < b:
                result.append((a, b, kind))
    return result
