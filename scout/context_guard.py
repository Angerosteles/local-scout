"""Experimental context guard (opt-in `guard`): session-local provenance signals for outgoing tool calls.

Nothing the user writes is parsed as consent: a destination the user mentioned only stops it from counting as
"came from untrusted content"; it never cancels other signals. Hermes' approval prompt is the consent step.
Indicators are kept in memory (capped per session), never the text itself."""
from collections import OrderedDict
import json
import re
import threading
from urllib.parse import urlsplit

from . import tool_guard

MAX_SESSIONS = 128
MAX_INDICATORS = 4096          # per set and session; a full index escalates every outgoing call
NO_SESSION = '\x00no-session'  # calls without a session id share one index: more prompts, never fewer

_INDICATOR = re.compile(r'https?://[^\s\'"<>]+|[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}|(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])|(?<![\w.])[\w-]+(?:\.[\w-]+)+\.[a-zA-Z]{2,}|(?<!\w)/(?:[\w.*~+-]+/)*[\w.*~+-]+', re.I)
_PLACEHOLDER = re.compile(r'\[(?:EMAIL|PHONE|SSN|IBAN|CARD|CREDIT_CARD|NAME|ADDRESS)[^\]]*\]', re.I)
# Outbound transfer, by shell command shape (checked on the command string itself) or code shape (any argument).
# Binaries count only at a command position, so file names such as mail-01.txt or rsync.log do not;
# curl and wget only with an upload or POST option. Quoted text (echo 'curl -d ...') is not a command.
_CMD = r'(?:^|[;&|`(]\s*|\$\(\s*|\b(?:sudo|exec|xargs|env|nohup|time)\s+(?:-\S+\s+)*)'
_SHELL_BODY = re.compile(r"\b(?:ba|z|da)?sh\s+-c\s+(['\"])(.*?)\1", re.S)
_QUOTED = re.compile(r"'[^']*'|\"(?:[^\"\\\\]|\\\\.)*\"", re.S)
_CODE_PATTERN = (r'\b(?:requests|httpx|session)\.(?:post|put|patch)\s*\(|\burlopen\s*\([^\n]*data\s*=|\bRequest\s*\([^\n]*data\s*='
                r'|\.sendall?\s*\(|\bsmtplib\b')
_SHELL_EGRESS = re.compile(
    _CMD + r'curl\b[^\n|;&]*(?:\s-d\b|\s-F\b|--data(?:-binary|-raw|-urlencode)?\b|--form\b|--upload-file\b|\s-T\b|-X\s*(?:POST|PUT|PATCH)\b)'
    r'|' + _CMD + r'wget\b[^\n|;&]*--post-(?:file|data)'
    r'|' + _CMD + r'(?:scp|rsync|sftp|ftp|nc|ncat|netcat|socat|telnet|mail|mailx|sendmail|mutt)\b'
    r'|' + _CMD + r'git\s+push\b|' + _CODE_PATTERN, re.I | re.M)
_CODE_EGRESS = re.compile(_CODE_PATTERN, re.I)
# Tools whose name says they send something (send_message, mcp__crm__upload_file, ...); reading tools do not count.
_SEND_NAME = re.compile(r'(?:^|_)(?:send|mail|email|upload|post|publish|share|message)(?:_|$)', re.I)


def command_shape(command):
    """The command with quoted text removed, plus the bodies of sh -c '...' as separate lines."""
    bodies = [body for _, body in _SHELL_BODY.findall(command)]
    return '\n'.join([_QUOTED.sub(' ', command)] + [_QUOTED.sub(' ', body) for body in bodies])


def indicators(text):
    out = set()
    for m in _INDICATOR.finditer(text[:262144]):
        item = m.group().rstrip('.,;:)]}')
        if item.startswith(('http://', 'https://')):
            try:
                item = urlsplit(item).hostname or ''
            except ValueError:
                item = ''
        if item:
            out.add(item.lower())
    return out


def _leaves(value, depth=0):
    if isinstance(value, str):
        yield value
    elif depth < 32 and isinstance(value, (list, tuple)):
        for item in value:
            yield from _leaves(item, depth + 1)
    elif depth < 32 and isinstance(value, dict):
        for key, item in value.items():
            yield from _leaves(key, depth + 1)  # keys are visible to the model too
            yield from _leaves(item, depth + 1)


def flatten(value):
    """Text leaves joined by newlines. JSON escaping would glue words (an escaped newline before
    requests.post reads as nrequests.post), so patterns run on the raw strings."""
    try:
        return '\n'.join(_leaves(value))[:262144]
    except (TypeError, ValueError, RecursionError):
        return ''


class ContextGuard:
    def __init__(self, scout):
        self.scout = scout
        self.sessions = OrderedDict()
        self.lock = threading.RLock()

    def enabled(self):
        config = self.scout.profile()['config']
        return config.get('mode', 'off') != 'off' and 'profile' in config.get('grants', {}).get('guard', [])

    def state(self, session):
        if not isinstance(session, str) or not session or len(session) > 256:
            session = NO_SESSION
        state = self.sessions.setdefault(session, {'user': set(), 'untrusted': set(), 'flagged': set(), 'paths': set(),
                                                   'source': '', 'full': False})
        self.sessions.move_to_end(session)
        while len(self.sessions) > MAX_SESSIONS:
            self.sessions.popitem(last=False)
        return state

    @staticmethod
    def _add(state, key, items):
        room = MAX_INDICATORS - len(state[key])
        items = set(items) - state[key]
        if len(items) > room:
            state['full'] = True
            items = set(sorted(items)[:max(room, 0)])
        state[key].update(items)

    def user_request(self, request=None, session_id='', **kwargs):
        if not self.enabled() or not isinstance(request, dict):
            return None
        mentioned = set()
        for item in request.get('messages', request.get('input', [])) or []:
            if isinstance(item, dict) and item.get('role') == 'user':
                mentioned |= indicators(flatten(item.get('content', '')))
        with self.lock:
            self._add(self.state(session_id), 'user', mentioned)
        return None

    def tool_result(self, tool_name=None, args=None, result=None, session_id='', **kwargs):
        if not self.enabled():
            return None
        if isinstance(result, str) and result[:1] in '{[':
            # Hermes tool results are JSON text; index the decoded strings, not the escaped form.
            try:
                result = json.loads(result)
            except ValueError:
                pass
        text = flatten(result)
        found = indicators(text)
        _, categories, _ = tool_guard.transform(text, 'context', 'memory')
        flagged = 'prompt_injection' in categories
        with self.lock:
            state = self.state(session_id)
            self._add(state, 'untrusted', found)
            if flagged:
                self._add(state, 'flagged', found)
                state['source'] = str(tool_name or 'tool')[:80]
            if tool_name == 'read_file' and isinstance(args, dict):
                self._add(state, 'paths', (x for x in indicators(flatten(args)) if x.startswith('/')))
        return None

    def pre_tool_call(self, tool_name=None, args=None, session_id='', **kwargs):
        if not self.enabled():
            return None
        name = str(tool_name or '')
        text = flatten(args)
        command = args.get('command') if isinstance(args, dict) and isinstance(args.get('command'), str) else ''
        is_egress = bool(_SHELL_EGRESS.search(command_shape(command[:262144]))) or bool(_CODE_EGRESS.search(text)) or bool(_SEND_NAME.search(name))
        if not is_egress:
            return None
        with self.lock:
            state = self.state(session_id)
            used = indicators(text)
            destinations = {x for x in used if not x.startswith('/')}
            signals = []
            if state['full']:
                signals.append('S0 index full')  # provenance can no longer be judged: always ask
            if state['source']:
                signals.append('S1 flagged context')
            if destinations & (state['untrusted'] - state['user']):
                signals.append('S2 untrusted destination')
            if state['source'] and used & state['flagged']:
                signals.append('S3 instruction indicators overlap')
            if _PLACEHOLDER.search(text):
                signals.append('S4 mask placeholder')
            if used & state['paths']:
                signals.append('S5 previously read path')
            # Flagged session: any second signal. Otherwise: a destination from untrusted content, placeholders, or a full index.
            strong = {'S0 index full', 'S2 untrusted destination', 'S4 mask placeholder'}
            if (state['source'] and len(signals) > 1) or strong & set(signals):
                return {'action': 'approve', 'message': 'Local Scout context guard: ' + ', '.join(signals) + '; source=' + (state['source'] or 'tool result'), 'rule_key': 'local_scout_context'}
        return None
