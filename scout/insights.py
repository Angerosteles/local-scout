"""Local, metadata-only usage journal and privacy-preserving summaries."""
from collections import Counter
import json
import os
import math
import re
from pathlib import Path
import time

from . import storage
from ..schemas import SCHEMAS

SCHEMA_VERSION = 1
JOURNAL = 'insights.jsonl'
MAX_JOURNAL_BYTES = 1_000_000
MAX_EVENT_BYTES = 2048
MIN_COUNT = 5
TOOLS = set(SCHEMAS)
ERROR_CODES = {'OFF', 'CONSENT_REQUIRED', 'INVALID_INPUT', 'NOT_FOUND', 'UNSUPPORTED_SOURCE', 'PLAN_ONLY', 'INSUFFICIENT_DATA', 'ARTIFACT_INVALID', 'IO_ERROR', 'QUALITY_GATE_NOT_MET'}
PII_CATEGORIES = {'pesel', 'email', 'address', 'ssn', 'card', 'cvv', 'secret', 'iban', 'phone', 'name', 'prompt_injection'}
METHODS = {'logreg', 'knn', 'trees', 'own_cnn', 'tfidf_search', 'embed_search', 'regex', 'basal_zero_shot', 'lfm3', 'lfm25_moe', 'minicpm_v47', 'basal15', 'conv1d', 'agent_mlp', 'tfidf_logreg', 'small_local_llm', 'cloud', 'transformer_ner'}
FAMILIES = {'triage', 'search', 'fields'}
# Every Forward(...) reason in local_answers.py; anything else is recorded as 'error'.
LOCAL_ANSWER_REASONS = {'off', 'consent', 'task', 'api_mode', 'tool_loop', 'input', 'marker', 'artifact',
                        'confidence', 'budget', 'withheld', 'injection', 'slow_engine', 'error'}
LOCAL_ANSWER_ESTIMATE_BASIS = 'estimate: serialized messages/tools/functions characters / 4; not provider usage'


def _safe_read(path, default):
    try:
        return storage.read_json(path)
    except (OSError, ValueError, TypeError):
        return default


def append_event(root, event):
    """Append one allowlisted metadata event and keep a bounded rotating journal."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    clean = {k: event[k] for k in ('ts', 'kind', 'tool', 'ok', 'code', 'duration_ms', 'response_bytes', 'input_tokens', 'output_tokens') if k in event}
    if event.get('kind') == 'local_answer':
        clean = {k: event[k] for k in ('ts', 'kind', 'answered', 'forwarded', 'reason',
                                      'estimated_input_tokens_avoided', 'estimate_basis') if k in event}
        if type(clean.get('answered')) is not int or clean['answered'] not in (0, 1):
            return
        clean['forwarded'] = 1 - clean['answered']
        clean['reason'] = (None if clean['answered'] else
                           clean.get('reason') if clean.get('reason') in LOCAL_ANSWER_REASONS else 'error')
        clean['estimate_basis'] = LOCAL_ANSWER_ESTIMATE_BASIS
        estimate = clean.get('estimated_input_tokens_avoided', 0)
        clean['estimated_input_tokens_avoided'] = (estimate if clean['answered'] and
            type(estimate) in (int, float) and math.isfinite(estimate) and estimate >= 0 else 0)
    data = (json.dumps(clean, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
    if len(data) > MAX_EVENT_BYTES:
        return
    path = root / JOURNAL
    if path.is_symlink():
        return
    try:
        current = path.stat().st_size if path.exists() else 0
        if current + len(data) > MAX_JOURNAL_BYTES:
            rotated = root / (JOURNAL + '.1')
            rotated.unlink(missing_ok=True)
            if path.exists():
                os.replace(path, rotated)
        with path.open('ab') as stream:
            stream.write(data)
            stream.flush()
    except OSError:
        return


def record_hermes_tool(scout, name, status, duration_ms):
    """Hermes 0.23 post_tool_call: never inspect args, result or error prose."""
    try:
        config = scout.profile()['config']
        grants = config.get('grants', {})
        if config.get('mode', 'off') == 'off' or not (grants.get('insights') or grants.get('observe')):
            return
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', name) or name in TOOLS:
            return
        if type(duration_ms) not in (int, float) or not math.isfinite(duration_ms):
            return
        append_event(scout.root(), dict(ts=time.time(), kind='hermes_tool', tool=name,
                                       ok=status == 'ok', duration_ms=max(0, int(duration_ms))))
    except Exception:
        return


def _session_cost(scout, session_id=None, tokens=0, used=False, observed=False):
    """Charge observed session deltas once; retain session identifiers only in RAM."""
    state = scout.profile()
    known_session = isinstance(session_id, str) and 0 < len(session_id) <= 256
    key = session_id if known_session else None
    sessions = state.setdefault('agent_sessions', {})
    if key not in sessions and len(sessions) >= 1000:
        sessions.pop(next(iter(sessions)))
    session = sessions.setdefault(key, dict(tokens=0, accounted=0, used=False, observed=False))
    session['tokens'] += tokens
    session['used'] |= used
    session['observed'] |= observed
    if not session['used'] or not session['observed']:
        return
    delta = session['tokens'] - session['accounted']
    if delta <= 0 and session.get('charged'):
        return
    path = scout.root() / 'agent-cost.json'
    cost = _safe_read(path, dict(tokens=0, observations=0))
    cost['tokens'] += max(0, delta)
    cost['observations'] += 1
    cost['unknown_session_scope'] = cost.get('unknown_session_scope', False) or not known_session
    storage.write_json(path, cost)
    session['accounted'] = session['tokens']
    session['charged'] = True


def savings_summary(root, avoided):
    cost = _safe_read(Path(root) / 'agent-cost.json', {})
    measured = cost.get('observations', 0) > 0
    tokens = cost.get('tokens') if measured else None
    usage = _usage_not_observed_reason(Path(root))
    return dict(setup_tokens=tokens, net_tokens=avoided-tokens if measured else None,
                net_note=('estimate minus observed whole-session agent tokens in sessions using Scout; '
                          'conservative attribution, usage-hook coverage may be incomplete; retained lifetime counters'
                          + ('; missing session IDs use profile-process scope' if cost.get('unknown_session_scope') else '')
                          if measured else 'net unavailable; agent setup usage not observed (' + usage + ')'))


def _usage_not_observed_reason(root):
    config = _safe_read(root / 'settings.json', {})
    if config.get('mode') == 'off':
        return 'collection mode off'
    if config.get('mode') == 'on_demand':
        return 'on_demand does not collect usage-hook counters'
    if 'observe' not in config.get('grants', {}):
        return 'observe consent absent'
    return 'no accepted usage event in this journal period'


def record_local_answer(scout, answered, reason, estimated_input_tokens_avoided=0):
    """No text, label, model, dataset, session ID or exception details are retained."""
    try:
        config = scout.profile()['config']
        grants = config.get('grants', {})
        if config.get('mode', 'off') == 'off' or not (grants.get('insights') or grants.get('observe')):
            return
        estimate = estimated_input_tokens_avoided
        if type(estimate) not in (int, float) or not math.isfinite(estimate) or estimate < 0:
            estimate = 0
        append_event(scout.root(), dict(ts=time.time(), kind='local_answer', answered=int(bool(answered)),
                                       forwarded=int(not answered),
                                       reason=None if answered else reason if reason in LOCAL_ANSWER_REASONS else 'error',
                                       estimated_input_tokens_avoided=estimate if answered else 0,
                                       estimate_basis=LOCAL_ANSWER_ESTIMATE_BASIS))
    except Exception:
        return


def record_tool(scout, name, result, duration_ms, response_bytes, session_id=None):
    try:
        # Preserve Scout's no-state guarantee while the whole plugin is explicitly off.
        config = scout.profile()['config']
        if config.get('mode', 'off') == 'off':
            return
        # Consent first: local operation counters need an explicit insights or observe grant.
        grants = config.get('grants', {})
        if not (grants.get('insights') or grants.get('observe')):
            return
        _session_cost(scout, session_id=session_id, used=True)
        ok = bool(isinstance(result, dict) and result.get('ok') is True)
        error = result.get('error', {}) if isinstance(result, dict) else {}
        append_event(scout.root(), dict(ts=time.time(), kind='tool', tool=name, ok=ok,
                                       code=error.get('code') if isinstance(error, dict) else None,
                                       duration_ms=max(0, int(duration_ms)), response_bytes=max(0, int(response_bytes))))
    except Exception:
        return


def observe_usage_hook(scout):
    """Wrap the existing consent-aware usage hook; persist only accepted token deltas."""
    if getattr(scout, '_insights_usage_wrapped', False):
        return
    original = scout.usage_hook

    def wrapped(payload=None, **kwargs):
        try:
            before = dict(scout.profile().get('usage', {}))
        except Exception:
            before = {}
        original(payload, **kwargs)
        try:
            after = scout.profile().get('usage', {})
            delta = {key: (after.get(key) or 0) - (before.get(key) or 0) for key in ('input_tokens', 'output_tokens')}
            if before != after:
                event = dict(payload or {}, **kwargs)
                _session_cost(scout, session_id=event.get('session_id'), tokens=sum(delta.values()), observed=True)
            if any(v > 0 for v in delta.values()):
                append_event(scout.root(), dict(ts=time.time(), kind='usage', **delta))
        except Exception:
            return

    scout.usage_hook = wrapped
    scout._insights_usage_wrapped = True


def _read_journal(root, cutoff):
    result = []
    for suffix in ('.1', ''):
        path = Path(root) / (JOURNAL + suffix)
        try:
            if path.is_symlink() or path.stat().st_size > MAX_JOURNAL_BYTES:
                continue
            with path.open(encoding='utf-8') as stream:
                for line in stream:
                    if len(line) > MAX_EVENT_BYTES:
                        continue
                    try:
                        row = json.loads(line)
                        if isinstance(row, dict) and isinstance(row.get('ts'), (int, float)) and row['ts'] >= cutoff:
                            result.append(row)
                    except ValueError:
                        continue
        except OSError:
            continue
    return result


def summarize(root, days=30, now=None):
    """Read only aggregate/local state. Dataset IDs, prose, paths and raw text are omitted."""
    root = Path(root)
    now = time.time() if now is None else now
    cutoff = now - max(1, min(int(days), 365)) * 86400
    journal = _read_journal(root, cutoff)
    tools = {}
    hermes_tools = {}
    pii_by_source = {}
    input_tokens = output_tokens = 0
    usage_events = 0
    local_answers = dict(answered=0, forwarded=0, estimated_input_tokens_avoided=0)
    forwarded_reasons = Counter()
    for row in journal:
        if row.get('kind') == 'local_answer':
            for key in local_answers:
                value = row.get(key, 0)
                if type(value) in (int, float) and math.isfinite(value) and value >= 0:
                    local_answers[key] += value
            reason = row.get('reason')
            if row.get('forwarded') == 1 and reason in LOCAL_ANSWER_REASONS:
                forwarded_reasons[reason] += 1
        elif row.get('kind') == 'tool' and row.get('tool') in TOOLS:
            item = tools.setdefault(row['tool'], {'ok': 0, 'errors': {}, 'duration_ms': 0, 'response_bytes': 0})
            item['duration_ms'] += max(0, int(row.get('duration_ms', 0) or 0))
            item['response_bytes'] += max(0, int(row.get('response_bytes', 0) or 0))
            if row.get('ok') is True:
                item['ok'] += 1
            else:
                code = row.get('code')
                if code in ERROR_CODES:
                    item['errors'][code] = item['errors'].get(code, 0) + 1
                else:
                    item['errors']['unknown'] = item['errors'].get('unknown', 0) + 1
        elif row.get('kind') == 'hermes_tool':
            name = row.get('tool')
            if isinstance(name, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', name):
                item = hermes_tools.setdefault(name, dict(ok=0, errors=0, duration_ms=0))
                item['ok' if row.get('ok') is True else 'errors'] += 1
                item['duration_ms'] += max(0, int(row.get('duration_ms', 0) or 0))
        elif row.get('kind') == 'usage':
            input_tokens += max(0, int(row.get('input_tokens', 0) or 0))
            output_tokens += max(0, int(row.get('output_tokens', 0) or 0))
            usage_events += 1

    masked = Counter()
    observed = Counter()
    prompt_warnings = 0
    prompt_injection_alerts = 0
    sources = Counter()
    for event in _safe_read(root / 'aggregates.json', {}).get('events', []):
        if not isinstance(event, dict) or event.get('timestamp', 0) < cutoff:
            continue
        category, source = event.get('category'), event.get('source')
        if event.get('prompt_warning') is True:
            prompt_warnings += 1
        if category in PII_CATEGORIES:
            observed[category] += 1
            if category == 'prompt_injection':
                prompt_injection_alerts += 1
            if (source in ('tool_result', 'terminal') or
                    (source in ('user_prompt', 'auxiliary_call') and event.get('action') == 'masked')) and category != 'prompt_injection':
                masked[category] += 1
            source = source if isinstance(source, str) and source in ('tool_result', 'terminal', 'api_request', 'auxiliary_call', 'user_prompt') else 'unknown'
            pii_by_source.setdefault(source, Counter())[category] += 1
            sources[source] += 1

    choices = _safe_read(root / 'choices.json', {})
    methods = {}
    for family, choice in choices.items() if isinstance(choices, dict) else []:
        recipe = choice.get('recipe_id') if isinstance(choice, dict) else None
        if family in FAMILIES and recipe in METHODS:
            methods.setdefault(family, Counter())[recipe] += 1

    predictions = Counter()
    trainings = Counter()
    decisions = _safe_read(root / 'decisions.json', [])
    for decision in decisions if isinstance(decisions, list) else []:
        if not isinstance(decision, dict):
            continue
        outcome = decision.get('outcome') or {}
        if outcome.get('status') in ('measured', 'INSUFFICIENT_DATA', 'PLAN_ONLY', 'QUALITY_GATE_NOT_MET'):
            trainings[outcome['status']] += 1
        for prediction in outcome.get('predictions', []):
            if isinstance(prediction, dict):
                verdict = prediction.get('decision')
                predictions['accepted' if verdict == 'accepted' else 'refused'] += 1

    telemetry = _safe_read(root / 'handle-telemetry.json', {})
    counters = dict(handled_locally=0, abstained=0, estimated_tokens_avoided=0)
    for values in telemetry.values() if isinstance(telemetry, dict) else []:
        if isinstance(values, dict):
            for key in counters:
                value = values.get(key, 0)
                if type(value) in (int, float) and math.isfinite(value) and value >= 0:
                    counters[key] += value
    local_answer_count = local_answers['answered'] if any(local_answers.values()) or forwarded_reasons else None
    handling_sources = {'handle_telemetry': counters['handled_locally'] if telemetry else None,
                        'local_answers': local_answer_count}
    available_handling = [v for v in handling_sources.values() if v is not None]
    handled_locally = sum(available_handling) if available_handling else None
    estimated_avoided = counters['estimated_tokens_avoided'] + local_answers['estimated_input_tokens_avoided']
    calibration_events = 0
    calibration_ref = _safe_read(root / 'calibration.json', {})
    ref = calibration_ref.get('detail_ref') if isinstance(calibration_ref, dict) else None
    if isinstance(ref, str) and re.fullmatch(r'calibrations/cal-[a-p]{64}\.json', ref):
        details = _safe_read(root / ref, {})
        events = details.get('events', []) if isinstance(details, dict) else []
        calibration_events = len(events) if isinstance(events, list) else 0
    savings = savings_summary(root, estimated_avoided)

    return {
        'period_days': max(1, min(int(days), 365)),
        'operations': tools,
        'hermes_operations': hermes_tools,
        'abstained': counters['abstained'],
        'prompt_warnings': prompt_warnings,
        'prompt_injection_alerts': prompt_injection_alerts,
        'pii_masks': dict(sorted(masked.items())),
        'observed_categories': dict(sorted(observed.items())),
        'mask_sources': dict(sorted(sources.items())),
        'pii_by_source': {source: dict(sorted(counts.items())) for source, counts in sorted(pii_by_source.items())},
        'predictions': {'accepted': predictions['accepted'], 'refused': predictions['refused']},
        'methods': {family: dict(sorted(counts.items())) for family, counts in sorted(methods.items())},
        'training': dict(sorted(trainings.items())),
        'calibration_events': calibration_events,
        'tokens_observed': ({'input': input_tokens, 'output': output_tokens} if usage_events else
                            {'input': None, 'output': None, 'reason': _usage_not_observed_reason(root)}),
        'local_answers': local_answers,
        'forwarded_reasons': dict(sorted(forwarded_reasons.items())),
        'handled_locally': handled_locally,
        'handling_sources': handling_sources,
        'estimated_tokens_avoided': estimated_avoided,
        'savings': savings,
        'metrics_scope': 'operations and alerts: journal period; handle, methods and cost: retained lifetime state',
    }


def community_payload(summary):
    """Build a strictly allowlisted public aggregate schema."""
    def rounded(value):
        return int(round(value / 5.0) * 5)

    pii = {key: rounded(value) for key, value in summary['pii_masks'].items() if value >= MIN_COUNT}
    operations = {}
    for tool, values in summary['operations'].items():
        operations[tool] = {'ok': rounded(values['ok']), 'errors': {code: rounded(count) for code, count in values['errors'].items()},
                            'duration_ms': rounded(values['duration_ms']), 'response_bytes': rounded(values['response_bytes'])}
    payload = {
        'schema_version': SCHEMA_VERSION,
        'period_days': summary['period_days'],
        'state_metrics_scope': 'retained_state',
        'operations': operations,
        'pii_masks': pii,
        'pii_by_source': {source: {category: rounded(count) for category, count in counts.items() if count >= MIN_COUNT}
                          for source, counts in summary['pii_by_source'].items()
                          if any(count >= MIN_COUNT for count in counts.values())},
        'predictions': {k: rounded(v) for k, v in summary['predictions'].items()},
        'methods': {family: {method: rounded(count) for method, count in methods.items()}
                    for family, methods in summary['methods'].items()},
        'training': {k: rounded(v) for k, v in summary['training'].items()},
        'calibration_events': rounded(summary['calibration_events']),
        'local_answers': {k: rounded(v) for k, v in summary['local_answers'].items()},
        'forwarded_reasons': {reason: rounded(count) for reason, count in summary['forwarded_reasons'].items()
                              if count >= MIN_COUNT},
    }
    if summary['tokens_observed'].get('input') is not None:
        payload['tokens_observed'] = {k: rounded(v) for k, v in summary['tokens_observed'].items()
                                      if type(v) is int}
    if summary.get('prompt_injection_alerts', 0) >= MIN_COUNT:
        payload['prompt_injection_alerts'] = rounded(summary['prompt_injection_alerts'])
    if summary['handled_locally'] is not None:
        payload['handled_locally'] = rounded(summary['handled_locally'])
    return payload


def export_allowed(scout):
    grants = scout.profile()['config'].get('grants', {})
    return 'insights' in grants and bool(grants['insights'])
