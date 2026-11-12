"""Redaction and coverage failures for tool-result transform hooks."""
import json
import base64
import binascii
import bisect
import re
from contextvars import ContextVar

from . import privacy, injection

# Scan-failure policy of the profile being served, set per request/result by the caller from
# settings.json ('guard_mode', written by `scout configure --guard-mode`). Default: fail-closed.
GUARD_OPEN = ContextVar('local_scout_guard_open', default=False)

MAX_INPUT_CHARS = 256 * 1024

# High confidence, imperative patterns. Kept local to tool-result transformation;
# ordinary prose about security concepts is not enough to trigger an alert.
PROMPT_INJECTION_PATTERNS = (
    re.compile(r"(?i)\b(?:ignore|disregard|forget|delete|override|bypass|stop following)\b.{0,55}\b(?:previous|prior|above|earlier|all|your|the)\b.{0,40}\b(?:text|tasks?|orders?|information|assignments?|directions?|requests?|restrictions?|instructions?|rules?|prompts?)\b"),
    re.compile(r"(?i)\b(?:reveal|print|show|repeat|list|output|tell me|provide|give me|what(?:'s| is| are))\b.{0,70}\b(?:password|secret|system prompt|(?:original|initial|hidden|previous|your) instructions?)\b"),
    re.compile(r"(?i)\b(?:forget everything|ignore above|pretend you are|you are now|act as|new task)\b"),
    re.compile(r"(?i)\b(?:ignore|disregard|forget|override|bypass)\s+(?:(?:all|any|the|your|previous|prior|above|earlier|system|developer)\s+){0,3}(?:instructions?|rules?|prompts?|safety(?:\s+rules?)?)\b"),
    re.compile(r"(?i)\byou\s+are\s+now\s+(?:an?\s+)?(?:system|developer|assistant|admin|root|unrestricted|jailbroken)\b"),
    re.compile(r"(?i)\b(?:reveal|print|show|repeat|output|expose)\s+(?:the\s+)?(?:hidden\s+|full\s+)?system\s+prompt\b"),
)
_BASE64_BLOCK = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{32,4096}={0,2}(?![A-Za-z0-9+/])")
_EDUCATIONAL_CONTEXT = re.compile(r"(?i)\b(?:example|phrase|quoted|documentation|explains?|describes?|discusses?)\b")

# Hidden content: an HTML comment, an element hidden by `hidden` / `display:none` /
# `visibility:hidden`, or a markdown comment line, followed within 2000 characters by an
# imperative trigger and, within 2000 characters after that trigger, by an object word.
#
# Up to 1.7.4 these were three regexes, `<!--.{0,2000}\bTRIGGER\b.{0,2000}?OBJECT\b.*?--\s*>`
# and similar; on long text that repeats an opener and a trigger without ever closing, the
# engine retried the bounded wildcards against each other and one 256 KB result could take
# minutes. The scan below finds every opener, trigger, object and closer once and answers each
# opener with binary searches (O(n log n) in the input; no rescanning per opener). It reproduces the
# old matches (start, end and therefore the educational-context check) with one deliberate
# change: an opener that is never closed is still checked (the old regexes required the close).
_HIDDEN_WINDOW = 2000
_HIDDEN_TRIGGER = re.compile(r"(?i)\b(?:ignore|disregard|override|reveal|send|exfiltrate)\b")
_HIDDEN_OBJECT = re.compile(r"(?i)(?:instructions?|prompt|secret|data|http)\b")
_COMMENT_CLOSE = re.compile(r"--\s*>")
_HIDDEN_MARKER = re.compile(r"(?i)display\s*:\s*none|visibility\s*:\s*hidden|hidden")
_MD_COMMENT = re.compile(r"(?i)\[(?://|comment)\]:\s*#\s*\(")
_LEADING_SPACE = re.compile(r"\s*")


class _HiddenScan:
    """Positions of triggers, objects and closers in one text, shared by the three openers."""

    def __init__(self, text):
        self.text = text
        triggers = [(m.start(), m.end()) for m in _HIDDEN_TRIGGER.finditer(text)]
        objects = [(m.start(), m.end()) for m in _HIDDEN_OBJECT.finditer(text)]
        self.t_start = [s for s, _ in triggers]
        self.o_start = [s for s, _ in objects]
        self.o_end = [e for _, e in objects]
        # For each trigger: the first object starting at or after its end, if within the window.
        self.first_object = []
        valid = []
        for _, t_end in triggers:
            k = bisect.bisect_left(self.o_start, t_end)
            self.first_object.append(k)
            valid.append(k < len(objects) and objects[k][0] - t_end <= _HIDDEN_WINDOW)
        self.t_end = [e for _, e in triggers]
        # prev_valid[i]: largest j <= i with an object in reach of trigger j, else -1.
        self.prev_valid, last = [], -1
        for i, ok in enumerate(valid):
            last = i if ok else last
            self.prev_valid.append(last)
        # Object ends of each trigger's first object never decrease with the trigger index.
        self.first_object_end = [self.o_end[k] if k < len(objects) else len(text) + 1
                                 for k in self.first_object]
        self.closers = [(m.start(), m.end()) for m in _COMMENT_CLOSE.finditer(text)]
        self.parens = [m.start() for m in re.finditer(r'\)', text)]

    def last_trigger(self, lo, hi, object_end_limit=None):
        """Rightmost trigger starting in [lo, hi] with an object in reach (ending by the limit)."""
        i1 = bisect.bisect_right(self.t_start, hi) - 1
        if object_end_limit is not None:
            i1 = min(i1, bisect.bisect_right(self.first_object_end, object_end_limit) - 1)
        if i1 < 0:
            return None
        j = self.prev_valid[i1]
        return j if j >= 0 and self.t_start[j] >= lo else None

    def comments(self):
        last_end, close_starts = 0, [s for s, _ in self.closers]
        last_close = close_starts[-1] if close_starts else -1
        for m in re.finditer('<!--', self.text):
            p = m.start()
            if p < last_end:
                continue
            lo, hi = p + 4, p + 4 + _HIDDEN_WINDOW
            if last_close >= lo:
                # Closed somewhere later: the old rule exactly. Rightmost trigger whose object is
                # followed by a close; the match ends at that close.
                i = self.last_trigger(lo, hi, object_end_limit=last_close)
                if i is None:
                    continue
                k = bisect.bisect_left(close_starts, self.first_object_end[i])
                end = self.closers[k][1]
            else:
                # Never closed (the browser hides the rest of the page): still hidden content.
                i = self.last_trigger(lo, hi)
                if i is None:
                    continue
                end = self.first_object_end[i]
            last_end = end
            yield p, end

    def hidden_elements(self):
        text, last_end = self.text, 0
        p = text.find('<')
        while p != -1:
            g = text.find('>', p)
            if g == -1:
                return
            # Every '<' before this '>' gives the same tag end; the leftmost one has the widest body.
            if p >= last_end and _HIDDEN_MARKER.search(text, p + 2, g):
                lo = g + 1 + _LEADING_SPACE.match(text, g + 1).end() - (g + 1)
                i = self.last_trigger(lo, lo + _HIDDEN_WINDOW)
                if i is not None:
                    end = self.first_object_end[i]
                    last_end = end
                    yield p, end
            p = text.find('<', max(g + 1, last_end))

    def markdown_comments(self):
        text, last_end = self.text, 0
        for m in _MD_COMMENT.finditer(text):
            q = m.start()
            # `(?:^|\n)\s*[...]`: only whitespace back to a newline or to the start of the text.
            j = q - 1
            start = None
            while j >= 0 and text[j].isspace():
                if text[j] == '\n':
                    start = j
                j -= 1
            if j < 0:
                start = 0
            if start is None or start < last_end:
                continue
            c = m.end()
            k = bisect.bisect_left(self.parens, c)        # first ')' after the opener
            r = self.parens[k] if k < len(self.parens) else -1
            closed = r != -1
            limit = r if closed else len(text)
            i = self.last_trigger(c, min(c + _HIDDEN_WINDOW, limit - 1), object_end_limit=limit)
            if i is None:
                continue
            if closed:
                end = r + 1
            else:   # never closed: end at the last object in reach of the trigger
                k = bisect.bisect_right(self.o_start, self.t_end[i] + _HIDDEN_WINDOW) - 1
                end = self.o_end[k]
            last_end = end
            yield start, end

    def spans(self):
        yield from self.comments()
        yield from self.hidden_elements()
        yield from self.markdown_comments()


# Exfiltration: a verb, then (on the same line) a sensitive object and a URL in either order:
#   verb .{0,180} OBJECT .{0,120} URL     and     verb .{0,180} URL .{0,120} OBJECT
# Same matches as the two regexes used up to 1.7.4 (`.` does not cross a newline there), but
# answered from token positions: on long repeats of `send data` or `send https://` those regexes
# spent about a second per 256 KB result in backtracking.
_EXFIL_VERB = re.compile(r"(?i)\b(?:send|email|upload|post|exfiltrate|forward)\b")
_EXFIL_OBJECT = re.compile(r"(?i)\b(?:secrets?|credentials?|tokens?|environment|files?|data|contents?|system\s+prompt)\b")
_EXFIL_URL = re.compile(r"(?i)\bhttps?://")


def _exfiltration_spans(text):
    newlines = [m.start() for m in re.finditer('\n', text)]

    def line_end(pos):          # `.` does not cross a newline in these rules
        k = bisect.bisect_left(newlines, pos)
        return newlines[k] if k < len(newlines) else len(text)

    verbs = [(m.start(), m.end()) for m in _EXFIL_VERB.finditer(text)]
    objects = [(m.start(), m.end()) for m in _EXFIL_OBJECT.finditer(text)]
    urls = [(m.start(), m.end()) for m in _EXFIL_URL.finditer(text)]
    for first, second in ((objects, urls), (urls, objects)):
        f_start = [s for s, _ in first]
        s_start = [s for s, _ in second]
        # prev_valid[i]: largest j <= i whose token is followed by one of the other kind on the
        # same line within 120 characters (independent of the verb).
        prev_valid, last = [], -1
        for i, (_, f_end) in enumerate(first):
            k = bisect.bisect_left(s_start, f_end)
            if k < len(second) and s_start[k] <= min(f_end + 120, line_end(f_end)):
                last = i
            prev_valid.append(last)
        last_end = 0
        for v_start, v_end in verbs:
            if v_start < last_end:
                continue
            # greedy `.{0,180}`: the rightmost valid token starting within reach on the verb's line
            i = bisect.bisect_right(f_start, min(v_end + 180, line_end(v_end))) - 1
            i = prev_valid[i] if i >= 0 else -1
            if i < 0 or f_start[i] < v_end:
                continue
            # greedy `.{0,120}`: the rightmost token of the other kind in reach, same line
            f_end = first[i][1]
            k = bisect.bisect_right(s_start, min(f_end + 120, line_end(f_end))) - 1
            last_end = second[k][1]
            yield v_start, last_end


def _educational(text, start, end):
    before = text[max(0, start-100):start]
    after = text[end:min(len(text), end+100)]
    return bool(_EDUCATIONAL_CONTEXT.search(before) and _EDUCATIONAL_CONTEXT.search(after))


def _prompt_injection(text):
    # same Unicode normalisation as PII masking (zero-width, full-width, hyphens).
    text = privacy._normalise_for_detection(text)[0]
    for pattern in PROMPT_INJECTION_PATTERNS:
        for match in pattern.finditer(text):
            if not _educational(text, match.start(), match.end()):
                return True
    for start, end in _HiddenScan(text).spans():
        if not _educational(text, start, end):
            return True
    for start, end in _exfiltration_spans(text):
        if not _educational(text, start, end):
            return True
    # Decode only bounded, syntactically valid base64 candidates. Never retain
    # or emit decoded content; scan it with the same imperative rules.
    for match in _BASE64_BLOCK.finditer(text):
        block = match.group()
        if len(block) % 4:
            block += "=" * (4 - len(block) % 4)
        try:
            decoded = base64.b64decode(block, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
        if any(pattern.search(decoded) for pattern in PROMPT_INJECTION_PATTERNS):
            return True
        if next(_HiddenScan(decoded).spans(), None) is not None or next(_exfiltration_spans(decoded), None) is not None:
            return True
    return False


def withhold(value, reason, force_closed=False):
    """Content-free alert used instead of output that could not be scanned (fail-closed).

    `force_closed` is for callers that could not read their own profile settings: they must not
    inherit an `open` policy from an enclosing call for another profile."""
    if GUARD_OPEN.get() and not force_closed:
        return None, (), True
    size = len(value.encode("utf-8", errors="surrogatepass")) if isinstance(value, str) else 0
    return f"LOCAL_SCOUT_ALERT v1; action=withheld; reason={reason}; type={type(value).__name__}; size_bytes={size}", (), True


def _transform(value, source, event_id):
    """Return ``(replacement, categories, partial)`` without retaining matched text."""
    if not isinstance(value, str):
        return withhold(value, "unsupported_type")
    if len(value.encode("utf-8", errors="surrogatepass")) > MAX_INPUT_CHARS:
        return withhold(value, "oversize")

    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, ValueError):
        parsed = None
        is_json = False
    else:
        is_json = True

    if is_json and isinstance(parsed, (int, float)) and not isinstance(parsed, bool):
        # A top-level number can carry PII (e.g. a card number): scan it as text.
        is_json = False
    if is_json and not isinstance(parsed, (str, list, dict)):
        return withhold(value, "unsupported_json_type")

    found = set()
    marker_slot = "\x00LOCAL_SCOUT_MARKER_SLOT\x00"
    slot_used = False

    def redact(text, field=None):
        nonlocal slot_used
        replacement, masked_categories = privacy.mask_local_details(text, field=field)
        if masked_categories:
            found.update(masked_categories)
        if _prompt_injection(text) or injection.is_injection(privacy._normalise_for_detection(text)[0]):
            found.add("prompt_injection")
        if found and not slot_used and not is_json:
            replacement += ("" if replacement.endswith("\n") else "\n") + marker_slot
            slot_used = True
        return replacement

    try:
        if is_json:
            def visit(item, field=None):
                if isinstance(item, str):
                    return redact(item, field)
                if isinstance(item, list):
                    return [visit(child, field) for child in item]
                if isinstance(item, dict):
                    result = {}
                    for key, child in item.items():
                        clean_key = redact(key)
                        if clean_key in result or clean_key == '_local_scout_alert':
                            raise ValueError("ambiguous JSON keys")
                        result[clean_key] = visit(child, key)   # the key gives context ("otp_code": "543918")
                    return result
                if item is None or isinstance(item, bool):
                    return item
                if isinstance(item, (int, float)):
                    # Numbers are scanned as text (e.g. a card number stored as a number); without a match they
                    # stay numbers, otherwise every read_file result (total_lines) would be withheld.
                    masked_number = redact(str(item), field)
                    return masked_number if masked_number != str(item) else item
                raise ValueError("unscanned JSON scalar")

            changed = visit(parsed)
            if not found:
                return None, (), False
            categories = tuple(sorted(found))
            action = "masked+flagged" if "prompt_injection" in found and len(found) > 1 else ("flagged" if "prompt_injection" in found else "masked")
            marker = (f"LOCAL_SCOUT_ALERT v1; categories={','.join(categories)}; action={action}; "
                      f"source={source}; event={event_id}")

            def fill_slot(item):
                if isinstance(item, str):
                    return item.replace(marker_slot, marker)
                if isinstance(item, list):
                    return [fill_slot(child) for child in item]
                if isinstance(item, dict):
                    return {key: fill_slot(child) for key, child in item.items()}
                return item

            changed = fill_slot(changed)
            if isinstance(changed, dict):
                changed['_local_scout_alert'] = marker
            elif isinstance(changed, list):
                changed.append(marker)
            else:
                changed += "\n" + marker
            body = json.dumps(changed, ensure_ascii=False, separators=(",", ":"))
        else:
            body = redact(value)
            if not found:
                return None, (), False
            categories = tuple(sorted(found))
            action = "masked+flagged" if "prompt_injection" in found and len(found) > 1 else ("flagged" if "prompt_injection" in found else "masked")
            marker = (f"LOCAL_SCOUT_ALERT v1; categories={','.join(categories)}; action={action}; "
                      f"source={source}; event={event_id}")
            body = body.replace(marker_slot, marker)
    except Exception:
        return withhold(value, "scan_failed_or_partial")

    return body, categories, False


def transform(value, source, event_id):
    """Convert any scan failure into a content-free coverage alert."""
    try:
        return _transform(value, source, event_id)
    except Exception:
        return withhold(value, "scan_failed")
