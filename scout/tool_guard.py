"""Redaction and coverage failures for tool-result transform hooks."""
import json
import os
import base64
import binascii
import re

from . import privacy, injection

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
    re.compile(r"(?is)<!--.{0,2000}\b(?:ignore|disregard|override|reveal|send|exfiltrate)\b.{0,2000}?(?:instructions?|prompt|secret|data|http)\b.*?--\s*>"),
    re.compile(r"(?is)<[^>]+(?:display\s*:\s*none|visibility\s*:\s*hidden|hidden)[^>]*>\s*.{0,2000}\b(?:ignore|disregard|override|reveal|send|exfiltrate)\b.{0,2000}?(?:instructions?|prompt|secret|data|http)\b"),
    re.compile(r"(?is)(?:^|\n)\s*\[(?://|comment)\]:\s*#\s*\([^)]{0,2000}\b(?:ignore|disregard|override|reveal|send|exfiltrate)\b[^)]{0,2000}(?:instructions?|prompt|secret|data|http)\b[^)]*\)"),
    re.compile(r"(?i)\b(?:send|email|upload|post|exfiltrate|forward)\b.{0,180}\b(?:secrets?|credentials?|tokens?|environment|files?|data|contents?|system\s+prompt)\b.{0,120}\bhttps?://"),
    re.compile(r"(?i)\b(?:send|email|upload|post|exfiltrate|forward)\b.{0,180}\bhttps?://.{0,120}\b(?:secrets?|credentials?|tokens?|environment|files?|data|contents?|system\s+prompt)\b"),
)
_BASE64_BLOCK = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{32,4096}={0,2}(?![A-Za-z0-9+/])")
_EDUCATIONAL_CONTEXT = re.compile(r"(?i)\b(?:example|phrase|quoted|documentation|explains?|describes?|discusses?)\b")


def _prompt_injection(text):
    # same Unicode normalisation as PII masking (zero-width, full-width, hyphens).
    text = privacy._normalise_for_detection(text)[0]
    for pattern in PROMPT_INJECTION_PATTERNS:
        for match in pattern.finditer(text):
            before = text[max(0, match.start()-100):match.start()]
            after = text[match.end():min(len(text), match.end()+100)]
            quoted_example = (_EDUCATIONAL_CONTEXT.search(before) and
                              _EDUCATIONAL_CONTEXT.search(after))
            if not quoted_example:
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
    return False


def withhold(value, reason):
    """Content-free alert used instead of output that could not be scanned (fail-closed)."""
    if os.environ.get("LOCAL_SCOUT_GUARD_MODE") == "open":
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
