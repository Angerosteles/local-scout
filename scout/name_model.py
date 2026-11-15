"""Person-name detector for user prompts (pure Python).

Hashed-feature logistic token classifier exported to JSON (models/name_model.json): character n-grams,
capitalisation, context cue words and lowercase frequency in ordinary text. A token is a name above
HIGH; adjacent capitalised tokens join a detected name unless they are boundaries or known titles. Trained on the Gretel validation
split (Apache-2.0, synthetic) and measured on the unused test split plus held-out negatives — see meta
in the JSON. Detection is incomplete; titles, product names and code identifiers may be over-masked.
"""
from functools import lru_cache, wraps
from bisect import bisect_right
import json
import math
from pathlib import Path
import re
import unicodedata
import zlib
from . import language

TOKEN = re.compile(r"[^\W\d_](?:[^\W\d_]|[\u0300-\u036f])*(?:['’\-][^\W\d_](?:[^\W\d_]|[\u0300-\u036f])*)*")
_ASCII_TOKEN = re.compile(r"[A-Za-z]+(?:['’\-][A-Za-z]+)*")
MAX_TEXT = 262144
MAX_NAME_TOKENS = 5
_HONORIFICS = frozenset('pan pana panu panem panie pani panią'.split())
_PL_TITLES = frozenset('dyrektor dyrektorze prezes prezesie doktor doktorze profesor profesorze mecenas mecenasie'.split())
_PL_COMMON = frozenset('raport wyniki dziękuję proszę witam serdecznie'.split())
_PL_EXCLUDED = _PL_COMMON | _PL_TITLES
_TITLE_EVIDENCE = frozenset(('lord', 'lady', 'sir', 'dr', 'mr', 'mrs', 'ms', 'miss'))
_SENTENCE_COMMON = re.compile(r'(?im)(?:^[^\S\n]*|[.!?]\s+)(?P<word>May(?=\s+I\b)|Will(?=\s+(?:the|you|it)\b)|Mark(?=\s+the\b)|Bill(?=\s+the\b)|Rose(?=\s+from\s+the\b))')
_NON_PERSON = re.compile(r'\b(?:Pan|Pani)\s+[^\W\d_]+(?=\s+to\s+(?:tytuł|książka|powieść|utwór)\b)')
_BOUNDARIES = frozenset('the a an dear hello hi thanks president sir dr mr mrs ms miss lord lady professor in on at by from to for and or of street avenue road lane boulevard house name first last phone email address dob ssn iban card secret token password contact please can support we przy nie przekaż zaprosiłem spotkałem rozmawiałem dokument podpis kontakt pamięć ładowanie środowisko żółte'.split())
_SENT_END_RE, _COLON_BEFORE_RE = re.compile(r'[.!?\n]\s*$'), re.compile(r':\s*$')
_LODZ_WAW_RE = re.compile(r'(?i:Łodzi|Warszawie)')
_COORD_GAP_RE = re.compile(r'[ \t]*(?:,[ \t]*(?:and[ \t]+)?|and[ \t]+)')
_WORD_RE, _ALPHA_RE, _FOLLOW_RE, _ADJ_RE = re.compile(r"[^\W\d_]+"), re.compile(r'[A-Za-z]+'), re.compile(r'\s*([A-Za-z]+)'), re.compile(r'([A-Za-z]+)[ \t]+$')


@lru_cache(maxsize=1)
def _model():
    data = json.loads((Path(__file__).parent / 'models' / 'name_model.json').read_text(encoding='utf-8'))
    return (data['weights'], float(data['bias']), data['lowfreq'], frozenset(data['cues']), (1 << int(data['nbits'])) - 1,
            float(data['meta']['high']), float(data['meta']['low']))


@lru_cache(maxsize=16384)
def _h(value, mask):
    return zlib.crc32(value.encode('utf-8')) & mask


@lru_cache(maxsize=8192)
def _word_base_hashes(w, lf_bin, mask):
    b = '^' + w.lower() + '$'
    f = ['c%d:%s' % (n, b[k:k + n]) for n in (2, 3, 4) for k in range(len(b) - n + 1)]
    f += ['len:%d' % min(len(w), 12), 'allcaps' if w.isupper() and len(w) > 1 else 'title' if w[1:].islower() else 'mixed']
    if '-' in w:
        f.append('hyphen')
    f.append('lf:%d' % lf_bin)
    return tuple(_h(x, mask) for x in f)


def _features(text, toks, i, lowfreq, cues, mask):
    s, e, w = toks[i]
    lf = lowfreq.get(w.lower(), 0)
    out = list(_word_base_hashes(w, min(int(math.log2(lf + 1)), 12) if lf else 0, mask))
    prev = toks[i - 1] if i > 0 else None
    nxt = toks[i + 1] if i + 1 < len(toks) else None
    gap_prev = text[prev[1]:s] if prev else text[:s]
    out.append(_h('start' if not prev or _SENT_END_RE.search(gap_prev) else 'mid', mask))
    if _COLON_BEFORE_RE.search(gap_prev):
        out.append(_h('colon_before', mask))
    if prev:
        pl = prev[2].lower()
        out.append(_h('p:' + pl if pl in cues else 'pcap' if prev[2][:1].isupper() else 'plow', mask))
        if pl in cues:
            out.append(_h('cue', mask))
    if i > 1 and toks[i - 2][2].lower() in cues:
        out.append(_h('pp:' + toks[i - 2][2].lower(), mask))
    if nxt:
        out.append(_h('ncap' if nxt[2][:1].isupper() and text[e:nxt[0]] in (' ', '-') else 'nlow', mask))
        nl = nxt[2].lower()
        out.append(_h('n:' + nl if nl in cues else 'n:other', mask))
    else:
        out.append(_h('end', mask))
    return out


def tokens(text):
    """Canonical NFC words with original offsets, excluding possessive suffixes."""
    cache = language.CACHE.get()
    if cache is not None and ('tokens', text) in cache:
        return cache[('tokens', text)]
    if text.isascii():
        res = [(m.start(), m.end() - 2 if (g := m.group()).endswith("'s") else m.end(),
                g[:-2] if g.endswith("'s") else g) for m in _ASCII_TOKEN.finditer(text)]
    else:
        res = [(m.start(), m.end() - 2 if (g := m.group()).endswith(("'s", '’s')) else m.end(),
                unicodedata.normalize('NFC', g[:-2] if g.endswith(("'s", '’s')) else g)) for m in TOKEN.finditer(text)]
    if cache is not None:
        cache[('tokens', text)] = res
    return res


class LegacyScore:
    """Current regression without the optional Polish person dictionaries."""
    def score(self, text, toks, candidates):
        cache = language.CACHE.get()
        lprob = cache.get(('lprob', text)) if cache is not None else None
        if lprob is not None and all(i in lprob[0] for i in candidates):
            return ({i: lprob[0][i] for i in candidates}, lprob[1], lprob[2])
        weights, bias, freq, cues, mask, high, low = _model()
        prev = dict(lprob[0]) if lprob is not None else {}
        out = {i: prev[i] if i in prev else 1.0 / (1.0 + math.exp(-max(-60., min(60., bias + sum(weights[f] for f in _features(text, toks, i, freq, cues, mask))))))
               for i in candidates}
        if cache is not None:
            cache[('lprob', text)] = ({**prev, **out}, high, low)
        return (out, high, low)


def _call_cache(function):
    @wraps(function)
    def cached(*args, **kwargs):
        token = language.CACHE.set({}) if language.CACHE.get() is None else None
        try:
            return function(*args, **kwargs)
        finally:
            if token is not None:
                language.CACHE.reset(token)
    return cached


@_call_cache

def spans(text, *, backend=None):
    """Return [(start, end, 'name')] for detected person names; never raises on ordinary text."""
    if not isinstance(text, str) or not text or len(text) > MAX_TEXT or not any(c.isupper() for c in text):
        return []
    cache = language.CACHE.get()
    bkey = ('base_spans', text, id(spans), id(_base_spans))
    if backend is None and cache is not None and bkey in cache:
        return list(cache[bkey])
    if isinstance(backend, str):
        if backend != 'g_full_r1':
            raise ValueError('Unknown name backend: ' + backend)
        from .name_model_g import backend as operating_point_g
        backend = operating_point_g(backend)
    if hasattr(backend, 'filter_spans'):
        return backend.filter_spans(text)
    toks = tokens(text)
    cand = [i for i, t in enumerate(toks) if t[2][:1].isupper() and
            (len(t[2]) >= 2 or text[t[1]:t[1]+1] == '.')]
    if hasattr(backend, 'candidates'):
        cand = backend.candidates(text, toks, cand)
    if not cand:
        if backend is None and cache is not None:
            cache[bkey] = []
        return []
    if backend is None:
        prob, high, low = LegacyScore().score(text, toks, cand)
    else:
        prob, high, low = backend.score(text, toks, cand)
    non_person = {m.start() + t.start() for m in _NON_PERSON.finditer(text) for t in TOKEN.finditer(m.group())}
    non_person.update(m.start('word') for m in _SENTENCE_COMMON.finditer(text))
    paragraphs = list(language.paragraphs(text))
    starts = [offset for offset, _ in paragraphs]
    langs = [language.detect(paragraph) for _, paragraph in paragraphs]
    pl = {i for i in cand if langs[bisect_right(starts, toks[i][0])-1] != 'en'}
    excluded = {i for i in cand if toks[i][2].lower() in _BOUNDARIES or
                toks[i][2].lower() in _HONORIFICS or
                (i in pl and toks[i][2].lower() in _PL_EXCLUDED) or
                toks[i][0] in non_person or
                (_ordinary_leader(toks[i][2]) and (i == 0 or _SENT_END_RE.search(text[toks[i-1][1]:toks[i][0]]))) or
                (i > 0 and toks[i-1][2].lower() in ('w', 'we', 'przy') and
                 _LODZ_WAW_RE.fullmatch(toks[i][2]) and
                 text[toks[i-1][1]:toks[i][0]].isspace())}
    flagged = {i for i, p in prob.items() if i not in excluded and
               (p >= high and len(toks[i][2]) >= 2 or
                i in pl and i > 0 and toks[i-1][2].lower() in _HONORIFICS and text[toks[i-1][1]:toks[i][0]].isspace())}
    # Retain the title's person evidence without including the title in the mask.
    flagged.update(i for i in cand if i not in excluded and i > 0 and
                   toks[i-1][2].lower() in _TITLE_EVIDENCE and
                   prob.get(i-1,0) >= high and prob[i] >= low and
                   text[toks[i-1][1]:toks[i][0]] in (' ','. '))
    # A middle initial is strong structural evidence when both neighbouring words score above LOW.
    flagged.update(i for i in cand if len(toks[i][2]) == 1 and i-1 not in excluded and i+1 not in excluded and
                   prob.get(i-1, 0) >= low and prob.get(i+1, 0) >= low and
                   text[toks[i-1][1]:toks[i][0]] == ' ' and text[toks[i][1]:toks[i+1][0]] == '. ')
    long_run = set()
    run = []
    for i in cand:
        gap = text[toks[run[-1]][1]:toks[i][0]] if run else ''
        if run and (i != run[-1] + 1 or not (gap in (' ', '-', ' - ')
                       or len(toks[run[-1]][2]) == 1 and gap == '. ')):
            if len(run) > MAX_NAME_TOKENS:
                long_run.update(run)
            run = []
        run.append(i)
    if len(run) > MAX_NAME_TOKENS:
        long_run.update(run)
    # Long title runs retain only individually supported tokens, without neighbour expansion.
    pending = list(flagged)
    while pending:
        j = pending.pop()
        for i in (j - 1, j + 1):
            if i not in prob or i in flagged or i in excluded:
                continue
            # LOW rejects weak discourse leaders; applying it to surnames loses coverage.
            if prob[i] < low and toks[i][2].lower() in _LEAD_CUES:
                continue
            if i in long_run or j in long_run:
                continue
            a, b = sorted((i, j))
            if hasattr(backend, 'allow_join') and not backend.allow_join(text, toks, a, b):
                continue
            if text[toks[a][1]:toks[b][0]] in (' ', '-', ' - ') or (len(toks[a][2]) == 1 and text[toks[a][1]:toks[b][0]] == '. '):
                flagged.add(i)
                pending.append(i)
    result = []
    joined_tokens = 0
    for i in sorted(flagged):
        s, e, _ = toks[i]
        if result and i not in long_run and joined_tokens < MAX_NAME_TOKENS and (not hasattr(backend, 'allow_join') or backend.allow_join(text, toks, i-1, i)) and (text[result[-1][1]:s] in (' ', '-', ' - ') or
                       i > 0 and len(toks[i-1][2]) == 1 and text[result[-1][1]:s] == '. '):
            result[-1] = (result[-1][0], e, 'name')
            joined_tokens += 1
        else:
            result.append((s, e, 'name'))
            joined_tokens = 1
    result = _name_boundaries(text, result)
    if backend is None and cache is not None:
        cache[bkey] = result
    return result


_base_spans = spans


# Route only confident English paragraphs; larger prompts retain the baseline detector.
EN_CONFIDENCE = .8  # 14.11 (Konrad): 0.8 with the Polish veto; 0.99 kept only a third of EN paragraphs
EN_MAX_TEXT = 8192
EN_MAX_CANDIDATES = 64
_POLISH_ROUTE_CUES = _HONORIFICS | _PL_TITLES
_SINGLE_CUES = frozenset('hi hello hey dear thanks thank cc by from to with ask asked tell told call email emailed ping pinged mr mrs ms dr prof per attn regards best cheers sincerely'.split())


_GREETING_WORDS = frozenset('cześć czesc witaj witam hej'.split())
_DISCOURSE_WORDS = frozenset('and or but yet so although though because while whereas if then meanwhile'.split())


def _ordinary_leader(word):
    """Ordinary adverbs, conjunctions and greetings cannot seed a person's name."""
    from .name_model_g import classes
    return (word.lower() in _LEAD_CUES | _GREETING_WORDS | _DISCOURSE_WORDS
            or 'r' in classes(word.lower())[0])


def _name_boundaries(text, names):
    """Remove sentence-leading discourse words before any full-name evidence is formed."""
    result = []
    for s, e, kind in names:
        before = text[:s].rstrip()
        initial = not before or before[-1:] in '.!?' or not text[text.rfind('\n', 0, s)+1:s].strip()
        words = list(TOKEN.finditer(text, s, e))
        if initial:
            while words and _ordinary_leader(words[0].group()):
                words.pop(0)
        if words:
            result.append((words[0].start(), e, kind))
    return sorted(set(result))


def _contact_names(text):
    """An interpersonal verb followed by a rare title-case pair supplies full-name evidence."""
    from .name_model_g import classes
    from .name_model_f import bucket
    toks, freq, result = tokens(text), _model()[2], []
    for i in range(1, len(toks)-1):
        left, first, last = toks[i-1:i+2]
        if (not first[2].istitle() or not last[2].istitle()
                or min(len(first[2]), len(last[2])) < 2
                or not re.fullmatch(r'[ \t]+', text[left[1]:first[0]])
                or text[first[1]:last[0]] != ' '
                or first[0] and (text[first[0]-1].isalnum() or text[first[0]-1] == '_')
                or last[1] < len(text) and (text[last[1]].isalnum() or text[last[1]] == '_')):
            continue
        senses = classes(left[2].lower())[1]
        verbs = {k: v for k, v in senses.items() if k.startswith('verb.')}
        contact = {'verb.communication', 'verb.social', 'verb.contact'}
        if not verbs or sum(v for k, v in verbs.items() if k in contact) <= sum(verbs.values())/2:
            continue
        # Reuse the existing ordinary-frequency band; no model threshold is changed.
        if any(bucket(freq, t[2]) >= 2 or _ordinary_leader(t[2])
               or t[2].lower() in _BOUNDARIES | _EN_TITLES for t in (first, last)):
            continue
        # Ordinary dictionary nouns such as Marketing/Team do not constitute a rare name pair.
        surname_senses = {'noun.person', 'noun.animal', 'noun.plant', 'noun.time'}
        if any(classes(t[2].lower())[0] and not surname_senses.intersection(classes(t[2].lower())[1])
               for t in (first, last)):
            continue
        # Longer title runs may be product or organization labels rather than a person pair.
        if i+2 < len(toks) and toks[i+2][2][:1].isupper() and text[last[1]:toks[i+2][0]] == ' ':
            continue
        result.append((first[0], last[1], 'name'))
    return result


def _name_lists(text):
    """Syntactic lists, independent of which members the model detected."""
    non_person = [(m.start(), m.end()) for m in _NON_PERSON.finditer(text)]
    candidates = [(s, e, 'name') for s, e, w in tokens(text)
                  if w[:1].isupper() and len(w) >= 2 and w.lower() not in _BOUNDARIES | _LEAD_CUES | _EN_TITLES
                  and not (s and (text[s-1].isalnum() or text[s-1] == '_'))
                  and not (e < len(text) and (text[e].isalnum() or text[e] == '_'))
                  and not any(a < e and s < b for a, b in non_person)]
    groups, group = [], []
    for candidate in candidates:
        if group and not _COORD_GAP_RE.fullmatch( text[group[-1][1]:candidate[0]]):
            groups.append(group)
            group = []
        group.append(candidate)
    groups.append(group)
    return [group for group in groups if len(group) >= 2]


def _reversed_pair(text, group):
    """An isolated surname-first pair in a header or a quoted CSV field."""
    from .name_model_g import classes
    if len(group) != 2:
        return False
    (s, e, _), (a, b, _) = group
    if not re.fullmatch(r',[ \t]+', text[e:a]):
        return False
    word = text[s:e].lower()
    pos, sem = classes(word)
    # Person-only occupational senses also occur as surnames (e.g. Smith).
    if word in _SINGLE_CUES | _LEAD_CUES or pos and set(sem) != {'noun.person'}:
        return False
    before, after = text[:s], text[b:]
    csv = (re.search(r'(?:^|[,;\r\n])[ \t]*"$', before)
           and re.match(r'"[ \t]*(?:[,;\r\n]|$)', after))
    header = (re.search(r'(?:^|[\r\n])[ \t]*$', before)
              or re.search(r'(?:^|[;\r\n])[ \t]*[^\W\d_]+(?:[ \t]+[^\W\d_]+)?[ \t]*:[ \t]*$', before))
    return bool(csv or header and re.match(r'[ \t]*(?:[;.!?\r\n]|$)', after))


def _list_person_context(text, group, names):
    from .name_model_g import classes, roots
    contact = {'verb.communication', 'verb.social', 'verb.contact'}
    actions = contact | {'verb.motion', 'verb.possession'}
    def verb_senses(word):
        return {key: value for stem in roots(word) for key, value in classes(stem)[1].items()
                if key.startswith('verb.')}
    after = re.match(r'[ \t]+([a-z]+)\b', text[group[-1][1]:])
    following = after[1] if after else ''
    verbs = verb_senses(following) if following else {}
    pos = classes(following)[0] if following else {}
    # Plural/nominal heads veto propagation even when a member is confirmed.
    modal = following in {'will', 'would', 'can', 'could', 'shall', 'should', 'may', 'might', 'must'}
    if not modal and (pos.get('n', 0) and (not verbs or following.endswith('s') or pos['n'] >= sum(verbs.values()))
            or following.endswith('s') and not pos and not verbs):
        return False
    if modal:
        tail = re.match(r'[ \t]+([a-z]+)\b', text[group[-1][1] + after.end():])
        verbs = verb_senses(tail[1]) if tail else {}
    person_action = sum(v for k, v in verbs.items() if k in actions) > sum(verbs.values()) / 2
    full_words = {w for s, e, _ in names if len(tokens(text[s:e])) >= 2
                  and not (s < group[-1][1] and group[0][0] < e)
                  for _, _, w in tokens(text[s:e])}
    def cued(span):
        return bool(re.search(r'(?i)\b(?:hi|hello|hey|dear|thanks|thank|cc|attn|'
                              + '|'.join(sorted(_EN_TITLES)) + r')[ \t]*[.:]?[ \t]+$', text[:span[0]]))
    full_words.update(text[s:e] for s, e, kind in names if cued((s, e, kind)))
    confirmed = any(text[s:e] in full_words or cued((s, e, kind)) for s, e, kind in group)
    before = re.search(r'\b([a-z]+)[ \t]+$', text[:group[0][0]])
    previous = verb_senses(before[1]) if before else {}
    # Require a predominantly interpersonal verb, not a polysemous cover/mix.
    object_context = sum(v for k, v in previous.items() if k in contact) > sum(previous.values()) / 2
    return person_action or confirmed or object_context


def _coordinated_names(text, names, *, lists=True):
    """Propagate only from person context or a header/CSV surname-first pair."""
    result = list(names)
    ordered = sorted(names)
    starts = [span[0] for span in ordered]
    def overlaps(span):
        index = bisect_right(starts, span[1] - 1) - 1
        return index >= 0 and ordered[index][1] > span[0]
    for group in _name_lists(text):
        if _reversed_pair(text, group) or lists and _list_person_context(text, group, names):
            result.extend(x for x in group if not overlaps(x))
    return sorted(result)


def _singletons_with_evidence(text, names, evidence=None):
    """Keep full spans; a singleton needs an explicit person-context cue."""
    from .name_model_g import classes, roots
    from .name_context import _SUFFIXES
    full_words = {word for s, e, _ in (names if evidence is None else evidence)
                  if len(text[s:e].split()) >= 2
                  for word in text[s:e].replace("'s", '').replace('’s', '').split()}
    coordinated = set()
    unsupported = set()
    for group in _name_lists(text):
        if _reversed_pair(text, group) or _list_person_context(text, group, names if evidence is None else evidence):
            coordinated.update(group)
        else:
            unsupported.update(group)
    verbs = {}
    kept = []
    for s, e, kind in names:
        word = text[s:e]
        if len(word.split()) >= 2:
            kept.append((s, e, kind))
            continue
        line_start = text.rfind('\n', 0, s) + 1
        line_end = text.find('\n', e)
        line_end = len(text) if line_end < 0 else line_end
        previous = _ALPHA_RE.findall( text[max(0, s-25):s])
        previous = previous[-1].lower() if previous else ''
        alone = not text[line_start:s].strip(' \t\r-—,') and not text[e:line_end].strip(' \t.,\r')
        speaker = not text[line_start:s].strip() and text[e:e+1] == ':'
        before = text[:s].rstrip()
        start = not before or before[-1:] in '.!?' or not text[line_start:s].strip()
        following = _FOLLOW_RE.match( text[e:])
        following = following.group(1) if following else ''
        if following not in verbs:
            # Regular participles can have a separate adjective entry (e.g. processed).
            verbs[following] = following.islower() and any(pos.split('-', 1)[0] == 'v' for stem in roots(following) for pos in classes(stem)[0])
        adjacent = _ADJ_RE.search( text[max(0, s-40):s])
        following_pos = classes(following.lower())[0] if following else {}
        object_end = (not following or following.lower() in _BOUNDARIES
                      or any(pos.split('-', 1)[0] in {'r', 'v'} for pos in following_pos))
        person_action = (bool(adjacent) and adjacent[1].islower() and word.lower() not in _SUFFIXES and object_end
                         and bool({'verb.communication', 'verb.social', 'verb.contact'} & classes(previous)[1].keys()))
        role = bool(adjacent) and _role_noun(previous)
        if ((s, e, kind) in coordinated or word in full_words
                or previous in _EN_TITLES | {'best', 'regards', 'cheers', 'sincerely'}
                or (s, e, kind) not in unsupported and (previous in _SINGLE_CUES | _LEAD_CUES
                                                       or person_action or role or alone or speaker or start and verbs[following]
                                                       or _ordinary_leader(previous) and following.islower())):
            kept.append((s, e, kind))
    return kept


_PARTICLES = frozenset('van der de da di von le la del della bin ibn al'.split())
# Leading discourse/contact words never start a person's name here (closed class, not names).
_LEAD_CUES = frozenset('hey hi hello morning evening afternoon thanks could would can should please reach call send ping notify email ask tell cc attn'.split())
_EN_TITLES = frozenset('mr mrs ms miss dr prof professor capt captain'.split())
_PARTICLE_TAIL_RE = re.compile(r'(?:[ \t]+(?:' + '|'.join(sorted(_PARTICLES)) + r'))+[ \t]+(' + TOKEN.pattern + r')')
_AL_TAIL_RE = re.compile(r'[ \t]+al-(' + TOKEN.pattern + r')')


def _role_noun(word):
    """Dominant OEWN sense is a person role (tenant, applicant); tom/grant/mark are not."""
    from .name_model_g import classes
    groups = classes(word)[1]
    return bool(groups) and groups.get('noun.person', 0) * 2 >= sum(groups.values())


def _english_boundaries(text, names):
    """Adjust EN spans without changing detection in other paragraphs."""
    result = []
    for s, e, kind in names:
        words = list(TOKEN.finditer(text, s, e))
        before = text[:s].rstrip()
        initial = not before or before[-1] in '.!?\n' or not text[text.rfind('\n', 0, s)+1:s].strip()
        if len(words) > 1 and initial:
            first = words[0].group().lower()
            earlier = any(a < s and len(text[a:b].split()) > 1 and
                          words[0].group() in text[a:b].split() for a, b, _ in names)
            if not earlier and (first in _LEAD_CUES or len(words) > 2 and _role_noun(first)):
                s = words[1].start()
        if text[s:e].lower().rstrip('.') in _EN_TITLES:
            continue
        tail = _PARTICLE_TAIL_RE.match(text[e:]) or _AL_TAIL_RE.match(text[e:])
        if (tail and tail.group(1)[0].isupper() and text[e-1:e].isalpha()
                and len(tokens(text[s:e + tail.end()])) <= MAX_NAME_TOKENS):
            end = e + tail.end()
            if not any(e < b and a < end for a, b, _ in names):
                e = end
        result.append((s, e, kind))
    return result


def _titled(text, span):
    """A title or honorific right before a single name identifies the person like a full name (Mr. Smith, Panie Tomaszu)."""
    before = re.findall(r"[^\W\d_]+\.?", text[max(0, span[0]-16):span[0]])
    return bool(before) and before[-1].rstrip('.').lower() in _EN_TITLES | _HONORIFICS | {'mx'}


def _prompt_spans_existing(text, mode="deep"):
    if mode not in ("basic", "deep"):
        raise ValueError("invalid name masking mode")
    # G sees the bounded document; confident EN paragraphs use it, others keep the baseline; errors keep baseline coverage.
    original = spans(text)
    if not isinstance(text, str) or not text or len(text) > EN_MAX_TEXT:
        return original
    if sum(t[2][:1].isupper() for t in tokens(text)) > EN_MAX_CANDIDATES:
        return original
    try:
        contact_names = _contact_names(text)
        original = _coordinated_names(text, sorted(set(original + contact_names)), lists=False)
    except Exception:
        return original
    from . import paragraph_context
    parts = list(language.paragraphs(text))
    selected = []
    for _, paragraph in parts:
        try:
            cache = language.CACHE.get()
            probabilities = paragraph_context.classify(paragraph)['lang']
            if cache is not None:
                cache[('pc_lang', paragraph, id(paragraph_context.classify))] = probabilities
            # Only confident English takes the English path; uncertain text keeps the previous detector (deep) or its
            # basic-level filter (basic), so a short Polish line is not judged by the English models.
            selected.append(max(probabilities, key=probabilities.get) == 'EN'
                            and probabilities.get('EN', 0.) >= EN_CONFIDENCE
                            and language.paragraph_language(paragraph, probabilities=probabilities) not in ('pl', 'other')
                            and language.detect(paragraph) != 'pl'
                            and not any(t[2].lower() in _POLISH_ROUTE_CUES for t in tokens(paragraph)))
        except Exception:
            selected.append(False)
    if not any(selected):
        return original
    starts = [offset for offset, _ in parts]
    def english(span):
        return selected[bisect_right(starts, span[0])-1]
    try:
        non_person = [(m.start(), m.end()) for m in _NON_PERSON.finditer(text)]
        fresh = [span for span in spans(text, backend='g_full_r1') if english(span)
                 and not any(a < span[1] and span[0] < b for a, b in non_person)]
        fresh = sorted(set(_name_boundaries(text, fresh) + [x for x in contact_names if english(x)]))
        from .name_context import filter_spans
        fresh = filter_spans(text, [span for span in _coordinated_names(text, fresh) if english(span)])
        unchanged = [span for span in original if not english(span)]
        english_names = _english_boundaries(text, _singletons_with_evidence(text, fresh, evidence=unchanged + fresh))
        if mode == 'basic':
            multi = [x for x in english_names if len(text[x[0]:x[1]].split()) >= 2]
            words = {w for s, e, _ in multi for w in text[s:e].replace("'s", '').replace('’s', '').split() if w[:1].isupper()}
            singles = sorted(x for x in english_names if len(text[x[0]:x[1]].split()) == 1)
            # "Kowalska, Anna" is one person written surname-first: keep an isolated comma pair, not a longer list.
            paired = {span for group in _name_lists(text) if _reversed_pair(text, group) for span in group}
            english_names = multi + [x for x in singles if text[x[0]:x[1]] in words or x in paired or _titled(text, x)]
        return sorted(unchanged + english_names)
    except Exception:
        # Baseline failures still propagate to the existing fail-closed masking caller.
        return original


@_call_cache
def prompt_spans(text, mode="deep", other_languages="off"):
    if mode not in ("basic", "deep") or other_languages not in ("on", "off"):
        raise ValueError("invalid name masking mode")
    found = _name_boundaries(text, _prompt_spans_existing(text, mode=mode))
    if not isinstance(text, str) or not text:
        return found
    parts = list(language.paragraphs(text))
    kinds = [language.paragraph_language(part, lexical_only=len(text) > EN_MAX_TEXT) for _, part in parts]
    starts = [start for start, _ in parts]
    # basic = full names, titled names and repeats in every language; deep keeps all names in English and Polish.
    basic = [span for span in found if mode == 'basic' and kinds[bisect_right(starts, span[0])-1] != 'other'
             or (kinds[bisect_right(starts, span[0])-1] == 'other' and other_languages == 'on')]
    multi = [x for x in basic if len(text[x[0]:x[1]].split()) >= 2
             and text[x[0]:x[1]].split()[0].lower() not in ('cześć', 'czesc', 'witaj', 'witam', 'hej')]
    words = {word for s, e, _ in multi for word in text[s:e].split()}
    paired = {span for group in _name_lists(text) if _reversed_pair(text, group) for span in group}
    keep = set(multi + [x for x in basic if text[x[0]:x[1]] in words or x in paired or _titled(text, x)])
    filtered = set(basic)
    return [span for span in found if (span not in filtered and kinds[bisect_right(starts, span[0])-1] != 'other') or span in keep]

