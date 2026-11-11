"""Profile-scoped, opt-in Scout core. Agent tools cannot change consent or settings; only the operator CLI can."""
from collections import Counter
from datetime import datetime
import importlib.util
import json
import math
from pathlib import Path
import re
import secrets
import shutil
import threading
import time

from . import basal, privacy, storage, decisions, tool_guard
from .profile import facts, GUIDE
from .observer import extract
from .calculator import calculate
from ..schemas import FIELDS, validate

ID = re.compile(r'^[a-z0-9_-]{1,64}$')
LABEL_ID = re.compile(r'^[a-z0-9_:.-]{1,64}$')
KINDS = {'triage': 'classifier', 'search': 'index', 'fields': 'extraction'}
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_ROWS = 20_000
MAX_CLASSES = 256
LIMITATIONS = 'observed_attempt_only; not_a_firewall; no_delivery_proof; incomplete_PII_detection; no_alert_does_not_mean_safe; retry_dedup_session_only'
COVERAGE = 'request_messages_text_and_text_blocks_plus_system_prompt; metadata_ignored; nontext_unsupported; missing_origin_unknown; missing_request_id_not_deduplicated'
MESSAGES = {
    'OFF': 'Scout is off.', 'CONSENT_REQUIRED': 'Local consent is required for this scope.',
    'INVALID_INPUT': 'Invalid parameters.', 'NOT_FOUND': 'Approved ID not found.',
    'UNSUPPORTED_SOURCE': 'The source does not match the approved local dataset format.',
    'PLAN_ONLY': 'This recipe is a plan and cannot be executed.',
    'QUALITY_GATE_NOT_MET': 'Validation quality gate not met.',
    'INSUFFICIENT_DATA': 'Not enough data for valid validation.',
    'ARTIFACT_INVALID': 'The artifact is corrupt or invalid.', 'IO_ERROR': 'Local read or write error.'}


class ScoutError(Exception):
    pass


BASAL_MISSING = 'unavailable: no local basal engine configured (configure --basal-url http://127.0.0.1:<port>)'
UNAVAILABLE = ('unavailable: torch is missing', BASAL_MISSING)


def fail(code, message=None):
    raise ScoutError(code, message)


class Scout:
    def __init__(self, ctx, clock=None):
        self.ctx = ctx
        self.clock = clock or time.time
        self.lock = threading.RLock()
        self.profiles = {}

    def root(self):
        if hasattr(self.ctx, 'home'):
            home = Path(self.ctx.home)
        else:
            from hermes_constants import get_hermes_home
            home = get_hermes_home()
        return home / 'plugin-data' / 'local-scout'

    def profile(self):
        key = str(self.root())
        config_file = self.root() / 'settings.json'
        signature = (config_file.stat().st_mtime_ns, config_file.stat().st_size) if config_file.is_file() else None
        if key not in self.profiles:
            config = storage.read_json(config_file) if config_file.is_file() else dict(mode='off', grants={}, sources={})
            self.profiles[key] = dict(config=config, settings_signature=signature, seen={}, usage_seen={}, tool_seen={}, queries={}, events=[],
                                       usage=dict(input_tokens=None, output_tokens=None, quality='unknown'), notifications=[], notified={}, coverage={})
            aggregate = self.root() / 'aggregates.json'
            if aggregate.is_file():
                self.profiles[key]['events'] = storage.read_json(aggregate)['events']
            active = self.root() / 'active.json'
            if active.is_file():
                self.profiles[key]['active_dataset'] = storage.read_json(active).get('dataset_id')
        state = self.profiles[key]
        if state.get('settings_signature') != signature:
            if signature is None:
                self.profiles.pop(key)
                return self.profile()
            state['config'] = storage.read_json(config_file)
            state['settings_signature'] = signature
        return state

    def configure_local(self, mode='off', grants=None, sources=None, retention_days=30, basal_url=None, basal_descriptions=None,
                        synthetic_pii=False, answer_task=None, answer_marker=None):
        """Trusted local UI/CLI only; never registered as an agent tool."""
        mode = 'background' if mode == 'on' else mode
        if mode not in ('off', 'background', 'report', 'on_demand') or not 1 <= retention_days <= 30 or type(synthetic_pii) is not bool:
            raise ValueError
        with self.lock:
            state = self.profile()
            config = dict(state['config'])
            config.update(mode=mode, retention_days=retention_days)
            config['synthetic_pii'] = synthetic_pii
            if answer_task not in (None, 'triage') or (answer_marker is not None and
                    (not isinstance(answer_marker, str) or not 1 <= len(answer_marker) <= 256)):
                raise ValueError
            if answer_task is not None:
                config['answer_task'] = answer_task
            if answer_marker is not None:
                config['answer_marker'] = answer_marker
            if grants is not None:
                if set(grants)-{'observe', 'report', 'train', 'mask', 'insights', 'answer'} or any(not isinstance(v,list) or any(not ID.fullmatch(s) for s in v) for v in grants.values()):
                    raise ValueError
                config['grants'] = grants
            if sources is not None:
                approved = {}
                for did, path in sources.items():
                    if not ID.fullmatch(did):
                        raise ValueError
                    path = Path(path)
                    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_SOURCE_BYTES:
                        raise ValueError
                    approved[did] = str(path.resolve())
                config['sources'] = approved
            if basal_url is not None:
                # '' removes the engine; otherwise only http://<loopback IP>:<port> is accepted.
                config.pop('gguf_path', None)
                if basal_url:
                    config['basal_url'] = basal.check_url(basal_url)
                else:
                    config.pop('basal_url', None)
            if basal_descriptions is not None:
                # Operator-written category descriptions; masked like any other text that may reach a model.
                config['basal_descriptions'] = {label: privacy.mask_local_details(text)[0]
                                                for label, text in basal.check_descriptions(basal_descriptions).items()}
            # Setting off on a pristine plugin need not create any directory.
            if mode != 'off' or self.root().exists():
                storage.write_json(self.root() / 'settings.json', config)
            state['config'] = config

    def title_risk(self):
        config = self.profile()['config']
        if config.get('mode', 'off') == 'off' or 'profile' not in config.get('grants', {}).get('mask', []):
            return None
        instruction = 'Set auxiliary.title_generation.enabled: false in the Hermes profile config.yaml and restart Hermes.'
        try:
            path = self.root().parent.parent / 'config.yaml'
            if not path.exists():
                enabled = True
            else:
                if path.stat().st_size > 1024 * 1024:
                    raise ValueError('config too large')
                import yaml
                data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
                value = (data.get('auxiliary') or {}).get('title_generation') or {}
                enabled = value.get('enabled', True)
            if enabled is False:
                return None
            return 'Title generation may send prompt content through auxiliary calls; hook protection requires live verification. ' + instruction
        except Exception:
            return 'Cannot verify title generation configuration; assume it is enabled. ' + instruction

    def delete_local(self):
        with self.lock:
            root = self.root()
            self.profiles.pop(str(root), None)
            if root.exists() and not root.is_symlink():
                shutil.rmtree(root)

    def grant(self, scope, dataset):
        if dataset not in self.profile()['config'].get('grants', {}).get(scope, []):
            fail('CONSENT_REQUIRED')

    def rows(self, dataset):
        config = self.profile()['config']
        source = config.get('sources', {}).get(dataset)
        if source is None:
            fail('NOT_FOUND')
        path = Path(source)
        if path.is_symlink() or path.stat().st_size > MAX_SOURCE_BYTES:
            fail('UNSUPPORTED_SOURCE')
        result, ids = [], set()
        synthetic_pii = bool(config.get('synthetic_pii', False))
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                if len(line) > 65536 or len(result) >= MAX_ROWS:
                    fail('UNSUPPORTED_SOURCE')
                row = json.loads(line)
                if not isinstance(row,dict) or not {'id','group','kind','text','label','destination'} <= row.keys():
                    fail('UNSUPPORTED_SOURCE')
                if any(not isinstance(row[k],str) or not ID.fullmatch(row[k]) for k in ('id','group','kind')) or not isinstance(row['label'],str) or not LABEL_ID.fullmatch(row['label']):
                    fail('UNSUPPORTED_SOURCE')
                if row['id'] in ids or not isinstance(row['text'], str) or len(row['text']) > 20000:
                    fail('UNSUPPORTED_SOURCE')
                kind=row['kind']
                if row['destination'] not in ('cloud','local','unknown') or kind not in (*KINDS,'doc','pesel','email','phone','address','negative','secret'):
                    fail('UNSUPPORTED_SOURCE')
                if set(row)-{'id','group','kind','text','label','destination','timestamp','api_request_id','split','title','entity_types'}:
                    fail('UNSUPPORTED_SOURCE')
                if any(privacy.categories(row[key]) for key in ('id','group','label')):
                    fail('UNSUPPORTED_SOURCE')
                request_id=row.get('api_request_id')
                if request_id is not None and (not isinstance(request_id,str) or privacy.categories(request_id)):
                    fail('UNSUPPORTED_SOURCE')
                if kind in KINDS or kind == 'doc':
                    if kind == 'fields' and row['label'] == 'unknown':
                        fail('UNSUPPORTED_SOURCE')
                    if kind == 'fields' and 'entity_types' in row:
                        types = row['entity_types']
                        if (not isinstance(types,list) or len(types)>MAX_CLASSES or len(set(types))!=len(types) or
                                any(not isinstance(t,str) or not LABEL_ID.fullmatch(t) for t in types) or
                                (types and row['label'] not in types) or (not types and row['label']!='none')):
                            fail('UNSUPPORTED_SOURCE')
                    if not ID.fullmatch(row['id']):
                        fail('UNSUPPORTED_SOURCE')
                    found = privacy.categories(row['text'])
                    if found and not synthetic_pii:
                        fail('UNSUPPORTED_SOURCE')
                    # Keep only detector types in memory; raw PII never reaches artifacts or output.
                    row['_detected_types'] = list(found)
                    if synthetic_pii and found:
                        row['text'], _ = privacy.mask_local_details(row['text'])
                    title = row.get('title')
                    if title is not None:
                        if not isinstance(title,str) or len(title)>512:
                            fail('UNSUPPORTED_SOURCE')
                        title_types = privacy.categories(title)
                        if title_types and not synthetic_pii:
                            fail('UNSUPPORTED_SOURCE')
                        if synthetic_pii and title_types:
                            row['title'], _ = privacy.mask_local_details(title)
                    if kind == 'doc' and row['label'] != row['id']:
                        fail('UNSUPPORTED_SOURCE')
                    if kind == 'search' and row['label'] != 'unknown' and not ID.fullmatch(row['label']):
                        fail('UNSUPPORTED_SOURCE')
                elif row['label'] != kind or not re.fullmatch(r'b-[0-9]{3,4}',row['id']):
                    fail('UNSUPPORTED_SOURCE')
                if 'split' in row and row['split'] not in ('train','validation','test'):
                    fail('UNSUPPORTED_SOURCE')
                ids.add(row['id'])
                result.append(row)
        docs = {r['id'] for r in result if r['kind']=='doc'}
        for kind in KINDS:
            labels = set()
            for r in result:
                if r['kind']!=kind or r['label']=='unknown':
                    continue
                labels.update((r.get('entity_types') or [r['label']]) if kind=='fields' else (r['label'],))
            if kind in ('triage','fields') and len(labels)>MAX_CLASSES:
                fail('UNSUPPORTED_SOURCE')
        if docs and any(r['kind']=='search' and r['label']!='unknown' and r['label'] not in docs for r in result):
            fail('UNSUPPORTED_SOURCE')
        doc_ids = [r['id'] for r in result if r['kind']=='doc']
        doc_titles = [r.get('title') for r in result if r['kind']=='doc']
        if len(doc_titles)!=len(doc_ids) or any(not isinstance(t,str) or not t for t in doc_titles):
            fail('UNSUPPORTED_SOURCE')
        return result

    def approve_query_local(self, query_id, text, dataset='demo_a'):
        if not ID.fullmatch(query_id) or not isinstance(text,str) or len(text)>4096 or privacy.categories(text):
            raise ValueError
        self.grant('observe',dataset)
        self.profile()['queries'][query_id] = (dataset,dict(id=query_id,text=text))

    def decision_rows(self, dataset, rows=None):
        from .learning import split, Insufficient
        rows = self.rows(dataset) if rows is None else rows
        allowed = []
        for kind in KINDS:
            subset = [r for r in rows if r['kind'] == kind]
            try:
                train, validation, _ = split(subset)
                allowed.extend(train + validation)
            except Insufficient:
                pass
        ids = {r['id'] for r in allowed}
        return [r for r in rows if r['id'] in ids and r['kind']!='doc']

    def query(self, query_id):
        state = self.profile()
        if query_id in state['queries']:
            dataset,row = state['queries'][query_id]
            self.grant('observe',dataset)
            return dataset,row
        for dataset in state['config'].get('sources', {}):
            if dataset not in state['config'].get('grants',{}).get('observe',[]):
                continue
            for row in self.decision_rows(dataset):
                if row['id'] == query_id and row['kind'] in KINDS:
                    return dataset,row
        fail('NOT_FOUND')

    def _timestamp(self, event):
        value = event.get('timestamp',event.get('started_at'))
        if type(value) in (int,float) and math.isfinite(value):
            return float(value)
        if isinstance(value,str):
            try:
                return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
            except ValueError:
                pass
        return self.clock()

    def _event(self,event,seen,category_cache=None):
        now = self.clock()
        stamp = self._timestamp(event)
        if not now-86400 <= stamp <= now+60:
            return []
        rid = event.get('api_request_id')
        if isinstance(rid,str) and len(rid)<=256:
            if rid in seen:
                return []
            if len(seen)>=10000:
                seen.pop(next(iter(seen)))
            seen[rid] = stamp
        texts,coverage = extract(event)
        state=self.profile()
        counters=state['coverage'].setdefault(event.get('dataset_id','profile'),dict(text_parts=0,unsupported_parts=0,truncated_parts=0))
        for key,value in coverage.items():
            counters[key]+=value
        cats = set()
        for value in texts:
            found = privacy.categories(value)
            cats.update(found)
            if category_cache is not None:
                category_cache[value] = found
        origin = event.get('destination')
        origin = origin if origin in ('cloud','local') else 'unknown'
        return [dict(category=c, timestamp=stamp, scope=origin) for c in sorted(cats)]

    def _prune(self):
        state = self.profile()
        cutoff = self.clock()-state['config'].get('retention_days',30)*86400
        state['events'] = [e for e in state['events'] if e['timestamp']>=cutoff]
        state['notified']={k:v for k,v in state['notified'].items() if v>=self.clock()-86400}
        for key in ('seen','usage_seen'):
            state[key] = {k:v for k,v in state[key].items() if v>=cutoff}

    def observe(self, payload=None, **kwargs):
        # An observer returns nothing and never prints even an exception.
        try:
            with self.lock:
                state = self.profile()
                config = state['config']
                if config['mode'] not in ('background','report'):
                    return
                event = dict(payload or {}, **kwargs)
                dataset = event.get('dataset_id','profile')
                if dataset not in config.get('grants',{}).get('observe',[]):
                    return
                previous_count = len(state['events'])
                self._prune()
                request_id = event.get('api_request_id')
                duplicate = isinstance(request_id, str) and request_id in state['seen']
                category_cache = {}
                additions = self._event(event,state['seen'],category_cache)
                # with prompt masking active, llm_request (prompt_guard) records the masked
                # prompt and its notice; the observer sees the pre-mask request and would add a contradictory
                # "sent unmasked" notice and double-count the categories.
                mask_active = 'profile' in config.get('grants', {}).get('mask', [])
                warning = [] if duplicate or (mask_active and 'aux_task' not in event) else self._prompt_warning(event, category_cache)
                if warning:
                    for category in warning:
                        match = next((e for e in additions if e['category'] == category), None)
                        if match is not None:
                            match['prompt_warning'] = True
                        else:
                            additions.append(dict(category=category, timestamp=self.clock(), scope='unknown', source='user_prompt', prompt_warning=True))
                    if config['mode'] == 'report':
                        state['notifications'].append(dict(dataset_id=dataset, prompt_categories=warning))
                        state['notifications'] = state['notifications'][-100:]
                if not additions:
                    if len(state['events']) != previous_count:
                        storage.write_aggregates(self.root()/'aggregates.json', state['events'], now=self.clock())
                    return
                state['events'].extend(dict(e,dataset_id=dataset) for e in additions)
                state['events'] = state['events'][-10000:]
                storage.write_aggregates(self.root()/'aggregates.json', state['events'], now=self.clock())
                if config['mode']=='report' and dataset in config.get('grants',{}).get('report',[]):
                    report = self._report([e for e in state['events'] if e.get('dataset_id')==dataset],dataset)
                    for alert in report['alerts']:
                        key=(dataset,alert['category'],alert['scope'])
                        if key not in state['notified']:
                            state['notified'][key]=self.clock()
                            state['notifications'].append(dict(dataset_id=dataset,category=alert['category'],scope=alert['scope']))
                    state['notifications']=state['notifications'][-100:]
        except Exception:
            return

    def _prompt_warning(self, event, category_cache=None):
        messages = event.get('request_messages', event.get('messages', []))
        if not isinstance(messages, list):
            return []
        message = next((m for m in reversed(messages) if isinstance(m, dict) and m.get('role') == 'user'), {})
        content = message.get('content', '')
        parts = ([b['text'] for b in content if isinstance(b, dict) and b.get('type') in ('text', 'input_text') and isinstance(b.get('text'), str)]
                 if isinstance(content, list) else [content] if isinstance(content, str) else [])
        content = '\n'.join(parts)
        if not parts:
            return []
        # One turn sends several API requests with the same last prompt: warn once per prompt
        # (SHA-256 fingerprint kept in memory, no content), not on every request.
        import hashlib
        fingerprint = hashlib.sha256(content.encode('utf-8', 'replace')).hexdigest()[:24]
        seen_prompts = self.profile().setdefault('prompt_seen', [])
        if fingerprint in seen_prompts:
            return []
        seen_prompts.append(fingerprint)
        del seen_prompts[:-200]
        warning = getattr(privacy, 'prompt_warning', None)
        if callable(warning):
            found = warning(content)
        else:
            cache = category_cache or {}
            found = set()
            for part in parts:
                found.update(cache[part] if part in cache else privacy.categories(part))
        if isinstance(found, dict):
            found = found.get('categories', found)
        # Only fixed category names can enter a notification or persistent state.
        allowed = {'email', 'phone', 'ssn', 'card', 'address', 'iban', 'pesel', 'secret'}
        if not isinstance(found, (list, tuple, set, dict)):
            found = privacy.categories(content)
        return sorted({c for c in found if isinstance(c, str)} & allowed)

    def post_tool_call(self, tool_name=None, status=None, duration_ms=0, **kwargs):
        from .insights import record_hermes_tool
        record_hermes_tool(self, tool_name, status, duration_ms)

    def usage_hook(self, payload=None, **kwargs):
        try:
            with self.lock:
                state = self.profile()
                if state['config']['mode'] not in ('background','report'):
                    return
                event = dict(payload or {},**kwargs)
                if event.get('dataset_id','profile') not in state['config'].get('grants',{}).get('observe',[]):
                    return
                self._prune()
                usage = event.get('usage')
                if not isinstance(usage,dict):
                    return
                rid = event.get('api_request_id')
                rid=rid if isinstance(rid,str) and len(rid)<=256 else None
                if rid is not None and rid in state['usage_seen']:
                    return
                pairs = [('input_tokens','prompt_tokens'),('output_tokens','completion_tokens')]
                values = [usage.get(a,usage.get(b)) for a,b in pairs]
                if any(type(v) is not int or v<0 for v in values):
                    return
                if rid is not None:
                    if len(state['usage_seen'])>=10000:
                        state['usage_seen'].pop(next(iter(state['usage_seen'])))
                    state['usage_seen'][rid] = self.clock()
                for key,value in zip(('input_tokens','output_tokens'),values):
                    state['usage'][key] = (state['usage'][key] or 0)+value
                state['usage']['quality'] = 'measured' if rid is not None else 'unknown'
        except Exception:
            return

    def _report(self, events, dataset_id):
        counts = Counter((e['category'],e['scope']) for e in events if e.get('source') != 'user_prompt' and self.clock()-86400 <= e['timestamp'] <= self.clock()+60)
        alerts = [dict(category=c,count=n,scope=s,status='observed_attempt',proposal='local_detector_and_masking')
                  for (c,s),n in sorted(counts.items()) if n>=3]
        tool_events = [dict(category=e['category'], source=e['source'], timestamp=e['timestamp'], event_id=e['event_id'])
                       for e in self.profile()['events'] if e.get('dataset_id') == dataset_id and
                       e.get('source') in ('tool_result','terminal')][-100:]
        telemetry_path = self.root()/'handle-telemetry.json'
        telemetry = dict(handled_locally=0, abstained=0, estimated_tokens_avoided=0)
        if telemetry_path.is_file():
            telemetry.update(storage.read_json(telemetry_path).get(dataset_id, {}))
        from .insights import savings_summary
        telemetry.update(savings_summary(self.root(), telemetry['estimated_tokens_avoided']))
        telemetry.update(estimate_basis='sum of per handled task calibration estimates')
        return dict(handle_telemetry=telemetry,alerts=alerts,tool_events=tool_events,coverage=COVERAGE,
                    coverage_counts=dict(self.profile()['coverage'].get(dataset_id,dict(text_parts=0,unsupported_parts=0,truncated_parts=0))),limitations=LIMITATIONS)

    def _transform_tool_output(self, value, source, session_id='', tool_call_id=''):
        """Mask a tool or terminal result for the model and journal only metadata (categories, never text).

        Returns None (result unchanged) only when masking is confirmed off. If the consent state cannot be
        read, or anything fails after masking is on, the result is withheld instead of passed through raw:
        the same fail-closed rule as the prompt guard. The scan itself withholds on its own errors."""
        masking_off = False
        try:
            with self.lock:
                state = self.profile()
                config = state['config']
                if config.get('mode', 'off') == 'off' or 'profile' not in config.get('grants', {}).get('mask', []):
                    masking_off = True
                    return None
                event_id = secrets.token_hex(8)
                replacement, categories, partial = tool_guard.transform(value, source, event_id)
                now = self.clock()
                additions = []
                if partial:
                    additions.append(('coverage:partial', event_id))
                else:
                    session = session_id if isinstance(session_id, str) and len(session_id) <= 256 else ''
                    call = tool_call_id if isinstance(tool_call_id, str) and len(tool_call_id) <= 256 else ''
                    for category in categories:
                        dedup = (session, call, category)
                        if session and call and dedup in state['tool_seen']:
                            continue
                        if session and call:
                            if len(state['tool_seen']) >= 10000:
                                state['tool_seen'].pop(next(iter(state['tool_seen'])))
                            state['tool_seen'][dedup] = now
                        additions.append((category, event_id))
                if additions:
                    for category, item_id in additions:
                        state['events'].append(dict(category=category, timestamp=now, scope=source,
                                                    source=source, event_id=item_id, dataset_id='profile'))
                    state['events'] = state['events'][-10000:]
                    try:
                        storage.write_aggregates(self.root()/'aggregates.json', state['events'], now=now)
                    except Exception:
                        # Masking is already computed; journal failure must not restore raw output.
                        pass
                return replacement
        except Exception:
            return None if masking_off else tool_guard.withhold(value, 'scout_state_unavailable')[0]

    def transform_tool_result(self, tool_name=None, args=None, result=None, session_id='', tool_call_id='', **kwargs):
        return self._transform_tool_output(result, 'tool_result', session_id, tool_call_id)

    def transform_terminal_output(self, command=None, output=None, returncode=None, task_id='', tool_call_id='', **kwargs):
        return self._transform_tool_output(output, 'terminal', task_id, tool_call_id)

    def extras(self):
        config = self.profile()['config']
        return {'own_cnn': None if importlib.util.find_spec('torch') else 'unavailable: torch is missing',
                'basal_zero_shot': None if config.get('basal_url') else BASAL_MISSING}

    def runtime_model(self, meta):
        """Attach runtime-only settings to a loaded artifact (the basal engine address is never stored in it)."""
        if meta.get('engine') != 'basal_zero_shot':
            return meta
        url = self.profile()['config'].get('basal_url')
        if not url:
            fail('PLAN_ONLY', BASAL_MISSING)
        return dict(meta, basal_url=url)

    def call(self,name,params):
        with self.lock:
            try:
                if self.profile()['config']['mode']=='off':
                    fail('OFF')
                if name not in FIELDS or not isinstance(params,dict) or set(params)!=set(FIELDS[name]):
                    fail('INVALID_INPUT')
                if name=='scout_choose':
                    issue = privacy.reason_issue(params['reason'])
                    if issue:
                        fail('INVALID_INPUT', f'The reason parameter violates a rule: {issue}.')
                validate(name,params)
                for key,value in params.items():
                    if key.endswith('_id') and (not isinstance(value,str) or not ID.fullmatch(value)):
                        fail('INVALID_INPUT')
                value = getattr(self,name)(**params)
                return {'ok':True,'data':value}
            except ScoutError as exc:
                code = exc.args[0]
                if len(exc.args)>1 and exc.args[1] in UNAVAILABLE:
                    return {'ok':False,'error':{'code':code,'message':exc.args[1]}}
                if len(exc.args)>1 and isinstance(exc.args[1],str):
                    return {'ok':False,'error':{'code':code,'message':exc.args[1]}}
            except OSError:
                code = 'IO_ERROR'
            except (ValueError,TypeError,KeyError,IndexError,OverflowError):
                code = 'INVALID_INPUT'
            except Exception:
                code = 'IO_ERROR'
            return {'ok':False,'error':{'code':code,'message':MESSAGES[code]}}

    def decision_facts(self, rows, kind):
        result=facts([r for r in rows if r['kind']==kind],kind)
        extras=self.extras()
        result.update(torch_available=extras['own_cnn'] is None, basal_available=extras['basal_zero_shot'] is None,
                      cloud_transfer_consent=False)
        return result

    def scout_inspect(self,dataset_id):
        self.grant('observe',dataset_id)
        rows=self.rows(dataset_id)
        counts = Counter(row['kind'] for row in rows)
        from .catalog import recipes
        self.profile()['active_dataset']=dataset_id
        storage.write_json(self.root()/'active.json', dict(dataset_id=dataset_id))
        candidates = [dict(id=kind,kind=task,count=counts[kind],recipe_ids=[r['id'] for r in recipes()],
                           profile=self.decision_facts(rows,kind))
                      for kind,task in KINDS.items() if counts[kind]>=20]
        calibration = self.root()/'calibration.json'
        if calibration.is_file():
            from .calibration_output import read_details
            evidence = read_details(storage.read_json(calibration), self.root())
            from .operator import digest
            if evidence.get('dataset_id') == dataset_id and evidence.get('input_digest') == digest(self.decision_rows(dataset_id)):
                for c in candidates:
                    facts = evidence.get('card_facts', {}).get(c['id']) or self.calibration_facts(evidence, c['id'], rows)
                    c['calibration_evidence'] = dict(facts, detail_ref=storage.read_json(calibration).get('detail_ref'))
                    c['calibration_evidence']['families'] = [
                        {k:v for k,v in f.items() if k != 'card_conditions'} for f in facts['families']]
        choices_path = self.root()/'choices.json'
        choices = storage.read_json(choices_path) if choices_path.is_file() else {}
        decisions_path = self.root()/'decisions.json'
        entries = storage.read_json(decisions_path) if decisions_path.is_file() else []
        for candidate in candidates:
            choice = choices.get(candidate['id'], {})
            entry = next ( (e for e in entries if e['decision_id']==choice.get('decision_id') and e['dataset_id']==dataset_id), None)
            outcome = (entry or {}).get('outcome') or {}
            candidate['active_validation'] = dict(recipe_id=choice.get('recipe_id') if entry else None,
                metrics=outcome.get('metrics'), scope='validation',
                abstentions=(outcome['metrics']['n_test'] - round(outcome['metrics']['coverage'] * outcome['metrics']['n_test'])) if outcome.get('metrics') else None)
        cards = [dict(id=i, disk=d, ram=r, license=l, requirements=req) for i,d,r,l,req in [
            ('basal_zero_shot','pico 0.8B Q8 ~0.8 GB','~1.8 GB; ~2 s per ticket on a 15 W laptop CPU (measured offline)','Apache-2.0','local basal-serve engine (configure --basal-url)'),
            ('lfm3','Q4 ~1.1 GB','weights plus runtime; unmeasured','LFM Open License v1.0; verify variant','compatible backend'),
            ('lfm25_moe','Q4 >=12 GB','weights plus runtime','LFM Open License v1.0; verify terms','compatible backend'),
            ('minicpm_v47','Q4 ~0.5–1 GB (1B); ~17.5 GB (35B)','weights plus runtime','Apache-2.0','compatible multimodal backend'),
            ('small_local_llm','model dependent','model dependent','model dependent','compatible backend')]]
        for card in cards:
            card.update(recipe='recipes/'+card['id']+'.md', installation='Review recipe and model license; obtain compatible weights and runtime separately.', expected_gain='unknown until measured on your holdout')
        return dict(candidates=candidates,usage=self.profile()['usage'],decision_brief=GUIDE,
                    upgrade_cards=cards, measurement='CLI --model-path accepts a local sentence-transformers directory for calibration replay, not GGUF. basal_zero_shot calls the local basal engine set with configure --basal-url and picks its threshold on validation like the other methods. Compare on a separately frozen holdout.')

    def candidate(self,candidate_id):
        if candidate_id not in KINDS:
            fail('NOT_FOUND')
        state=self.profile()
        active=state.get('active_dataset')
        datasets=[active] if active else list(state['config'].get('sources',{}))
        for dataset in datasets:
            if dataset in state['config'].get('grants',{}).get('observe',[]):
                if sum(r['kind']==candidate_id for r in self.rows(dataset))>=20:
                    return dataset
        fail('CONSENT_REQUIRED')

    def scout_recipes(self,candidate_id):
        dataset=self.candidate(candidate_id)
        profile=self.decision_facts(self.rows(dataset),candidate_id)
        from .catalog import recipes,conditions
        catalog=[dict(r,fit=conditions(r,profile)) for r in recipes()]
        return dict(candidate_id=candidate_id,recipes=catalog,profile=profile,decision_brief=GUIDE)

    def scout_choose(self,candidate_id,recipe_id,reason):
        dataset=self.candidate(candidate_id)
        self.require_unfrozen()
        from .catalog import recipes,conditions
        recipe = next((r for r in recipes() if r['id']==recipe_id),None)
        if not recipe:
            fail('NOT_FOUND')
        status = {'executable':'EXECUTABLE','plan':'PLAN_ONLY','external':'CLOUD_ONLY'}[recipe['implementation']]
        availability = self.extras().get({'conv1d':'own_cnn'}.get(recipe_id,recipe_id))
        if availability:
            status = 'PLAN_ONLY'
        path = self.root()/'choices.json'
        choices = storage.read_json(path) if path.is_file() else {}
        profile=self.decision_facts(self.rows(dataset),candidate_id)
        entry=decisions.record(self.root(),dataset,candidate_id,recipe_id,reason,profile,conditions(recipe,profile))
        choices[candidate_id] = dict(dataset_id=dataset,recipe_id=recipe_id,reason=reason,decision_id=entry['decision_id'])
        storage.write_json(path,choices)
        task_ok = recipe['task'] in ({'triage':{'classification'},'search':{'search'},
                                     'fields':{'extraction','fields and PII'}}[candidate_id])
        audit_conflict = entry['audit']['verdict'] in ('premise_false','against_card_conditions')
        if status != 'EXECUTABLE':
            next_step = 'Choice recorded without an artifact. This recipe cannot run locally; do not call scout_train. Review the cards and decide what to do next.'
        elif audit_conflict or not task_ok:
            next_step = 'Choice recorded without an artifact. The audit or task type indicates a conflict with the goal or premises; do not call scout_train or scout_predict. Review the card and premises.'
        elif recipe_id == 'regex':
            next_step = f'Choice recorded. Regex needs no training; call scout_handle(task=fields, text=...) for new work after CLI calibration. scout_predict with artifact_id=regex remains available for approved query_id evaluation.'
        else:
            next_step = (f'Choice recorded without an artifact. Call scout_train separately with candidate_id={candidate_id}, '
                         f'recipe_id={recipe_id}, dataset_id={dataset}; then review the validation result and use scout_handle for new work.')
        return dict(candidate_id=candidate_id,recipe_id=recipe_id,status=status,unavailable_reason=availability,
                    decision_id=entry['decision_id'],audit=entry['audit'],provenance=entry['provenance'],next_step=next_step)

    def scout_train(self,candidate_id,recipe_id,dataset_id,max_error=None):
        self.grant('train',dataset_id)
        self.grant('observe',dataset_id)
        self.require_unfrozen()
        if candidate_id not in KINDS:
            fail('NOT_FOUND')
        from . import learning
        from .catalog import recipes
        recipe = next((r for r in recipes() if r['id']==recipe_id),None)
        if recipe is None:
            fail('NOT_FOUND')
        engine = learning.ALIASES.get(recipe_id,recipe_id)
        if self.extras().get(engine):
            fail('PLAN_ONLY',self.extras()[engine])
        if recipe['implementation']!='executable' or engine == 'regex':
            fail('PLAN_ONLY')
        if (candidate_id=='fields' or (candidate_id=='search') != (engine in ('tfidf_search','embed_search'))):
            fail('INVALID_INPUT')
        rows = [r for r in self.rows(dataset_id) if r['kind']==candidate_id or (candidate_id=='search' and r['kind']=='doc')]
        try:
            config = self.profile()['config']
            meta,arrays = learning.train(rows,recipe_id,max_error=max_error,basal_url=config.get('basal_url'),
                                         descriptions=config.get('basal_descriptions'))
        except learning.QualityGateNotMet as exc:
            best, n, max_accepted = exc.args
            detail = (f'best accepted error {best[0]:.1%} at threshold {best[2]}, accepted n={best[1]}'
                      if best else f'best accepted n={max_accepted}, below the minimum of 5 cases')
            message = f'Validation quality gate not met: {detail}; required error <=10%, validation n={n}.'
            decisions.attach(self.root(),dataset_id,candidate_id,recipe_id,dict(status='QUALITY_GATE_NOT_MET'))
            fail('QUALITY_GATE_NOT_MET', message)
        except learning.Insufficient:
            decisions.attach(self.root(),dataset_id,candidate_id,recipe_id,dict(status='INSUFFICIENT_DATA'))
            fail('INSUFFICIENT_DATA')
        except basal.Unavailable as exc:
            fail('IO_ERROR', str(exc))
        except basal.ReservedLabel as exc:
            fail('INVALID_INPUT', str(exc))
        meta.update(dataset_id=dataset_id,candidate_id=candidate_id)
        aid = storage.save_artifact(self.root()/'artifacts',meta,arrays)
        result = {key:meta[key] for key in ('split_counts','threshold','metrics','elapsed_seconds')}
        if engine in ('tfidf_search','embed_search') and not meta.get('corpus_mode'):
            result['threshold'] = None
        for key in ('feature_importance','fallback','max_distance','threshold_source','validation_curve','threshold_selection'):
            if key in meta:
                result[key] = meta[key]
        result['metrics_scope']='validation'
        result['artifact_id']=aid
        result['decision_id']=decisions.attach(self.root(),dataset_id,candidate_id,recipe_id,dict(status='measured',**result))
        return result

    def scout_handle(self, task, text):
        from .calibration_output import read_details
        from .operator import digest
        state = self.profile()
        choices_path = self.root()/'choices.json'
        choices = storage.read_json(choices_path) if choices_path.is_file() else {}
        dataset = choices.get(task, {}).get('dataset_id') or state.get('active_dataset')
        if not dataset:
            fail('CONSENT_REQUIRED')
        self.grant('observe', dataset)
        self.grant('train', dataset)
        self.grant('report', dataset)
        calibration_path = self.root()/'calibration.json'
        if not calibration_path.is_file():
            fail('NOT_FOUND', 'Calibrate the active dataset first.')
        calibration = read_details(storage.read_json(calibration_path), self.root())
        if calibration.get('dataset_id') != dataset or calibration.get('input_digest') != digest(self.decision_rows(dataset)):
            fail('NOT_FOUND', 'Calibration is missing or stale.')
        choices_path = self.root()/'choices.json'
        choices = storage.read_json(choices_path) if choices_path.is_file() else {}
        choice = choices.get(task, {})
        entries_path = self.root()/'decisions.json'
        entries = storage.read_json(entries_path) if entries_path.is_file() else []
        entry = next((e for e in entries if e['decision_id']==choice.get('decision_id') and e['dataset_id']==dataset and e['candidate_id']==task), None)
        if not entry or choice.get('dataset_id') != dataset:
            fail('NOT_FOUND', 'An active decision and artifact are required.')
        outcome = entry.get('outcome') or {}
        artifact = outcome.get('artifact_id') if outcome.get('status') == 'measured' else None
        if task == 'fields' and choice.get('recipe_id') == 'regex':
            artifact = 'regex'
        if not artifact:
            fail('NOT_FOUND', 'The selected decision has no active artifact.')
        detected = list(privacy.categories(text))
        masked, _ = privacy.mask_local_details(text)
        row = dict(id='live', text=masked, _detected_types=detected)
        if artifact == 'regex':
            found = self.field_types(row)
            result = dict(decision='accepted' if found else 'unknown', matched_fields=found, score=None)
        else:
            meta, arrays = storage.load_artifact(self.root()/'artifacts', artifact)
            storage.validate_model(meta, arrays)
            from .learning import ALIASES
            if meta['dataset_id'] != dataset or meta['candidate_id'] != task or meta['engine'] != ALIASES.get(choice['recipe_id'], choice['recipe_id']):
                fail('ARTIFACT_INVALID')
            from .learning import predict
            try:
                result = predict(self.runtime_model(meta), arrays, row)
            except basal.Unavailable as exc:
                fail('IO_ERROR', str(exc))
        accepted = result['decision']=='accepted'
        tokens = calibration['parameters']['tokens_per_task'] if accepted else 0
        telemetry_path = self.root()/'handle-telemetry.json'
        telemetry = storage.read_json(telemetry_path) if telemetry_path.is_file() else {}
        counters = telemetry.setdefault(dataset, dict(handled_locally=0, abstained=0, estimated_tokens_avoided=0))
        counters['handled_locally' if accepted else 'abstained'] += 1
        counters['estimated_tokens_avoided'] += tokens
        storage.write_json(telemetry_path, telemetry)
        return dict(status='handled_locally' if accepted else 'abstained', decision_id=entry['decision_id'],
                    label=result.get('label') if accepted else None, document_id=result.get('document_id') if accepted else None,
                    matched_fields=result.get('matched_fields') if accepted else None, confidence=result.get('score'),
                    estimated_tokens_avoided=tokens, estimate_basis='calibration tokens_per_task; estimate per handled task')

    def scout_predict(self,artifact_id,query_id):
        dataset,row = self.query(query_id)
        return self._predict_approved(artifact_id, dataset, row)

    @staticmethod
    def field_types(row):
        redacted = {name.lower() for name in re.findall(r'\[([A-Za-z0-9][A-Za-z0-9_:.-]{0,63})\]', row['text'])}
        found = set(row.get('_detected_types', ())) | redacted
        if 'entity_types' not in row:
            found |= set(privacy.fields(row['text']))
        return sorted(found)

    def _predict_approved(self,artifact_id,dataset,row):
        # Private operator evaluation path; public tools resolve only train/validation IDs.
        if artifact_id=='regex':
            found = self.field_types(row)
            result=dict(decision='accepted' if found else 'unknown',label=None,document_id=None,title=None,
                        score=None,threshold=None,matched_fields=found)
            decisions.attach(self.root(),dataset,'fields','regex',dict(status='measured_prediction',
                predictions=[dict(query_id=row['id'],decision=result['decision'],matched_fields_count=len(found))],
                quality='single_prediction_not_quality_benchmark'))
            return result
        if artifact_id=='basal_zero_shot':
            # The recipe id is not an artifact: train it first (threshold from validation), then predict with the artifact id.
            fail('PLAN_ONLY', self.extras()['basal_zero_shot'] or 'basal_zero_shot is a recipe; train it with scout_train and predict with the returned artifact_id')
        descriptor = self.root()/'artifacts'/(artifact_id+'.json')
        if not descriptor.is_file():
            fail('NOT_FOUND')
        try:
            meta,arrays = storage.load_artifact(self.root()/'artifacts',artifact_id)
            storage.validate_model(meta,arrays)
            if meta['dataset_id']!=dataset:
                fail('CONSENT_REQUIRED')
            from .learning import predict
            try:
                result=predict(self.runtime_model(meta),arrays,row)
            except basal.Unavailable as exc:
                fail('IO_ERROR', str(exc))
            decisions.attach(self.root(),dataset,meta['candidate_id'],meta['engine'],dict(
                predictions=[dict(query_id=row['id'],decision=result['decision'],label=result.get('label'),score=result.get('score'))]))
            return result
        except ScoutError:
            raise
        except Exception:
            fail('ARTIFACT_INVALID')

    def scout_report(self,dataset_id):
        self.grant('report',dataset_id)
        self.grant('observe',dataset_id)
        if self.profile()['config']['mode']=='on_demand':
            seen,events = {},[]
            self.profile()['coverage'][dataset_id]=dict(text_parts=0,unsupported_parts=0,truncated_parts=0)
            for row in self.rows(dataset_id):
                events.extend(self._event(dict(row,dataset_id=dataset_id),seen))
            return self._report(events,dataset_id)
        self._prune()
        storage.write_aggregates(self.root()/'aggregates.json', self.profile()['events'], now=self.clock())
        return self._report([e for e in self.profile()['events'] if e.get('dataset_id')==dataset_id],dataset_id)

    def require_unfrozen(self):
        if (self.root()/'evaluation-lock.json').exists():
            fail('INVALID_INPUT')

    def calibration_facts(self, report, kind, rows, history=None):
        from .calibration import costs
        from .catalog import recipes, conditions
        from .profile import from_parts
        from .calibration import replay_rows
        from .learning import split
        if history is None:
            history, _ = replay_rows(self.decision_rows(report['dataset_id']), report['parameters']['seed'])
        subset = [r for r in rows if r['kind'] == kind]
        if not subset:
            return dict(scope='train+validation_only', families=[], assumptions=report['assumptions'], recommendation=None)
        train, validation, _ = split(subset)
        train_ids = {r['id'] for r in train}
        val_ids = {r['id'] for r in validation}
        groups = []
        for family in report['families']:
            pairs = [(r,e) for r,e in zip(history,report['events']) if e['family'] == family['id'] and e['kind'] == kind]
            if pairs:
                members = [r for r,e in pairs]
                profile = from_parts([r for r in members if r['id'] in train_ids],
                                     [r for r in members if r['id'] in val_ids],kind)
                extras = self.extras()
                profile.update(torch_available=extras['own_cnn'] is None,
                               basal_available=extras['basal_zero_shot'] is None,cloud_transfer_consent=False)
                cards = [dict(recipe_id=r['id'], conditions=conditions(r,profile)) for r in recipes()]
                eligible = [c['recipe_id'] for c in cards if all(x['ok'] is True for x in c['conditions'])]
                groups.append(dict(id=family['id'], **costs([e for r,e in pairs],report['parameters'],report['parameters']['threshold']),
                                   profile=profile, cards_satisfying_conditions=eligible, card_conditions=cards))
        return dict(scope='train+validation_only', families=groups, assumptions=report['assumptions'],
                    quality='nearest_prior_label_proxy_not_model_accuracy', recommendation=None)

    def scout_calibrate(self,dataset_id,threshold,min_cases,seed):
        self.grant('observe',dataset_id)
        self.grant('report',dataset_id)
        from .calibration import replay
        from .operator import digest
        all_rows = self.rows(dataset_id)
        rows = self.decision_rows(dataset_id, all_rows)
        result = replay(rows, dict(threshold=threshold,min_cases=min_cases,seed=seed))
        result.update(dataset_id=dataset_id, input_digest=digest(rows), scope='train+validation_only')
        from .calibration import replay_rows
        history, _ = replay_rows(rows, seed)
        result['card_facts'] = {kind:self.calibration_facts(result, kind, all_rows, history) for kind in KINDS}
        return self.save_calibration(result)

    def save_calibration(self, result):
        from .calibration_output import save_report, MAX_RESPONSE_BYTES
        summary = save_report(self.root(), result)
        summary.update({k: result[k] for k in ('sampled', 'n_total', 'n_replay', 'sampling')})
        while len(json.dumps(dict(ok=True, data=summary), ensure_ascii=False).encode()) > MAX_RESPONSE_BYTES:
            summary['families'].pop()
            summary['families_omitted'] += 1
        return summary

    def scout_calculate(self,**params):
        return calculate(params)

    def notify_output(self, response_text=None, **kwargs):
        """Append a fixed safe local notice to the next user-facing Hermes reply."""
        from .local_answers import is_local_answer
        if is_local_answer(response_text):
            return None
        with self.lock:
            state=self.profile()
            if state['config']['mode']!='report' or not isinstance(response_text,str):
                return None
            self._prune()
            lines=[]
            for item in state['notifications']:
                dataset=item['dataset_id']
                grants=state['config'].get('grants',{})
                if dataset not in grants.get('observe',[]):
                    continue
                if 'prompt_categories' in item:
                    lines.append('your prompt contained: '+', '.join(item['prompt_categories'])+('; masked in the main request; auxiliary protection depends on Hermes hook delivery' if item.get('prompt_masked') else '; detected in a request; protection and delivery are not confirmed'))
                    continue
                if dataset not in grants.get('report',[]):
                    continue
                report=self._report([e for e in state['events'] if e.get('dataset_id')==dataset],dataset)
                for alert in report['alerts']:
                    if (alert['category'],alert['scope'])==(item['category'],item['scope']):
                        lines.append(alert['category']+': '+str(alert['count'])+' ('+alert['scope']+')')
            state['notifications']=[]
            if not lines:
                return None
            # advise masking only when it is not already on for the profile.
            advice = '' if 'profile' in state['config'].get('grants',{}).get('mask',[]) else ' Consider local detection and masking.'
            return response_text+'\n\nLocal Scout — observed_attempt: '+', '.join(lines)+'.'+advice+' This is not a firewall or proof of delivery; PII detection is incomplete.'
