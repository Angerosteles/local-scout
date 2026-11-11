"""Opt-in triage execution shortcut; no Hermes or optional SDK imports."""
import json
import math
import re
import threading
import time
from types import SimpleNamespace
import uuid
import zipfile

from . import insights, storage

MAX_TEXT = 8000
MAX_REQUEST_CHARS = 262144
MAX_LOCAL_SECONDS = 0.25
# each `hermes -z` process starts cold, and the first numpy/sklearn import plus artifact
# load exceeded 0.25 s, so every one-shot session forwarded (reason=budget). The first local decision of a process
# may take up to MAX_COLD_SECONDS (one-time cost, below a provider round trip); later decisions keep 0.25 s.
MAX_COLD_SECONDS = 5.0
ARTIFACT_ID = re.compile(r'model-[a-p]{32}')
LOCAL_REPLY = re.compile(
    r'\{"intent":"[a-z0-9_:.-]{1,64}","answered_by":"local_scout",'
    r'"confidence":(?:0\.\d{3}|1\.000)\}\n'
    r'Answered locally by Scout; the model was not asked to answer\.'
)


def is_local_answer(text):
    return isinstance(text, str) and LOCAL_REPLY.fullmatch(text) is not None


class Forward(Exception):
    """An allowlisted reason, never input or exception prose."""


def _signature(path, limit=storage.MAX_BYTES):
    if path.is_symlink():
        raise Forward('artifact')
    stat = path.stat()
    if not path.is_file() or stat.st_size > limit:
        raise Forward('artifact')
    return (stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)


def _last_user(request, marker):
    if not isinstance(request, dict):
        raise Forward('input')
    messages = request.get('messages')
    if not isinstance(messages, list) or not 1 <= len(messages) <= 512:
        raise Forward('input')
    last = messages[-1]
    if not isinstance(last, dict) or last.get('role') != 'user':
        raise Forward('tool_loop')
    if last.get('tool_calls') or last.get('function_call') or last.get('type') not in (None, 'message'):
        raise Forward('tool_loop')
    content = last.get('content')
    if isinstance(content, list):
        if not content or len(content) > 64 or any(
            not isinstance(part, dict) or part.get('type') != 'text' or
            not isinstance(part.get('text'), str) for part in content
        ):
            raise Forward('input')
        if sum(len(part['text']) for part in content) + len(content) - 1 > MAX_TEXT:
            raise Forward('input')
        content = '\n'.join(part['text'] for part in content)
    if not isinstance(content, str) or not 0 < len(content) <= MAX_TEXT:
        raise Forward('input')
    if marker is not None:
        if not isinstance(marker, str) or not 1 <= len(marker) <= 256 or marker not in content:
            raise Forward('marker')
        if content.count(marker) != 1:
            raise Forward('marker')
        content = content.split(marker, 1)[1]
    if not content.strip():
        raise Forward('input')
    return content


def _request_chars(request):
    """Bounded character estimate of the provider input, including history/tools."""
    nodes = 0

    def count(value, depth=0):
        nonlocal nodes
        nodes += 1
        if nodes > 20000 or depth > 20:
            raise Forward('input')
        if isinstance(value, str):
            result = len(value)
        elif isinstance(value, dict):
            result = 0
            for key, item in value.items():
                result += count(key, depth + 1) + count(item, depth + 1)
                if result > MAX_REQUEST_CHARS:
                    raise Forward('input')
        elif isinstance(value, (list, tuple)):
            result = 0
            for item in value:
                result += count(item, depth + 1)
                if result > MAX_REQUEST_CHARS:
                    raise Forward('input')
        elif value is None or type(value) in (int, float, bool):
            result = 0
        else:
            raise Forward('input')
        if result > MAX_REQUEST_CHARS:
            raise Forward('input')
        return result

    payload = {key: request[key] for key in ('messages', 'tools', 'functions') if key in request}
    count(payload)
    size = len(json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False))
    if size > MAX_REQUEST_CHARS:
        raise Forward('input')
    return size


def _completion(label, score, model):
    # Same completed-object contract as Hermes' chat streaming accumulator.
    content = ('{"intent":' + json.dumps(label, ensure_ascii=False) +
               ',"answered_by":"local_scout","confidence":' + f'{score:.3f}' +
               '}\nAnswered locally by Scout; the model was not asked to answer.')
    return SimpleNamespace(
        id='local-scout-' + uuid.uuid4().hex, object='chat.completion',
        created=int(time.time()), model=model or 'local-scout', provider='local_scout',
        choices=[SimpleNamespace(index=0, finish_reason='stop', logprobs=None,
                                 message=SimpleNamespace(role='assistant', content=content,
                                                         tool_calls=None, function_call=None,
                                                         reasoning_content=None, refusal=None))],
        usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0, total_tokens=0,
                              prompt_tokens_details=SimpleNamespace(cached_tokens=0),
                              completion_tokens_details=SimpleNamespace(reasoning_tokens=0)),
    )


class LocalAnswers:
    """One bounded cache per registered Scout, invalidated by active-file signatures."""

    def __init__(self, scout):
        self.scout = scout
        self._lock = threading.Lock()
        self._cache = None

    def _active_model(self, config):
        root = self.scout.root()
        paths = (root / 'choices.json', root / 'decisions.json')
        signatures = tuple(_signature(path, 1024 * 1024) for path in paths)
        key = (str(root), signatures)
        if self._cache is not None and self._cache[0] == key:
            _, dataset, meta, arrays, artifact_paths, artifact_signatures = self._cache
            if tuple(_signature(path) for path in artifact_paths) == artifact_signatures:
                if dataset not in config.get('grants', {}).get('answer', []):
                    raise Forward('consent')
                return meta, arrays
        self._cache = None
        choice = storage.read_json(paths[0]).get('triage', {})
        dataset = choice.get('dataset_id')
        if not dataset or dataset not in config.get('grants', {}).get('answer', []):
            raise Forward('consent')
        entries = storage.read_json(paths[1])
        entry = next((e for e in entries if e.get('decision_id') == choice.get('decision_id')
                      and e.get('dataset_id') == dataset and e.get('candidate_id') == 'triage'
                      and e.get('recipe_id') == choice.get('recipe_id')), None)
        outcome = (entry or {}).get('outcome') or {}
        aid = outcome.get('artifact_id')
        if outcome.get('status') != 'measured' or not isinstance(aid, str) or not ARTIFACT_ID.fullmatch(aid):
            raise Forward('artifact')
        artifact_paths = tuple(root / 'artifacts' / (aid + suffix) for suffix in ('.json', '.npz'))
        artifact_signatures = tuple(_signature(path) for path in artifact_paths)
        descriptor = storage.read_json(artifact_paths[0])
        engine = descriptor['metadata'].get('engine')
        if engine == 'basal_zero_shot':
            # basal takes seconds per ticket on a CPU, far above the in-turn budget: forward instead of waiting.
            raise Forward('slow_engine')
        if engine not in ('logreg', 'knn', 'trees'):
            raise Forward('artifact')
        with zipfile.ZipFile(artifact_paths[1]) as archive:
            if sum(item.file_size for item in archive.infolist()) > 16 * 1024 * 1024:
                raise Forward('artifact')
        meta, arrays = storage.load_artifact(root / 'artifacts', aid)
        if ((meta['engine'] == 'knn' and len(arrays['train']) > 2000) or
                (meta['engine'] == 'trees' and sum(meta['forest_sizes']) > 10000)):
            raise Forward('artifact')
        storage.validate_model(meta, arrays)
        from .learning import ALIASES
        if (meta['dataset_id'] != dataset or meta['candidate_id'] != 'triage' or
                meta['engine'] != ALIASES.get(choice['recipe_id'], choice['recipe_id']) or
                meta.get('threshold_source') != 'validation'):
            raise Forward('artifact')
        if signatures != tuple(_signature(path, 1024 * 1024) for path in paths) or artifact_signatures != tuple(
            _signature(path) for path in artifact_paths
        ):
            raise Forward('artifact')
        self._cache = (key, dataset, meta, arrays, artifact_paths, artifact_signatures)
        return meta, arrays

    def _answer(self, request, original_request, api_mode, model, started):
        config = self.scout.profile()['config']
        if config.get('mode', 'off') == 'off':
            raise Forward('off')
        if not config.get('grants', {}).get('answer'):
            raise Forward('consent')
        if config.get('answer_task') != 'triage':
            raise Forward('task')
        if api_mode != 'chat_completions':
            raise Forward('api_mode')
        # Inspect both tails: a transformed request must not hide a tool-loop suffix.
        from .prompt_guard import WITHHELD
        from . import injection, tool_guard
        current = _last_user(request, None)
        if WITHHELD in json.dumps(request, ensure_ascii=False):
            raise Forward('withheld')
        original = _last_user(original_request, None)
        text = _last_user(original_request, config.get('answer_marker'))
        if tool_guard._prompt_injection(original) or (injection.available() and injection.is_injection(original)):
            raise Forward('injection')
        chars = _request_chars(request)
        meta, arrays = self._active_model(config)
        budget = MAX_LOCAL_SECONDS if getattr(self, '_warm', False) else MAX_COLD_SECONDS
        if time.monotonic() - started > budget:
            raise Forward('budget')
        from . import learning, privacy
        masked, _ = privacy.mask_local_details(text)
        result = learning.predict(meta, arrays, dict(text=masked))
        elapsed = time.monotonic() - started
        self._warm = True
        if elapsed > budget:
            raise Forward('budget')
        score, label = result.get('score'), result.get('label')
        if (result.get('decision') != 'accepted' or label == 'unknown' or label not in meta['classes'] or
                type(score) not in (int, float) or not math.isfinite(score) or
                not meta['threshold'] <= score <= 1):
            raise Forward('confidence')
        if self.scout.profile()['config'] != config:
            raise Forward('consent')
        return _completion(label, score, model), chars / 4

    def __call__(self, *, request, next_call, original_request=None, api_mode=None, model=None, **context):
        response, avoided, reason = None, 0, 'error'
        try:
            started = time.monotonic()
            if not self._lock.acquire(blocking=False):
                raise Forward('budget')
            try:
                response, avoided = self._answer(request, original_request, api_mode, model, started)
            finally:
                self._lock.release()
        except Forward as exc:
            reason = exc.args[0]
        except Exception:
            reason = 'error'
        try:
            insights.record_local_answer(self.scout, response is not None, reason, avoided)
        except Exception:
            # Telemetry is optional; never retry a downstream provider call.
            if response is not None:
                response = None
        if response is not None:
            return response
        # Deliberately outside ALL exception handlers: downstream errors propagate once.
        return next_call(request)
