"""English grammar/animacy slot model; F2 addition and propagation are inherited.

OEWN supplies ordinary lowercase word classes, never a proper-name lexicon.
All grammar signals are learned features, not acceptance or exclusion rules.
"""
from collections import Counter
from functools import lru_cache
import json, math, re

from . import name_model_f as f

CHANNELS = ('morph', 'pos', 'sem', 'pron')
ARTICLES = frozenset(('a', 'an', 'the'))
PRONOUNS = frozenset('i you he she they we it this that these those'.split())
_IMPERATIVE_SET, _CUES_SET = ARTICLES | PRONOUNS, frozenset(('by', 'from', 'cc', 'thanks', 'hi', 'hello', 'dear'))
_INIT_RE, _POSS_RE, _PRON_RE = re.compile(r'[.!?\n][\s"“‘*_(>#•-]*$'), re.compile(r"['’]s\b"), re.compile(r'\b(?:he|she|they|it|who|which)\b')
F_SLOT = f.slot


@lru_cache(maxsize=1)
def lexicon():
    return json.loads((f.ROOT / 'name_g_lexicon.json').read_text(encoding='utf-8'))['words']


def roots(word):
    """Candidate regular stems; dictionary membership disambiguates the alternatives."""
    out = [word]
    for ending in ('s', 'es', 'ed', 'ing'):
        if word.endswith(ending) and len(word) > len(ending) + 1:
            stem = word[:-len(ending)]
            out += [stem, stem + 'e']
            if len(stem) > 2 and stem[-1] == stem[-2]:
                out.append(stem[:-1])
    if word.endswith(('ies', 'ied')):
        out.append(word[:-3] + 'y')
    return list(dict.fromkeys(out))


@lru_cache(maxsize=8192)
def classes(word):
    """Aggregate ordinary senses, including OEWN irregular forms; preserve ambiguity."""
    vocab = lexicon()
    candidates = [word] if word in vocab else roots(word)
    pos, sem = Counter(), Counter()
    for lemma in candidates:
        if lemma in vocab:
            p, s = vocab[lemma]
            pos.update(p)
            sem.update(s)
    return pos, sem


def extra(text, ctx, i, j, channel='pron'):
    level = CHANNELS.index(channel)
    toks = ctx['toks']
    cache = ctx.setdefault('word_classes', {})
    def word_classes(word):
        if word not in cache:
            cache[word] = classes(word)
        return cache[word]
    before, after = f.gaps(text, toks, i, j)
    initial = not i or bool(_INIT_RE.search(before))
    def at(k):
        if not 0 <= k < len(toks):
            return ''
        a, b = sorted((k, i if k < i else j))
        return '' if f.END.search(text[toks[a][1]:toks[b][0]]) else toks[k][2].lower()
    left, right = at(i-1), at(j+1)
    out = ['g:start:%d' % initial, 'g:article:%d' % (left in ARTICLES)]
    out += ['g:cue:' + left] if left in _CUES_SET else []
    out += ['g:imperative:%d' % (initial and right in _IMPERATIVE_SET)]
    out += ['g:possessive:%d' % bool(_POSS_RE.match(after))]
    for label, k in (('l2', i-2), ('l1', i-1), ('r1', j+1), ('r2', j+2)):
        word = at(k)
        if not word:
            continue
        for suffix in ('s', 'ed', 'ing', 'ly', 'tion', 'ness', 'ous', 'ive', 'al'):
            if word.endswith(suffix):
                out.append('g:m:%s:%s' % (label, suffix))
        if level >= 1:
            p, s = word_classes(word) if toks[k][2].islower() else ({}, {})
            for prefix, counts in [('p', p)] + ([('s', s)] if level >= 2 else []):
                total = sum(counts.values())
                for key, count in sorted(counts.items()):
                    band = min(3, int(4 * count / total))
                    out += ['g:%s:%s:%s' % (prefix, label, key),
                            'g:%s:%s:%s:%d' % (prefix, label, key, band)]
                    if label == 'r1':
                        out.append('g:x:%d:%s:%s' % (initial, prefix, key))
    if level >= 1:
        for word in {t[2].lower() for t in toks[i:j+1]}:
            p, s = word_classes(word)
            out += ['g:self:p:' + key for key in sorted(p)]
            if level >= 2:
                out += ['g:self:s:' + key for key in sorted(s)]
        out.append('g:ordinary:%d' % min(3, max(f.bucket(ctx['freq'], t[2]) for t in toks[i:j+1])))
    if level >= 2:
        # A nearby role is weak evidence; punctuation and possessive remain separate features.
        for k in range(j+1, min(j+4, len(toks))):
            word = at(k)
            if word and 'noun.person' in word_classes(word)[1]:
                out += ['g:role:%d' % (k-j), 'g:apposition:%d' % (',' in after)]
    if level >= 3:
        # Same and next sentence only, bounded locally; this is not resolved coreference.
        start = max((m.end() for m in f.END.finditer(text, 0, toks[i][0])), default=0)
        end = min(len(text), toks[j][1]+320)
        stops = list(f.END.finditer(text, toks[j][1], end))
        if len(stops) >= 2:
            end = stops[1].end()
        newline = text.find('\n', toks[j][1], end)
        if newline >= 0:
            end = newline
        for word in sorted(set(_PRON_RE.findall(text[start:end].lower()))):
            out.append('g:pron:' + word)
        if right in ('who', 'which'):
            out.append('g:relative:' + right)
    return out


def slot(text, ctx, i, j, channel='pron'):
    return F_SLOT(text, ctx, i, j) + extra(text, ctx, i, j, channel)


class Backend(f.Backend):
    def __init__(self, data, threshold=0., add=False, propagate=False):
        super().__init__(data, threshold, add, propagate)
        self.channel = data['channel']
        if self.channel not in CHANNELS:
            raise ValueError('Invalid G feature channel')

    def score(self, text, ctx, i, j):
        z = self.bias + sum(self.weights.get(h, 0.) for h in f.hashed(slot(text, ctx, i, j, self.channel), self.mask))
        return 1. / (1. + math.exp(-max(-60., min(60., z))))


@lru_cache(maxsize=1)
def backend(name):
    if name != 'g_full_r1':
        raise ValueError('Unknown G operating point: ' + name)
    data = json.loads((f.ROOT / 'name_g_slot.json').read_text(encoding='utf-8'))
    return Backend(data, data['points']['r1'], add=True, propagate='confirmed')
