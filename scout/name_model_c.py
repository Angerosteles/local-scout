"""Optional English span classifier with background counts and document evidence."""
import math, re
from array import array
import hashlib, json

from . import language, name_model as nm, paragraph_context as pc
FEATURES = [
    'length', 'title', 'mixed', 'hyphen', 'digit_near', 'ordinary_logfreq', 'sentence_end', 'code_near',
    'url_near', 'period_before', '-2:exists', '-2:ordinary_logfreq', '-1:ordinary_logfreq', '-1:cue',
    '1:capital', '1:length', '1:ordinary_logfreq', '2:length', '2:ordinary_logfreq', '2:cue',
    '2:boundary', 'tokens', 'mean_length', 'min_legacy', 'max_legacy', 'mean_legacy', 'base',
    'all_title', 'after_lower', 'quote_after', 'left_title', 'right_title', 'bg:lower:mean',
    'bg:lower:max', 'bg:title:mean', 'bg:title:max', 'bg:upper:mean', 'bg:upper:max',
    'bg:documents:min', 'bg:documents:mean', 'bg:documents:max', 'outside:-2:a', 'outside:-2:my',
    'outside:-2:this', 'outside:-1:a', 'outside:-1:am', 'outside:-1:at', 'outside:-1:by',
    'outside:-1:for', 'outside:-1:is', 'outside:-1:team', 'outside:-1:the', 'outside:-1:with',
    'outside:1:also', 'outside:1:born', 'outside:1:will', 'outside:2:from', 'outside:2:have',
    'outside:2:in', 'outside:2:on', 'outside:2:our', 'outside:2:the'
]

_QUOTE_AFTER_RE = re.compile(r'\s*["”’\u0027]')


SCHEMA = 'localscout-name-a-v1'


def sigmoid(z):
    return 1.0 / (1.0 + math.exp(-max(-60., min(60., z))))


def checksum(data):
    return hashlib.sha256(json.dumps({k:v for k,v in data.items() if k != 'sha256'},
                                    sort_keys=True, separators=(',',':'), ensure_ascii=False).encode()).hexdigest()


class Model:
    def __init__(self, data, *, feature_names):
        if data.get('schema') != SCHEMA or checksum(data) != data.get('sha256'):
            raise ValueError('Invalid name model schema/checksum')
        self.data = data
        count = len(feature_names)
        if data['feature_names'] != list(feature_names):
            raise ValueError('Invalid feature order')
        if data['kind'] != 'gbdt' or data['representation'] != 'dense':
            raise ValueError('Unsupported name model')
        if not 0 <= data['low'] <= data['high'] <= 1:
            raise ValueError('Invalid name thresholds')
        if not all(math.isfinite(float(x)) for x in [data['bias'], *data['calibration']]):
            raise ValueError('Invalid numeric model parameter')
        if len(data['trees']) > 100 or data['representation'] != 'dense':
            raise ValueError('Invalid tree budget')
        if not math.isfinite(data['learning_rate']) or data['learning_rate'] <= 0:
            raise ValueError('Invalid learning rate')
        for tree in data['trees']:
            n = len(tree['left'])
            if not 1 <= n <= 31 or any(len(tree[k]) != n for k in ('right','feature','threshold','value')):
                raise ValueError('Invalid tree arrays')
            for i, (left, right, feat) in enumerate(zip(tree['left'], tree['right'], tree['feature'])):
                if left == -1:
                    if right != -1:
                        raise ValueError('Invalid leaf')
                elif not (i < left < n and i < right < n and 0 <= feat < count):
                    raise ValueError('Invalid tree topology')
            if not all(math.isfinite(x) for x in tree['threshold']+tree['value']):
                raise ValueError('Invalid tree values')
        self._trees = tuple((tuple(t['left']), tuple(t['right']), tuple(t['feature']), tuple(t['threshold']), tuple(t['value']))
                            for t in data['trees'])

    def raw(self, vector):
        d = self.data
        z = d['bias']
        # sklearn's prediction API converts input features to float32.
        v32, lr = list(array('f', vector)), d['learning_rate']
        for left, right, feat, thresh, val in self._trees:
            node = 0
            while left[node] != -1:
                node = left[node] if v32[feat[node]] <= thresh[node] else right[node]
            z += lr * val[node]
        return z

    def probability(self, vector):
        a,b = self.data['calibration']
        return sigmoid(a*self.raw(vector)+b)


_SENT_END_RE, _URL_RE = re.compile(r'^[.!?\n]'), re.compile(r'https?://|www\.|@')
_GAP_PUNCT_RE = re.compile(r'[.!?\n:]')
_DIGIT_RE = re.compile(r'\d')
_CODE_CHARS_RE = re.compile(r'[{}[\]=<>;/_`]')
_CODE_CHARS = frozenset('{}[]=<>;/_`')
_CUES = frozenset('agent and applicant attention attn author by cc client contact customer dear dr employee from hello hi madam manager member miss mr mrs ms name names owner patient prof regards signed sincerely sir tenant to with'.split())
_ZERO4 = (0., 0., 0., 0.)


def token_features(text, toks, i, freq):
    s, e, w = toks[i]
    before = text[toks[i-1][1]:s] if i else text[:s]
    after = text[e:toks[i+1][0]] if i+1 < len(toks) else text[e:]
    local = text[max(0, s-40):min(len(text), e+40)]
    out = [min(len(w), 32)/32, float(w.istitle()), float(not w.istitle() and not w.isupper()),
           float('-' in w), float(bool(_DIGIT_RE.search(local))), math.log2(1+freq.get(w.lower(), 0)),
           float(i+1 == len(toks) or bool(_SENT_END_RE.search(after))),
           float(bool(_CODE_CHARS_RE.search(local))), float(bool(_URL_RE.search(local))), float('.' in before)]
    for delta in (-2, -1, 1, 2):
        k = i+delta
        exists = 0 <= k < len(toks)
        word = toks[k][2] if exists else ''
        if delta == -2:
            out.append(float(exists))
        if delta == 1:
            out += [float(word[:1].isupper()), min(len(word), 32)/32]
        if delta == 2:
            out.append(min(len(word), 32)/32)
        out.append(math.log2(1+freq.get(word.lower(), 0)) if exists else 0.)
        if delta in (-1, 2):
            out.append(float(word.lower() in _CUES) if exists else 0.)
        if delta == 2:
            gap = text[e:toks[k][0]] if exists else ''
            out.append(float(bool(_GAP_PUNCT_RE.search(gap))))
    return out


tokens = nm.tokens


def english(text):
    hint = language.detect(text)
    if hint != 'both':
        return hint == 'en'
    cache = language.CACHE.get()
    probs = cache.get(('pc_lang', text, id(pc.classify))) if cache is not None else None
    return (probs if probs is not None else pc.classify(text)['lang']).get('EN', 0.) >= .8


def proposals(text, toks, legacy=None):
    base = {(s, e) for s, e, _ in nm._base_spans(text, backend=legacy or nm.LegacyScore())}
    spans = set(base)
    for i, (s, e, word) in enumerate(toks):
        if not word[:1].isupper():
            continue
        if len(word) >= 2:
            spans.add((s, e))
        for j in range(i+1, min(i+4, len(toks))):
            if not toks[j][2].istitle() or not toks[j-1][2].istitle() or text[toks[j-1][1]:toks[j][0]] != ' ':
                break
            spans.add((s, toks[j][1]))
    return sorted(spans), base


class Backend:
    def __init__(self, data, threshold=None, propagate=True, multi_only=False):
        """multi_only skips isolated singleton vectors but retains blockers overlapping multi-word proposals."""
        self.data = data
        self.model = Model(data, feature_names=FEATURES)
        self.outside = [name.split(':', 2)[1:] for name in FEATURES if name.startswith('outside:')]
        self._outside_int = tuple((int(d), exp) for d, exp in self.outside)
        self.threshold = data['threshold'] if threshold is None else threshold
        self.propagate, self.multi_only = propagate, multi_only
        if not 0 <= self.threshold <= 1 or not 0 <= data['seed_threshold'] <= 1:
            raise ValueError('Invalid span threshold')
        self._bg_log = {w: tuple(math.log1p(x) for x in c) for w, c in data['background'].items()}

    def vectors(self, text):
        toks = tokens(text)
        if self.multi_only and not any(
                toks[k][2][:1].isupper() and toks[k+1][2][:1].isupper() and
                (text[toks[k][1]:toks[k+1][0]] in (' ', '-', ' - ') or len(toks[k][2]) == 1 and text[toks[k][1]:toks[k+1][0]] == '. ')
                for k in range(len(toks) - 1)):
            return toks, []
        ids = [i for i, (_, _, w) in enumerate(toks) if w[:1].isupper()]
        scores = nm.LegacyScore().score(text, toks, ids)
        probabilities = scores[0]

        class CachedLegacy:
            def score(self, text, toks, candidates):
                return {i: probabilities[i] for i in candidates}, scores[1], scores[2]

        spans, base = proposals(text, toks, CachedLegacy())
        starts = {s: i for i, (s, _, _) in enumerate(toks)}
        ends = {e: i for i, (_, e, _) in enumerate(toks)}
        if self.multi_only:
            covered = set()
            for s, e in spans:
                if s in starts and e in ends and ends[e] > starts[s]:
                    covered.update(range(starts[s], ends[e] + 1))
            spans = [(s, e) for s, e in spans if s in starts and starts[s] in covered]
        bg_log, feat_cache, out = self._bg_log, {}, []
        for s, e in spans:
            if s not in starts or e not in ends:
                continue
            i, j = starts[s], ends[e]
            words = [w for _, _, w in toks[i:j+1]]
            p = [probabilities.get(k, 0.) for k in range(i, j+1)]
            before = text[toks[i-1][1]:s] if i else text[:s]
            after = text[e:toks[j+1][0]] if j+1 < len(toks) else text[e:]
            if i not in feat_cache:
                feat_cache[i] = token_features(text, toks, i, self.data['ordinary_frequency'])
            vector = list(feat_cache[i]) + [
                len(words), sum(map(len, words))/len(words), min(p), max(p), sum(p)/len(p),
                float((s, e) in base), float(all(w.istitle() for w in words)),
                float(j+1 < len(toks) and toks[j+1][2].islower()),
                float(bool(_QUOTE_AFTER_RE.match(after))),
                float(i > 0 and toks[i-1][2].istitle() and before == ' '),
                float(j+1 < len(toks) and toks[j+1][2].istitle() and after == ' ')]
            if len(words) == 1:
                c0, c1, c2, c3 = bg_log.get(words[0].casefold(), _ZERO4)
                vector += [c0, c0, c1, c1, c2, c2, c3, c3, c3]
            else:
                counts = [bg_log.get(w.casefold(), _ZERO4) for w in words]
                for field in range(4):
                    values = [c[field] for c in counts]
                    if field == 3:
                        vector.append(min(values))
                    vector += [sum(values)/len(values), max(values)]
            n_toks = len(toks)
            for delta, expected in self._outside_int:
                k = i+delta if delta < 0 else j+delta
                word = toks[k][2] if 0 <= k < n_toks else ''
                vector.append(float(word == expected or (word.islower() and word.casefold() == expected)))
            out.append(((s, e), vector))
        return toks, out

    @staticmethod
    def resolve(scored, threshold):
        accepted = []
        for (s, e), p in sorted(scored, key=lambda item: (-item[1], -(item[0][1]-item[0][0]), item[0][0])):
            if p >= threshold and not any(s < d and c < e for c, d in accepted):
                accepted.append((s, e))
        return sorted(accepted)

    def paragraph(self, text, evidence=None):
        toks, vectors = self.vectors(text)
        if not vectors:
            return []
        scored = [(span, self.model.probability(v)) for span, v in vectors]
        accepted = self.resolve(scored, self.threshold)
        if self.propagate or evidence is not None:
            seeds = {span for span, p in scored if p >= max(self.threshold, self.data['seed_threshold'])
                     and span in accepted and len(tokens(text[span[0]:span[1]])) >= 2}
            if evidence is not None:
                evidence.update(seeds)
        if self.propagate:
            words = {w for s, e in seeds for a, b, w in toks if s <= a and b <= e}
            for s, e, w in toks:
                if w in words and w.istitle() and not any(s < d and c < e for c, d in accepted):
                    accepted.append((s, e))
        return [(s, e, 'name') for s, e in sorted(accepted)]

    def filter_spans(self, text, evidence=None):
        parts = [(offset, paragraph, english(paragraph)) for offset, paragraph in language.paragraphs(text)]
        original = nm._base_spans(text) if any(not en for _, _, en in parts) else []
        cache = language.CACHE.get()
        skip = cache.get(('c_skip', text)) if (cache is not None and self.multi_only) else None
        out = []
        for offset, paragraph, en in parts:
            if en:
                if skip is not None:
                    ex, ptoks = [(s - offset, e - offset) for s, e, _ in skip if offset <= s and e <= offset + len(paragraph)], tokens(paragraph)
                    if not any(ptoks[k][2][:1].isupper() and ptoks[k+1][2][:1].isupper()
                               and (paragraph[ptoks[k][1]:ptoks[k+1][0]] in (' ', '-', ' - ') or len(ptoks[k][2]) == 1 and paragraph[ptoks[k][1]:ptoks[k+1][0]] == '. ')
                               and not any(a < ptoks[k+1][1] and ptoks[k][0] < b for a, b in ex) for k in range(len(ptoks) - 1)):
                        continue
                local = set() if evidence is not None else None
                out += [(offset+s, offset+e, c) for s, e, c in self.paragraph(paragraph, local)]
                if evidence is not None:
                    evidence.update((offset+s, offset+e) for s, e in local)
            else:
                out += [(s, e, c) for s, e, c in original if offset <= s < offset+len(paragraph)]
        return sorted(out)
