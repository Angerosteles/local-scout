"""Optional client for a local basal engine (basal-serve by Remigiusz Kinas, Apache-2.0).

Local Scout never downloads, starts or configures the engine. The operator runs, for example,
``basal-serve --mode gguf --host 127.0.0.1 --port 8765 ...`` on the same machine and registers it with
``configure --basal-url http://127.0.0.1:8765``. Only loopback IP addresses are accepted and proxies are
bypassed, so text stays on the machine. Triage texts are masked before they are sent; the credential second
opinion (``credential_probability``) sends the unmasked line around a candidate, because it has to see the value.

basal answers a typed ``choice`` question with a probability for every allowed option. Local Scout adds an
``other`` option, refuses when ``other`` wins, and accepts the best label only above a threshold chosen on
validation data, exactly like its other triage methods (``learning.train``). The refusal option has a
reserved id (``none_of_these``), so a user class called ``other`` keeps its meaning. Redirects are refused
(a local server must not be able to forward text elsewhere) and answers are size-limited.
"""
import ipaddress
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

OTHER = 'none_of_these'  # reserved refusal option; a dataset class with this id is rejected
OTHER_DESCRIPTION = 'none of the listed topics'
MAX_RESPONSE_BYTES = 1 << 20
QUESTION = 'Which topic is this customer message about?'
TIMEOUT_SECONDS = 120
MAX_DESCRIPTION = 200
SAFE_LABEL = re.compile(r'^[a-z0-9_:.-]{1,64}$')
# A freshly started engine can answer 503 for a moment after it reports ready.
RETRY_STATUS = {503}
RETRIES = 3


class Unavailable(Exception):
    """The configured engine did not answer with a usable decision."""


class ReservedLabel(ValueError):
    """A dataset class uses the id reserved for the refusal option."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Unavailable(f'the local basal engine answered with a redirect (HTTP {code}); redirects are refused')


def check_url(url):
    """Return the normalised base URL, or raise ValueError unless it is http://<loopback IP>:<port>."""
    if not isinstance(url, str) or len(url) > 100:
        raise ValueError('basal_url must be http://127.0.0.1:<port>')
    parts = urllib.parse.urlsplit(url.strip())
    if (parts.scheme != 'http' or parts.username or parts.password or parts.query or parts.fragment
            or parts.path not in ('', '/')):
        raise ValueError('basal_url must be http://127.0.0.1:<port>')
    try:
        loopback = ipaddress.ip_address(parts.hostname or '').is_loopback
        port = parts.port
    except ValueError:
        loopback, port = False, None
    if not loopback or not port:
        raise ValueError('basal_url must be a loopback IP address with a port, e.g. http://127.0.0.1:8765')
    host = f'[{parts.hostname}]' if ':' in parts.hostname else parts.hostname
    return f'http://{host}:{port}'


def check_descriptions(descriptions):
    """Validate optional one-line category descriptions {label: text}; returns a clean dict."""
    if descriptions is None:
        return {}
    if not isinstance(descriptions, dict) or len(descriptions) > 256:
        raise ValueError('descriptions must map up to 256 labels to text')
    clean = {}
    for label, text in descriptions.items():
        if (not isinstance(label, str) or not SAFE_LABEL.fullmatch(label) or label == OTHER
                or not isinstance(text, str) or not 1 <= len(text.strip()) <= MAX_DESCRIPTION):
            raise ValueError('each description needs a label id and 1-200 characters of text')
        clean[label] = ' '.join(text.split())
    return clean


def criteria(classes, descriptions=None):
    """Allowed answers for basal: one entry per label plus ``other``."""
    descriptions = descriptions or {}
    out = {label: descriptions.get(label) or label.replace('_', ' ') for label in classes}
    out[OTHER] = OTHER_DESCRIPTION
    return out


def _ask(base, body):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    request = urllib.request.Request(base + '/v1/systemone', data=json.dumps(body).encode(),
                                     headers={'content-type': 'application/json'})
    for attempt in range(RETRIES):
        try:
            with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                data = response.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES:
                raise Unavailable('the local basal engine answer is larger than 1 MB')
            return json.loads(data)
        except Unavailable:
            raise
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRY_STATUS or attempt == RETRIES - 1:
                raise Unavailable(f'the local basal engine answered HTTP {exc.code}') from None
        except (OSError, ValueError) as exc:
            raise Unavailable('the local basal engine did not answer') from exc
        time.sleep(1)
    raise Unavailable('the local basal engine did not answer')


def decide(url, texts, classes, descriptions=None):
    """Return one ``(label_index or None, probability)`` per text.

    ``None`` means ``other`` (or no listed label) had the highest probability: the caller must refuse."""
    base = check_url(url)
    allowed = criteria(classes, descriptions)
    results = []
    for text in texts:
        body = {'state': text, 'questions': {'topic': {'type': 'choice', 'criteria': allowed, 'instructions': QUESTION}}}
        answer = _ask(base, body)
        try:
            probabilities = answer['answers']['topic']['probabilities']
            # Every allowed option must be present: a missing one is not a zero.
            scores = [float(probabilities[label]) for label in classes]
            other = float(probabilities[OTHER])
        except (KeyError, TypeError, ValueError, AttributeError):
            raise Unavailable('the local basal engine returned an incomplete or unexpected answer') from None
        if any(not 0.0 <= s <= 1.0 for s in scores + [other]):
            raise Unavailable('the local basal engine returned probabilities outside [0, 1]')
        best = max(range(len(scores)), key=scores.__getitem__)
        results.append((None if other >= scores[best] else best, scores[best]))
    return results


CREDENTIAL_QUESTION = 'Does this text contain a usable credential value?'
CREDENTIAL_CRITERIA = {
    'yes': 'the text contains an actual password, PIN, passphrase or access code value that someone could use to log '
           'in or unlock something',
    'no': 'the text contains no usable credential value (it may still talk about passwords in general)'}
CREDENTIAL_TIMEOUT_SECONDS = 8


def credential_probability(url, text, timeout=CREDENTIAL_TIMEOUT_SECONDS):
    """Probability that one line holds a usable credential (masking second opinion; one try, short timeout).

    Raises ``Unavailable`` on any failure; the caller then falls back to its deterministic threshold."""
    base = check_url(url)
    body = {'state': text[:2000], 'questions': {'q': {'type': 'choice', 'criteria': CREDENTIAL_CRITERIA,
                                                      'instructions': CREDENTIAL_QUESTION}}}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    request = urllib.request.Request(base + '/v1/systemone', data=json.dumps(body).encode(),
                                     headers={'content-type': 'application/json'})
    try:
        with opener.open(request, timeout=timeout) as response:
            data = response.read(MAX_RESPONSE_BYTES + 1)
        if len(data) > MAX_RESPONSE_BYTES:
            raise Unavailable('the local basal engine answer is larger than 1 MB')
        value = float(json.loads(data)['answers']['q']['probabilities']['yes'])
    except Unavailable:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise Unavailable('the local basal engine did not answer') from exc
    if not 0.0 <= value <= 1.0:
        raise Unavailable('the local basal engine returned a probability outside [0, 1]')
    return value


def credential_checker(config, budget=None):
    """Second opinion for uncertain password-like values from the registered local basal engine, or None.

    One checker serves one tool result or prompt (every text field in it): calls share one deadline, and each call's
    timeout is cut to the time left, so the whole check stays within ``budget`` seconds (default: the credentials
    TIME_BUDGET) plus connection overhead. Past the deadline it raises ``Unavailable``; the caller then keeps its
    deterministic threshold."""
    url = config.get('basal_url') if isinstance(config, dict) else None
    if not url:
        return None
    from . import credentials
    deadline = time.monotonic() + (credentials.TIME_BUDGET if budget is None else budget)

    def check(text):
        left = deadline - time.monotonic()
        if left <= 0.2:
            raise Unavailable('the time budget for the local basal engine is spent')
        return credential_probability(url, text, timeout=min(CREDENTIAL_TIMEOUT_SECONDS, left))
    check.memo = {}      # answers per candidate, shared by every scan that uses this checker
    return check
