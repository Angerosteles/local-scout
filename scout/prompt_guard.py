"""Copy-on-write protection for provider-bound user prompts and tool output."""
from collections import OrderedDict
from contextvars import ContextVar
import hashlib
import json
import os

from . import basal, credentials, privacy, storage
from .tool_guard import MAX_INPUT_CHARS

# One basal checker (one shared time budget) per provider request: every block and message of a request uses it.
# _NO_BASAL marks paths that never ask the engine (auxiliary title calls).
_REQUEST_CHECK = ContextVar('local_scout_request_basal_check', default=None)
_NO_BASAL = object()

ALERT = 'LOCAL_SCOUT_ALERT v1;'
WITHHELD = ALERT + ' action=withheld; reason=prompt_scan_failed; source=user_prompt'


class PromptGuard:
    def __init__(self, scout):
        self.scout = scout
        self.cache = OrderedDict()
        self.seen = OrderedDict()

    def scan(self, text, names=False, field=None):
        if len(text.encode('utf-8', 'surrogatepass')) > MAX_INPUT_CHARS:
            raise ValueError('oversize')
        config = self.scout.profile()['config']
        engine = config.get('basal_url') if names and isinstance(config, dict) else None
        key = hashlib.sha256('\0'.join(('N' if names else '-', str(field or ''), str(engine or ''), text))
                             .encode('utf-8', 'surrogatepass')).digest()
        if key in self.cache:
            return self.cache[key]
        # User prompts get prompt thresholds and the optional basal check; other roles (system prompt, history
        # already masked as tool results) use the deterministic text thresholds, so basal is not re-asked each turn.
        check = None
        if engine:
            shared = _REQUEST_CHECK.get()
            check = None if shared is _NO_BASAL else (shared or basal.credential_checker(config))
        state = {'degraded': False}
        context = credentials.CONTEXT.set(('prompt' if names else 'text', check, state))
        try:
            masked, categories = privacy.mask_local_details(text, names=names, field=field)
        finally:
            credentials.CONTEXT.reset(context)
        result = (masked, tuple(sorted(categories)))
        if not state['degraded']:          # a scan cut short by a basal failure or budget is not remembered
            self.cache[key] = result
            if len(self.cache) > 200:
                self.cache.popitem(last=False)
        return result

    TEXT_TYPES = ('text', 'input_text', 'output_text')

    def _leaves(self, value, found, field=None):
        """Mask string leaves, numbers and string keys of a JSON-like value; booleans and null are untouched.

        A value is scanned with its key as context ("otp_code": "543918"), so password-like values, codes and PINs
        are found as in plain text. Numbers are scanned as text and become a placeholder string only when something
        is detected. Keys are masked like values (as in tool results). If two keys become equal after masking, the
        later ones get a deterministic suffix ([EMAIL]#2): the JSON stays valid and distinct, and no value leaks.
        Withholding is not an option here, because these are tool-call arguments in the conversation history."""
        if isinstance(value, str):
            masked, cats = self.scan(value, field=field)
            found.update(cats)
            return masked
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, (int, float)):
            masked, cats = self.scan(str(value), field=field)
            found.update(cats)
            return masked if cats else value
        if isinstance(value, dict):
            out = {}
            for key, child in value.items():
                name = key if isinstance(key, str) else None
                if isinstance(key, str):
                    key = self._leaves(key, found)
                    base, n = key, 2
                    while key in out:
                        key, n = f'{base}#{n}', n + 1
                out[key] = self._leaves(child, found, name)
            return out
        if isinstance(value, list):
            return [self._leaves(v, found, field) for v in value]
        return value

    def _arguments(self, raw, found):
        """Tool-call arguments are JSON text: mask string leaves and re-serialise, so the JSON stays valid."""
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            masked, cats = self.scan(raw)
            found.update(cats)
            return masked
        local = set()
        new = self._leaves(parsed, local)
        if not local:
            return raw
        found.update(local)
        return json.dumps(new, ensure_ascii=False)

    def message(self, item, content_key, source, names=False, marker=True):
        """Return (new item, categories, action) for one message, block by block.

        Scanned: text/input_text/output_text blocks, tool_result content and tool_use input leaves.
        Unchanged: thinking, images and unknown blocks of assistant, tool and system messages.
        User messages are the exception: an unscannable user block (image, file) withholds the message."""
        content = item.get(content_key)
        if content is None:
            return item, (), None
        if isinstance(content, str):
            if len(content.encode('utf-8', 'surrogatepass')) > MAX_INPUT_CHARS:
                raise ValueError('oversize')
            value, found = self.scan(content, names)
            if not found:
                return item, (), None
            categories = set(found)
            new_content = value
        elif isinstance(content, list):
            texts = [p['text'] for p in content if isinstance(p, dict) and p.get('type') in self.TEXT_TYPES and isinstance(p.get('text'), str)]
            if sum(len(x.encode('utf-8', 'surrogatepass')) for x in texts) > MAX_INPUT_CHARS:
                raise ValueError('oversize')
            if len(texts) > 1:
                joined, joined_found = self.scan(''.join(texts), names)
                if joined != ''.join(self.scan(x, names)[0] for x in texts):
                    if item.get('role') in ('system', 'developer'):
                        # Detected data only appears across block boundaries. Never strip the agent's instructions:
                        # merge the text blocks into one masked block of the same type and keep the other blocks.
                        kind = next(b.get('type') for b in content if isinstance(b, dict) and b.get('type') in self.TEXT_TYPES)
                        rest = [b for b in content if not (isinstance(b, dict) and b.get('type') in self.TEXT_TYPES)]
                        return dict(item, **{content_key: [{'type': kind, 'text': joined}] + rest}), tuple(sorted(joined_found)), 'masked'
                    return dict(item, **{content_key: '[WITHHELD]' if source == 'auxiliary_call' else WITHHELD}), (), 'withheld'
            categories, new_content = set(), []
            for block in content:
                if not isinstance(block, dict):
                    new_content.append(block); continue
                kind = block.get('type')
                if kind in self.TEXT_TYPES and isinstance(block.get('text'), str):
                    value, found = self.scan(block['text'], names)
                    categories.update(found)
                    new_content.append(dict(block, text=value) if found else block)
                elif kind == 'tool_result':
                    inner = block.get('content')
                    if isinstance(inner, str):
                        value, found = self.scan(inner)
                        categories.update(found)
                        new_content.append(dict(block, content=value) if found else block)
                    elif isinstance(inner, list) and self._merge_split_text(inner) is not None:
                        merged, merged_found = self._merge_split_text(inner)
                        categories.update(merged_found)
                        new_content.append(dict(block, content=merged))
                    elif isinstance(inner, list):
                        local = set()
                        blocks = [dict(b, text=self._leaves(b['text'], local)) if isinstance(b, dict) and b.get('type') in self.TEXT_TYPES and isinstance(b.get('text'), str) else b for b in inner]
                        categories.update(local)
                        new_content.append(dict(block, content=blocks) if local else block)
                    else:
                        new_content.append(block)
                elif kind == 'tool_use' and isinstance(block.get('input'), (dict, list)):
                    local = set()
                    value = self._leaves(block['input'], local)
                    categories.update(local)
                    new_content.append(dict(block, input=value) if local else block)
                elif source == 'user_prompt':
                    # Unscannable user content (e.g. an image or file) stays fail-closed: withheld, as documented.
                    raise ValueError('unsupported user block')
                else:
                    new_content.append(block)  # assistant/tool/system thinking, images, unknown blocks: unchanged
            if not categories:
                return item, (), None
        else:
            raise ValueError('unsupported content')
        line = ALERT + ' categories=' + ','.join(sorted(categories)) + '; action=masked; source=' + source
        if marker and source not in ('auxiliary_call', 'system_prompt'):
            if isinstance(new_content, str):
                new_content = new_content + '\n' + line
            else:
                kinds = [b.get('type') for b in content if isinstance(b, dict)]
                new_content.append({'type': 'input_text' if 'input_text' in kinds else 'text', 'text': line})
        return dict(item, **{content_key: new_content}), tuple(sorted(categories)), 'masked'

    def record(self, item, categories, action, source='user_prompt'):
        try:
            # A digest of the representation is retained only in memory.
            key = hashlib.sha256(repr(item).encode('utf-8', 'replace')).digest()
            if key in self.seen:
                return
            self.seen[key] = None
            if len(self.seen) > 200:
                self.seen.popitem(last=False)
            with self.scout.lock:
                state = self.scout.profile()
                grants = state['config'].get('grants', {})
                if not any('profile' in grants.get(scope, []) for scope in ('observe', 'insights')):
                    return
                for category in categories or ('coverage:partial',):
                    state['events'].append(dict(category=category, timestamp=self.scout.clock(), scope=source, source=source, action=action, dataset_id='profile'))
                state['events'] = state['events'][-10000:]
                # Auxiliary (title/compression) masks are counted, but the user notice stays with the main prompt.
                if categories and source == 'user_prompt' and state['config'].get('mode') == 'report':
                    state['notifications'].append(dict(dataset_id='profile', prompt_categories=list(categories), prompt_masked=True))
                    state['notifications'] = state['notifications'][-100:]
                try:
                    storage.write_json(self.scout.root()/'aggregates.json', {'events': state['events']})
                except Exception:
                    pass
        except Exception:
            pass

    def __call__(self, request=None, **kwargs):
        try:
            config = self.scout.profile()['config']
            if config.get('mode', 'off') == 'off' or 'profile' not in config.get('grants', {}).get('mask', []):
                return None
            token = _REQUEST_CHECK.set(basal.credential_checker(config) or _NO_BASAL)
            try:
                return self.transform(request)
            finally:
                _REQUEST_CHECK.reset(token)
        except Exception:
            if os.environ.get('LOCAL_SCOUT_GUARD_MODE') == 'open':
                return None
            return self.emergency(request)

    def emergency(self, request):
        try:
            result = dict(request)
            for key in ('system', 'instructions'):
                if key in result:
                    result[key] = WITHHELD
            for key in ('messages', 'input'):
                if key in result:
                    result[key] = [{'role': 'user', 'content': WITHHELD}]
            return {'request': result, 'source': 'local_scout', 'reason': 'pii_mask'}
        except Exception:
            return {'request': {'messages': [{'role': 'user', 'content': WITHHELD}]}, 'source': 'local_scout', 'reason': 'pii_mask'}

    def auxiliary(self, request_messages=None, **kwargs):
        # Hermes 0.23 passes a shallow list copy: message dictionaries alias provider kwargs, so they are updated
        # in place. Same per-item logic as the main request (parity).
        try:
            config = self.scout.profile()['config']
            if config.get('mode', 'off') == 'off' or 'profile' not in config.get('grants', {}).get('mask', []):
                return
        except Exception:
            config = None
        token = _REQUEST_CHECK.set(_NO_BASAL)        # title calls never wait for the engine
        try:
            if not isinstance(request_messages, list):
                return
            for item in request_messages:
                if not isinstance(item, dict):
                    continue
                try:
                    if config is None:
                        raise ValueError('configuration unavailable')
                    new, categories, action, source = self._item(item, aux=True)
                    if action:
                        item.clear(); item.update(new)
                        self.record(dict(role=item.get('role'), aux=True, content=repr(new.get('content'))[:64]), categories, action, source='auxiliary_call')
                except Exception:
                    if item.get('role') not in ('system', 'developer') and item.get('content') is not None:
                        item['content'] = '[WITHHELD]'
        except Exception:
            return
        finally:
            _REQUEST_CHECK.reset(token)

    def _mask_arguments(self, item):
        """Mask tool-call arguments (chat tool_calls / function_call, Responses function_call items) as JSON."""
        found = set()
        if isinstance(item.get('tool_calls'), list):
            calls, any_changed = [], False
            for call in item['tool_calls']:
                fn = call.get('function') if isinstance(call, dict) else None
                if isinstance(fn, dict) and isinstance(fn.get('arguments'), str):
                    new = self._arguments(fn['arguments'], found)
                    if new != fn['arguments']:
                        call = dict(call, function=dict(fn, arguments=new)); any_changed = True
                calls.append(call)
            if any_changed:
                item = dict(item, tool_calls=calls)
        fn = item.get('function_call')
        if isinstance(fn, dict) and isinstance(fn.get('arguments'), str):
            new = self._arguments(fn['arguments'], found)
            if new != fn['arguments']:
                item = dict(item, function_call=dict(fn, arguments=new))
        if item.get('type') == 'function_call' and isinstance(item.get('arguments'), str):
            new = self._arguments(item['arguments'], found)
            if new != item['arguments']:
                item = dict(item, arguments=new)
        return item, bool(found)

    def _item(self, item, aux=False):
        """Process one message/item; shared by the main request and the auxiliary channel (parity)."""
        role, kind = item.get('role'), item.get('type')
        source = 'user_prompt' if role == 'user' else 'system_prompt' if role in ('system', 'developer') else 'tool_result'
        content_key = 'output' if kind == 'function_call_output' else 'content'
        item, args_changed = self._mask_arguments(item)
        if role not in ('user', 'assistant', 'tool', 'function', 'system', 'developer') and kind != 'function_call_output':
            return item, (), ('masked' if args_changed else None), source
        try:
            new, categories, action = self.message(item, content_key, 'auxiliary_call' if aux else source,
                                                   names=role == 'user', marker=role != 'assistant')
        except Exception:
            if source == 'system_prompt' or os.environ.get('LOCAL_SCOUT_GUARD_MODE') == 'open':
                # Never strip the agent's instructions because a scan failed; the explicit opt-out keeps content too.
                return item, (), ('masked' if args_changed else None), source
            notice = '[WITHHELD]' if aux else (WITHHELD if source == 'user_prompt' else WITHHELD.replace('source=user_prompt', 'source=' + source))
            new, categories, action = dict(item, **{content_key: notice}), (), 'withheld'
        if args_changed and not action:
            action = 'masked'
        return new, categories, action, source

    def _merge_split_text(self, blocks):
        """Text blocks whose detected data appears only when joined: return them merged into one masked block of the
        first text type (other blocks kept after it) with the categories found; None when block-by-block masking
        already covers everything."""
        texts = [b['text'] for b in blocks if isinstance(b, dict) and b.get('type') in self.TEXT_TYPES and isinstance(b.get('text'), str)]
        if len(texts) < 2:
            return None
        joined, joined_found = self.scan(''.join(texts))
        if joined == ''.join(self.scan(x)[0] for x in texts):
            return None
        kind = next(b.get('type') for b in blocks if isinstance(b, dict) and b.get('type') in self.TEXT_TYPES)
        rest = [b for b in blocks if not (isinstance(b, dict) and b.get('type') in self.TEXT_TYPES)]
        return [{'type': kind, 'text': joined}] + rest, joined_found

    def _mask_top_level(self, result, request):
        """Top-level `system` (string or text blocks) and Responses `instructions` may carry user data (memory/profile)."""
        changed = False
        for key in ('system', 'instructions'):
            value = request.get(key)
            if isinstance(value, str):
                masked, cats = self.scan(value)
                if cats:
                    result[key] = masked; changed = True
            elif isinstance(value, list) and all(isinstance(b, dict) for b in value):
                merged = self._merge_split_text(value)
                if merged is not None:            # detected data only across block boundaries: one masked block
                    result[key], changed = merged[0], True
                    continue
                blocks, block_changed = [], False
                for b in value:
                    if isinstance(b.get('text'), str):
                        masked, cats = self.scan(b['text'])
                        if cats:
                            b = dict(b, text=masked); block_changed = True
                    blocks.append(b)
                if block_changed:
                    result[key] = blocks; changed = True
        return changed

    def transform(self, request):
        result = dict(request)
        # system/developer content and top-level system/instructions were sent unmasked;
        # assistant tool_calls with content=None were replaced by a WITHHELD notice (broke every tool loop);
        # tool-call arguments were not scanned.
        changed = self._mask_top_level(result, request)
        for key in ('messages', 'input'):
            if key not in request:
                continue
            sequence = request[key]
            if key == 'input' and isinstance(sequence, str):
                sequence = [{'role': 'user', 'content': sequence}]
            if not isinstance(sequence, list):
                raise ValueError('unsupported request')
            output = []
            key_changed = False
            for item in sequence:
                if not isinstance(item, dict):
                    raise ValueError('unsupported message')
                replacement, categories, action, source = self._item(item)
                if action:
                    key_changed = True
                    if source == 'user_prompt':
                        self.record(item, categories, action)
                output.append(replacement)
            if key_changed:
                result[key] = output
                changed = True
        return {'request': result, 'source': 'local_scout', 'reason': 'pii_mask'} if changed else None
