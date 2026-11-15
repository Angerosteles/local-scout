"""Shared slot features, C addition and confirmed propagation for the G backend."""
from bisect import bisect_right
from functools import lru_cache
import json, math, re, zlib
from pathlib import Path

from . import language, name_model as nm, paragraph_context as pc

ROOT = Path(__file__).parent / 'models'
QUOTE = frozenset('"“”‘*_')
END = re.compile(r'[.!?\n]')
_POS_C_RE, _POS_S_RE = re.compile(r':[ \t"“‘*_]*$'), re.compile(r'[.!?\n][\s"“‘*_(>#•-]*$')
_DIGIT_RE, _REPEAT_GAP_RE = re.compile(r'\d'), re.compile(r"(?:[ \t]+|['’]s[ \t]+| ?- ?)")


tokens = nm.tokens


def bucket(freq, word):
    """Ordinary lowercase frequency in five steps; 2 and above means a common word."""
    c = freq.get(word.lower(), 0)
    return min(int(math.log2(c + 1)), 8) // 2 if c else 0


def rep(freq, word):
    """Context words keep their identity only when lowercase and common; names stay anonymous."""
    if word.islower():
        return word if bucket(freq, word) >= 2 else '<low>'
    return '<I>' if len(word) == 1 else '<UP>' if word.isupper() else '<Cap>'


def shape(word):
    return 'I' if len(word) == 1 else 'UP' if word.isupper() else 'Cap' if word[1:].islower() else 'Mix'


def context(text, freq=None):
    toks = tokens(text)
    caps = [k for k, t in enumerate(toks) if t[2][:1].isupper()]
    return dict(toks=toks, starts={t[0]: k for k, t in enumerate(toks)}, ends={t[1]: k for k, t in enumerate(toks)},
                prob=nm.LegacyScore().score(text, toks, caps)[0] if caps else {},
                lower={t[2] for t in toks if t[2].islower()},
                freq=nm._model()[2] if freq is None else freq)


def gaps(text, toks, i, j):
    before = text[toks[i-1][1]:toks[i][0]] if i else text[:toks[i][0]]
    after = text[toks[j][1]:toks[j+1][0]] if j + 1 < len(toks) else text[toks[j][1]:]
    return before, after


def eligible(text, ctx, i, j):
    """A veto needs explicit context; absence of evidence never removes a name."""
    toks = ctx['toks']
    before, after = gaps(text, toks, i, j)
    return (after == ' ' and j + 1 < len(toks) and toks[j+1][2].islower()
            or before.rstrip()[-1:] in QUOTE or after.lstrip()[:1] in QUOTE or after.lstrip()[:1] == ':'
            or any(bucket(ctx['freq'], t[2]) >= 2 for t in toks[i:j+1]))


def slot(text, ctx, i, j):
    """Feature strings for tokens i..j; spelling enters only as frequency and base-probability steps."""
    toks, freq = ctx['toks'], ctx['freq']
    before, after = gaps(text, toks, i, j)
    words = [t[2] for t in toks[i:j+1]]
    pos = ('C' if _POS_C_RE.search(before) else
           'S' if not i or _POS_S_RE.search(before) else 'M')
    l1 = rep(freq, toks[i-1][2]) if i else '<none>'
    l2 = rep(freq, toks[i-2][2]) if i > 1 else '<none>'
    nxt = toks[j+1][2] if j + 1 < len(toks) and not END.search(after) else ''
    r1 = rep(freq, nxt) if nxt else '<end>'
    r2 = (rep(freq, toks[j+2][2]) if nxt and j + 2 < len(toks) and not END.search(text[toks[j+1][1]:toks[j+2][0]])
          else '<end>')
    lg = before.rstrip()[-1:]
    lg = '0' if lg.isalnum() else lg
    rg = _DIGIT_RE.sub('0', after.strip()[:2]).replace('’', "'") or ('NL' if '\n' in after else 'SP' if after else 'EOT')
    b0, bz, n = bucket(freq, words[0]), bucket(freq, words[-1]), min(len(words), 3)
    p = int(max(ctx['prob'].get(k, 0.) for k in range(i, j + 1)) * 4.999)
    out = ['pos:' + pos, 'n:%d' % n, 'sh:' + '_'.join(shape(w) for w in words[:3]),
           'l1:' + l1, 'l2:' + l2, 'll:%s|%s' % (l2, l1), 'lg:' + lg,
           'r1:' + r1, 'r2:' + r2, 'rr:%s|%s' % (r1, r2), 'rg:' + rg, 'lr:%s|%s' % (l1, r1),
           'b0:%d' % b0, 'bz:%d' % bz, 'p:%d' % p,
           'x:%s|%d|%d' % (pos, n, b0), 'xr:%s|%d|%s' % (pos, b0, r1), 'xg:%s|%s' % (pos, rg),
           'xp:%s|%d|%d' % (pos, b0, p),
           'q:%d%d' % (before.rstrip()[-1:] in QUOTE, after.lstrip()[:1] in QUOTE)]
    if nxt.islower():
        out += ['s2:' + nxt[-2:], 's3:' + nxt[-3:], 'xs:%s|%d|%s' % (pos, b0, nxt[-2:])]
    out += ['in:' + w.lower() for w in words if bucket(freq, w) >= 2]
    if len(words) > 1 and bz >= 2:
        out.append('last:' + words[-1].lower())
    if words[0].lower() in ctx['lower']:
        out.append('doclow')
    return out


@lru_cache(maxsize=16384)
def _crc(x, mask):
    return zlib.crc32(x.encode('utf-8')) & mask


def hashed(features, mask):
    return sorted({_crc(x, mask) for x in features})


class Backend:
    def __init__(self, data=None, threshold=0., add=False, propagate=False):
        data = data or dict(weights={}, bias=0., nbits=16)
        if not 0 <= threshold <= 1 or not 8 <= int(data['nbits']) <= 20:
            raise ValueError('Invalid slot model')
        self.weights = {int(k): float(v) for k, v in data['weights'].items()}
        self.bias, self.mask = float(data['bias']), (1 << int(data['nbits'])) - 1
        if not all(math.isfinite(v) for v in [self.bias, *self.weights.values()]):
            raise ValueError('Invalid slot weights')
        self.seed = float(data.get('seed', .5))
        self.threshold, self.add, self.propagate, self.freq = threshold, add, propagate, None

    def english(self, paragraph):
        hint = language.detect(paragraph)
        if hint != 'both':
            return hint == 'en'
        cache = language.CACHE.get()
        probs = cache.get(('pc_lang', paragraph, id(pc.classify))) if cache is not None else None
        return (probs if probs is not None else pc.classify(paragraph)['lang']).get('EN', 0.) >= .8

    def judged(self, text, names):
        """Return ctx and [(span, score, may_veto)]; score is None outside English or token boundaries."""
        ctx = context(text, self.freq)
        parts = list(language.paragraphs(text))
        starts, known, jcache = [offset for offset, _ in parts], {}, {}

        def judge(s, e):
            if (s, e) in jcache:
                return jcache[(s, e)]
            i, j = ctx['starts'].get(s), ctx['ends'].get(e)
            k = bisect_right(starts, s) - 1
            if i is None or j is None or j < i:
                return None, False
            if k not in known:
                known[k] = self.english(parts[k][1])
            jcache[(s, e)] = (self.score(text, ctx, i, j), eligible(text, ctx, i, j)) if known[k] else (None, False)
            return jcache[(s, e)]
        return ctx, judge, [(x, *judge(x[0], x[1])) for x in sorted(names)]

    def repeat_confirmed(self, text, ctx, kept, evidence, judge):
        """Repeat full person-model seeds or isolated rare components, never part of a longer title run."""
        toks, full, words = ctx['toks'], set(), set()
        for s, e, _ in kept:
            if (s, e) not in evidence:
                continue
            score, _ = judge(s, e)
            i, j = ctx['starts'].get(s), ctx['ends'].get(e)
            if score is None or score < self.seed or i is None or j is None or j <= i:
                continue
            full.add(tuple(t[2] for t in toks[i:j+1]))
            words.update(t[2] for t in toks[i:j+1] if len(t[2]) >= 3 and bucket(ctx['freq'], t[2]) < 2 and not nm._ordinary_leader(t[2]))
        i = 0
        while full and i < len(toks):
            if not toks[i][2][:1].isupper():
                i += 1
                continue
            j = i
            while j + 1 < len(toks) and toks[j+1][2][:1].isupper():
                gap = text[toks[j][1]:toks[j+1][0]]
                if not (_REPEAT_GAP_RE.fullmatch(gap)
                        or len(toks[j][2]) == 1 and gap == '. '):
                    break
                j += 1
            match = tuple(t[2] for t in toks[i:j+1])
            s, e = toks[i][0], toks[j][1]
            if (match in full or i == j and match[0] in words) and not any(s < d and c < e for c, d, _ in kept):
                score, may = judge(s, e)
                if score is not None and not (may and score < self.threshold):
                    kept.append((s, e, 'name'))
            i = j + 1

    def apply(self, text, names, evidence=()):
        """Veto complete spans, then optionally propagate using the selected mechanism."""
        ctx, judge, rows = self.judged(text, names)
        kept = []
        for (s, e, c), score, may in rows:
            if may and score < self.threshold:
                continue
            kept.append((s, e, c))
        if self.propagate == 'confirmed':
            self.repeat_confirmed(text, ctx, kept, evidence, judge)
        return sorted(kept)

    def filter_spans(self, text):
        names = nm._name_boundaries(text, nm._base_spans(text))
        evidence = {(s, e) for s, e, _ in names} if self.propagate == 'confirmed' else None
        if self.add:
            cache = language.CACHE.get()
            if cache is not None:
                cache[('c_skip', text)] = names
            try:
                c_spans = c_backend().filter_spans(text, evidence=evidence)
            finally:
                if cache is not None:
                    cache.pop(('c_skip', text), None)
            names = names + [x for x in c_spans if len(text[x[0]:x[1]].split()) >= 2
                             and not any(s < x[1] and x[0] < e for s, e, _ in names)]
        names = nm._name_boundaries(text, names)
        return self.apply(text, names, evidence=evidence or ()) if names and (self.threshold or self.propagate) else sorted(names)


@lru_cache(maxsize=1)
def c_backend():
    from .name_model_c import Backend as C
    return C(json.loads((ROOT / 'name_c_span.json').read_text(encoding='utf-8')),
             threshold=.25, propagate=False, multi_only=True)
