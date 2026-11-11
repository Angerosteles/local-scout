"""Person-name detector for user prompts (pure Python).

Hashed-feature logistic token classifier exported to JSON (models/name_model.json): character n-grams,
capitalisation, context cue words and lowercase frequency in ordinary text. A token is a name above
HIGH; an adjacent capitalised token joins a detected name above LOW. Trained on the Gretel validation
split (Apache-2.0, synthetic) and measured on the unused test split plus held-out negatives — see meta
in the JSON. Detection is incomplete; titles, product names and code identifiers may be over-masked.
"""
from functools import lru_cache
import json
import math
from pathlib import Path
import re
import zlib

TOKEN = re.compile(r"[A-Za-z][A-Za-z'\-]*")
MAX_TEXT = 262144


@lru_cache(maxsize=1)
def _model():
    data = json.loads((Path(__file__).parent / 'models' / 'name_model.json').read_text(encoding='utf-8'))
    return (data['weights'], float(data['bias']), data['lowfreq'], frozenset(data['cues']), (1 << int(data['nbits'])) - 1,
            float(data['meta']['high']), float(data['meta']['low']))


def _h(value, mask):
    return zlib.crc32(value.encode('utf-8')) & mask


def _features(text, toks, i, lowfreq, cues, mask):
    s, e, w = toks[i]
    lw = w.lower()
    f = []
    b = '^' + lw + '$'
    for n in (2, 3, 4):
        for k in range(len(b) - n + 1):
            f.append('c%d:%s' % (n, b[k:k + n]))
    f.append('len:%d' % min(len(w), 12))
    f.append('allcaps' if w.isupper() and len(w) > 1 else 'title' if w[1:].islower() else 'mixed')
    if '-' in w:
        f.append('hyphen')
    f.append('lf:%d' % min(int(math.log2(lowfreq.get(lw, 0) + 1)), 12))
    prev = toks[i - 1] if i > 0 else None
    nxt = toks[i + 1] if i + 1 < len(toks) else None
    gap_prev = text[prev[1]:s] if prev else text[:s]
    f.append('start' if not prev or re.search(r'[.!?\n]\s*$', gap_prev) else 'mid')
    if re.search(r':\s*$', gap_prev):
        f.append('colon_before')
    if prev:
        pl = prev[2].lower()
        f.append('p:' + pl if pl in cues else 'pcap' if prev[2][:1].isupper() else 'plow')
        if pl in cues:
            f.append('cue')
    if i > 1 and toks[i - 2][2].lower() in cues:
        f.append('pp:' + toks[i - 2][2].lower())
    if nxt:
        f.append('ncap' if nxt[2][:1].isupper() and text[e:nxt[0]] in (' ', '-') else 'nlow')
        f.append('n:' + nxt[2].lower() if nxt[2].lower() in cues else 'n:other')
    else:
        f.append('end')
    return [_h(x, mask) for x in f]


def spans(text):
    """Return [(start, end, 'name')] for detected person names; never raises on ordinary text."""
    if not isinstance(text, str) or not text or len(text) > MAX_TEXT or not re.search(r'[A-Z]', text):
        return []
    weights, bias, lowfreq, cues, mask, high, low = _model()
    toks = [(m.start(), m.end(), m.group()) for m in TOKEN.finditer(text)]
    cand = [i for i, t in enumerate(toks) if t[2][:1].isupper() and len(t[2]) >= 2]
    if not cand:
        return []
    prob = {}
    for i in cand:
        z = bias + sum(weights[f] for f in _features(text, toks, i, lowfreq, cues, mask))
        prob[i] = 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, z))))
    flagged = {i for i, p in prob.items() if p >= high}
    changed = True
    while changed:
        changed = False
        for i, p in prob.items():
            if i in flagged or p < low:
                continue
            for j in (i - 1, i + 1):
                if j in flagged:
                    a, b = sorted((i, j))
                    if text[toks[a][1]:toks[b][0]] in (' ', '-', ' - '):
                        flagged.add(i); changed = True
                        break
    result = []
    for i in sorted(flagged):
        s, e, _ = toks[i]
        if result and text[result[-1][1]:s] in (' ', '-', ' - '):
            result[-1] = (result[-1][0], e, 'name')
        else:
            result.append((s, e, 'name'))
    return result
