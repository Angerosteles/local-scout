"""Process-local numbered masks. No values can be recovered from this table."""
from contextvars import ContextVar
import hashlib
import json
import re
import secrets
import threading


CATEGORIES = ('name', 'email', 'phone', 'address')
LIMIT = 99999
MAX_BYTES = 4 * 1024 * 1024
CURRENT = ContextVar('local_scout_numbered_masks', default=None)
TOKEN = re.compile(r'\[(NAME|EMAIL|PHONE|ADDRESS)_([1-9][0-9]{0,4})\]')


class Pseudonyms:
    """One monotonic namespace per profile/process; exhaustion keeps ordinary masks.

    Keys are keyed 256-bit digests of exact category/value pairs, not plaintext.
    Existing entries are never evicted or renumbered. The byte budget includes
    fixed reservation bitmaps and a conservative 256 bytes per digest entry.
    """

    def __init__(self, max_bytes=MAX_BYTES, limit=LIMIT):
        self.lock = threading.RLock()
        self.limit = min(max(0, limit), LIMIT)
        self.used = {c: bytearray((self.limit + 8) // 8) for c in CATEGORIES}
        self.last = dict.fromkeys(CATEGORIES, 0)
        self.numbers = {}
        self.salt = secrets.token_bytes(32)
        self.max_bytes = max_bytes
        self.bytes_used = 2048 + sum(len(bits) for bits in self.used.values())
        self.disabled = False

    def token(self, category, value):
        if category not in CATEGORIES or not value:
            return None
        digest = hashlib.blake2b(category.encode() + b'\0' + value.encode('utf-8', 'surrogatepass'),
                                 key=self.salt, digest_size=32).digest()
        with self.lock:
            if self.disabled:
                return None
            number = self.numbers.get(digest)
            if number is None:
                if self.bytes_used + 256 > self.max_bytes:
                    return None
                number = self.last[category] + 1
                bits = self.used[category]
                while number <= self.limit and bits[number >> 3] & (1 << (number & 7)):
                    number += 1
                # Remember skipped reservations even when the category is exhausted.
                self.last[category] = number
                if number > self.limit:
                    return None
                bits[number >> 3] |= 1 << (number & 7)
                self.numbers[digest] = number
                self.bytes_used += 256
            return '[%s_%d]' % (category.upper(), number)

    def reserve(self, value):
        """Reserve every literal token before assigning any numbers in a payload.

        Bitmaps have fixed size: hostile repeated tokens cannot grow the table.
        Old/foreign tokens arriving after allocation remain inherently ambiguous;
        no text-only hook can recover their profile or process provenance.
        """
        def visit(item):
            if isinstance(item, str):
                for match in TOKEN.finditer(item):
                    category, number = match[1].lower(), int(match[2])
                    if number <= self.limit:
                        self.used[category][number >> 3] |= 1 << (number & 7)
                # Tool results and historical function arguments may be JSON text.
                # Reserve tokens made visible only by decoding Unicode escapes.
                if item.lstrip().startswith(('{', '[', '"')):
                    try:
                        decoded = json.loads(item)
                    except (ValueError, TypeError):
                        pass
                    else:
                        visit(decoded)
            elif isinstance(item, dict):
                for key, child in item.items():
                    visit(key)
                    visit(child)
            elif isinstance(item, (list, tuple)):
                for child in item:
                    visit(child)

        with self.lock:
            try:
                visit(value)
            except Exception:
                # Broken/cyclic input or reservation failure must not skip the
                # normal privacy scan. Disable numbering and keep ordinary masks.
                self.disabled = True
