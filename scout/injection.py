"""Offline bounded classifier for tool-output injection alerts.

numpy/scikit-learn are imported lazily: the operator CLI may run under an interpreter without the plugin's
dependencies, and plugin loading must never fail because of this optional detector. Without them the
classifier reports itself unavailable and the deterministic rules in tool_guard remain active.
"""
from functools import lru_cache
from pathlib import Path

WINDOW = 2000
MAX_WINDOWS = 3


def vectorizer():
    import numpy as np
    from sklearn.feature_extraction.text import HashingVectorizer
    return HashingVectorizer(n_features=32768, analyzer='char', ngram_range=(3, 5), lowercase=True,
                             alternate_sign=True, norm='l2', dtype=np.float32)


@lru_cache(maxsize=1)
def _model():
    import numpy as np
    with np.load(Path(__file__).parent / 'models' / 'injection.npz', allow_pickle=False) as data:
        return data['weights'], float(data['intercept']), float(data['threshold'])


def available():
    try:
        _model()
        vectorizer()
        return True
    except Exception:
        return False


def score(text):
    import numpy as np
    weights, intercept, _ = _model()
    if len(text) <= WINDOW:
        windows = [text]
    else:
        count = min(MAX_WINDOWS, (len(text) + WINDOW - 1) // WINDOW)
        starts = [i * (len(text) - WINDOW) // (count - 1) for i in range(count)]
        windows = [text[i:i + WINDOW] for i in starts]
    return float(np.max(vectorizer().transform(windows) @ weights) + intercept)


def is_injection(text):
    try:
        return score(text) >= _model()[2]
    except ImportError:
        # Dependencies missing in this interpreter: rules-only detection (documented limitation).
        return False
