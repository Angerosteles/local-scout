"""Bounded audit of explicit premises. Free prose is never certified as true."""
import re
import uuid
from . import storage

VALUE = r'-?\d+(?:\.\d+)?|true|false|[a-z][a-z0-9_]*'
PREMISE = re.compile(
    rf'(?<![\w.])(?P<key>[a-z][a-z0-9_]{{1,40}})\s*'
    rf'(?P<op><=|>=|!=|=|:|<|>)\s*(?P<raw>{VALUE})'
    rf'(?:\s*(?P<tail_op><=|>=|!=|=|<|>)\s*(?P<tail_raw>{VALUE}))?'
    r'(?![\w]|\.(?!\s|$)|\s*[<>=!:])')
PREMISE_ALIASES = {'min_examples': 'min_examples_per_label_train'}


def scalar(raw):
    if re.fullmatch(r'-?\d+(?:\.\d+)?', raw):
        return float(raw)
    return {'true': True, 'false': False}.get(raw, raw)


def evaluate(actual, op, expected):
    if actual is None:
        return None
    if type(actual) is not type(expected) and not (type(actual) in (int,float) and type(expected) in (int,float)):
        return None
    if op in ('=','!='):
        return actual == expected if op=='=' else actual != expected
    if type(actual) not in (int,float) or type(expected) not in (int,float):
        return None
    return {'<':actual < expected, '<=':actual <= expected, '>':actual > expected, '>=':actual >= expected}[op]


def audit(reason, facts, fit):
    checks = []
    for match in PREMISE.finditer(reason.lower()):
        key = match['key']
        if key not in facts:
            key = PREMISE_ALIASES.get(key, key)
        known = key in facts
        if not known:
            continue
        comparisons = [(match['op'], match['raw'])]
        if match['tail_op']:
            comparisons.append((match['tail_op'], match['tail_raw']))
        for op, raw in comparisons:
            op = '=' if op == ':' else op
            checks.append(dict(fact=key, op=op, actual=facts.get(key),
                               ok=evaluate(facts.get(key),op,scalar(raw))))
    if any(p['ok'] is False for p in checks):
        verdict = 'premise_false'
    elif any(p['ok'] is False for p in fit):
        verdict = 'against_card_conditions'
    elif not checks or any(p['ok'] is None for p in checks+fit):
        verdict = 'unverified'
    else:
        verdict = 'explicit_premises_consistent'
    return dict(verdict=verdict, premises=checks, conditions=fit, prose_status='unverified',
                note='Only explicit premises were checked; the audit neither selects a method nor proves free text true.')


def record(root, dataset, candidate, recipe, reason, facts, fit):
    path = root/'decisions.json'
    entries = storage.read_json(path) if path.is_file() else []
    did = 'decision-'+uuid.uuid4().hex.translate(str.maketrans('0123456789abcdef','abcdefghijklmnop'))
    entry = dict(decision_id=did, dataset_id=dataset, candidate_id=candidate, recipe_id=recipe,
                 reason=reason, profile=facts, audit=audit(reason,facts,fit),
                 provenance='caller_supplied; live_agent_unverified', outcome=None)
    storage.write_json(path,(entries+[entry])[-100:])
    return entry


def attach(root, dataset, candidate, recipe, outcome):
    path = root/'decisions.json'
    if not path.is_file():
        return None
    entries = storage.read_json(path)
    for entry in reversed(entries):
        if (entry['dataset_id'],entry['candidate_id'],entry['recipe_id']) == (dataset,candidate,recipe):
            merged={**(entry.get('outcome') or {}), **outcome}
            if 'predictions' in outcome:
                merged['predictions']=(entry.get('outcome') or {}).get('predictions',[])+outcome['predictions']
            entry['outcome']=merged
            storage.write_json(path,entries)
            return entry['decision_id']
    return None
