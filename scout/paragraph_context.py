"""Offline paragraph context probabilities; no runtime dependencies beyond Python."""
import json, math, re
from functools import lru_cache
from operator import mul
from pathlib import Path

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_PL = frozenset("i w z na do za od po dla oraz ale nie się sie jest są sa to jak że ze czy proszę prosze dziękuję dziekuje pozdrawiam cześć czesc dzień dzien dobry możesz mozesz mam mamy ten ta tym jego jej".split())
_EN = frozenset("the a an and in on to of for from with at by is are was were be it this that we you your i please thanks thank regards hello hi dear can will have has as but or".split())
_GREET = re.compile(r"\b(?:hi|hello|dear|hey|cześć|czesc|hej|dzień dobry|dzien dobry|witam)\b", re.I)
_SIGN = re.compile(r"\b(?:regards|sincerely|cheers|best|pozdrawiam|dziękuję|dziekuje|dzięki|dzieki)\b", re.I)
_TECH = re.compile(r"\b(?:error|warning|debug|exception|failed|status|server|http|api|config|log|commit|build|docker|błąd|blad|serwer|konfiguracja)\b", re.I)
_INDENT = re.compile(r"\s+\S")
_URL = re.compile(r"https?://|www\.")
_PATH = re.compile(r"(?:^|\s)(?:/|\./|[A-Za-z]:\\)\S+", re.M)
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]{1,64}@(?=[\w.-]{1,255}(?![\w.-]))[\w.-]{1,252}\.[A-Za-z]{2,63}")
_KV = re.compile(r"^\s*[\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ -]{1,30}:\s*\S", re.M)
_LIST = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+", re.M)
_PIPE = re.compile(r"^[^|\n]*\|[^|\n]*\|.*$", re.M)
_ALNUM_DIGIT = re.compile(r"\b\w*\d\w*\b")
_SENT_END = re.compile(r"[.!?](?:\s|$)", re.M)
_BLANK = re.compile(r"\n\s*\n")
_CAPS_FLAG = re.compile(r"(?:\b[A-Z_]{2,}\b|--[\w-]+)")


def features(text):
    """Calculate 32 bounded or log-scaled features in a fixed order."""
    low, lines = text.lower(), text.splitlines() or [""]
    words = _WORD.findall(low)
    n, nw, nl, is_ascii = max(len(text), 1), max(len(words), 1), len(lines), text.isascii()
    digits = sum(map(text.count, "0123456789")) if is_ascii else sum(c.isdigit() for c in text)
    uppers = sum(map(text.count, "ABCDEFGHIJKLMNOPQRSTUVWXYZ")) if is_ascii else sum(c.isupper() for c in text)
    pl_count, en_count = sum(w in _PL for w in words), sum(w in _EN for w in words)
    punct = text.count(".") + text.count("!") + text.count("?")
    return [math.log1p(len(text)), math.log1p(len(words)), math.log1p(nl),
            sum(bool(_INDENT.match(x)) for x in lines) / nl, digits / n, uppers / n, punct / n, text.count(":") / n,
            sum(map(text.count, "()[]{}")) / n, sum(map(text.count, "=<>|")) / n, sum(map(text.count, "/\\_")) / n,
            text.count(",") / n, (("http" in low or "www." in low) and bool(_URL.search(text))),
            (("/" in text or "\\" in text) and bool(_PATH.search(text))), ("@" in text and bool(_EMAIL.search(text))),
            (len(_KV.findall(text)) / nl if ":" in text else 0.0), len(_LIST.findall(text)) / nl,
            (len(_PIPE.findall(text)) / nl if "|" in text else 0.0), bool(_GREET.search(low)), bool(_SIGN.search(low)),
            pl_count / nw, en_count / nw, (0.0 if is_ascii else sum(map(low.count, "ąćęłńóśźż")) / n),
            pl_count > 0, en_count > 0, bool(_TECH.search(low)),
            (len(_ALNUM_DIGIT.findall(text)) / nw if digits else 0.0), (len(_SENT_END.findall(text)) / nw if punct else 0.0),
            sum(map(len, words)) / nw / 20, ("\n" in text and bool(_BLANK.search(text))), text.count(";") / n,
            (len(_CAPS_FLAG.findall(text)) / nw if (uppers or "_" in text or "--" in text) else 0.0)]


@lru_cache(maxsize=1)
def _load():
    with (Path(__file__).resolve().parent / "models" / "paragraph_context.json").open(encoding="utf-8") as handle:
        return json.load(handle)


def classify(paragraph, *, weights=None):
    """Return class distributions and normalized entropy (0 certain, 1 uncertain)."""
    if not isinstance(paragraph, str):
        raise TypeError("paragraph must be a string")
    values = features(paragraph)
    result, uncertainty, source = {}, {}, weights if weights is not None else _load()
    for task in ("type", "lang"):
        model = source[task]
        standardized = [(x - m) / s for x, m, s in zip(values, model["mean"], model["scale"])]
        logits = [b + sum(map(mul, row, standardized)) for row, b in zip(model["coef"], model["intercept"])]
        peak = max(logits)
        exps = [math.exp(x - peak) for x in logits]
        total = sum(exps)
        probabilities = [x / total for x in exps]
        result[task] = dict(zip(model["classes"], probabilities))
        uncertainty[task] = (-sum(p * math.log(p) for p in probabilities if p) / math.log(len(probabilities))
                             if len(probabilities) > 1 else 0.)
    result["uncertainty"] = uncertainty
    return result
