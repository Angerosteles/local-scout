"""Copy-on-write protection for provider-bound user prompts and tool output."""
from collections import OrderedDict
import hashlib
import json
import os

from . import privacy, storage
from .tool_guard import MAX_INPUT_CHARS

ALERT = 'LOCAL_SCOUT_ALERT v1;'
WITHHELD = ALERT + ' action=withheld; reason=prompt_scan_failed; source=user_prompt'


class PromptGuard:
    def __init__(self, scout):
        self.scout = scout
        self.cache = OrderedDict()
        self.seen = OrderedDict()

    def scan(self, text, names=False):
        if len(text.encode('utf-8', 'surrogatepass')) > MAX_INPUT_CHARS:
            raise ValueError('oversize')
        key = hashlib.sha256((('N' if names else '-') + text).encode('utf-8', 'surrogatepass')).digest()
        if key not in self.cache:
            masked, categories = privacy.mask_local_details(text, names=names)
            self.cache[key] = (masked, tuple(sorted(categories)))
            if len(self.cache) > 200:
                self.cache.popitem(last=False)
        return self.cache[key]

    TEXT_TYPES = ('text', 'input_text', 'output_text')

    def _leaves(self, value, found):
        """Mask string leaves and string keys of a JSON-like value; other types are untouched.

        Keys are masked like values (as in tool results). If two keys become equal after masking, the later ones
        get a deterministic suffix ([EMAIL]#2): the JSON stays valid and distinct, and no value leaks. Withholding is
        not an option here, because these are tool-call arguments in the conversation history."""
        if isinstance(value, str):
            masked, cats = self.scan(value)
            found.update(cats)
            return masked
        if isinstance(value, dict):
            out = {}
            for key, child in value.items():
                if isinstance(key, str):
                    key = self._leaves(key, found)
                    base, n = key, 2
                    while key in out:
                        key, n = f'{base}#{n}', n + 1
                out[key] = self._leaves(child, found)
            return out
        if isinstance(value, list):
            return [self._leaves(v, found) for v in value]
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
                joined, _ = self.scan(''.join(texts), names)
                if joined != ''.join(self.scan(x, names)[0] for x in texts):
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
            return self.transform(request)
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
