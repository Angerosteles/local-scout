"""Conservative paragraph language hints; ambiguous text uses both rule sets."""
from contextvars import ContextVar
import re

CACHE = ContextVar('scout_call_cache', default=None)
_WORD = re.compile(r'[^\W\d_]+')
_SPLIT = re.compile(r'\r?\n[ \t]*\r?\n')
_PL = frozenset('jest są nie się proszę prosze dziękuję dziekuje pan pani pana panem panie panu panią telefon kontakt adres raport kwota cena koszt zł pln numer moj wyslij zadzwon stacjonarny'.split())
_PL_CUES = frozenset('proszę prosze dziękuję dziekuje pani pana panem panie panu panią kontakt'.split())
_EN = frozenset('the an and is are of for from with your you please thank thanks hello call phone contact address report this that can will'.split())


def detect(text):
    cache = CACHE.get()
    if cache is not None and ('d', text) in cache:
        return cache[('d', text)]
    words = set(_WORD.findall(text.lower()))
    pl, en = len(words & _PL), len(words & _EN)
    res = 'both' if pl and en else 'pl' if pl >= 2 or words & _PL_CUES else 'en' if en >= 2 or 'contact' in words else 'both'
    if cache is not None:
        cache[('d', text)] = res
    return res


def paragraphs(text):
    cache = CACHE.get()
    if cache is not None and ('p', text) in cache:
        return iter(cache[('p', text)])
    out, start = [], 0
    for match in _SPLIT.finditer(text):
        out.append((start, text[start:match.start()]))
        start = match.end()
    out.append((start, text[start:]))
    if cache is not None:
        cache[('p', text)] = out
    return iter(out)


_OTHER = (
    frozenset('der die das und ist ein eine einer eines dem den des mit für nicht auf im von zu sind'.split()),
    frozenset('le la les et est une des du pour dans avec pas sont que sur'.split()),
    frozenset('el los las una del para con por que son es está como'.split()),
    frozenset('il gli le della delle per con sono che nella nel questo una'.split()),
)
_EN_PL = frozenset('the and to of is are for with this that you i w na się sie jest oraz proszę prosze nie'.split())

def paragraph_language(text, *, lexical_only=False, probabilities=None):
    """Require strong evidence of one foreign language; uncertain text retains coverage."""
    words = re.findall(r'[^\W\d_]+', text.lower())
    foreign = max((sum(w in vocabulary for w in words) for vocabulary in _OTHER), default=0)
    familiar = sum(w in _EN_PL for w in words)
    if len(words) >= 8 and foreign >= 3 and familiar <= 1 and foreign >= 3 * max(familiar, 1):
        return 'other'
    if words and words[0] in ('cześć', 'czesc', 'witaj', 'witam', 'hej'):
        return 'pl'
    hint = detect(text)
    if hint == 'pl' and any(w in {'hi', 'hello', 'please', 'thanks'} for w in words):
        return 'uncertain'
    if hint in ('pl', 'en'):
        return hint
    if len(words) < 8 or lexical_only:
        return 'uncertain'
    from . import paragraph_context
    try:
        if probabilities is None:
            probabilities = paragraph_context.classify(text)['lang']
    except Exception:
        return 'uncertain'
    if len(words) >= 8 and probabilities.get('PL', 0) >= .85:
        return 'pl'
    return 'uncertain'
