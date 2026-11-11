"""Trusted local operator procedures. Never registered as Hermes tools."""
import hashlib
import json
from . import storage, learning


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def freeze(scout, dataset):
    scout.grant('train', dataset)
    scout.grant('observe', dataset)
    path = scout.root()/'evaluation-lock.json'
    if path.exists():
        raise ValueError('evaluation already locked')
    choices = storage.read_json(scout.root()/'choices.json')
    kinds = {r['kind'] for r in scout.rows(dataset)} & {'triage', 'search', 'fields'}
    if not kinds or not all(k in choices and choices[k].get('dataset_id') == dataset for k in kinds):
        raise ValueError('freeze requires a decision for each dataset family')
    artifacts = {}
    for kind in sorted(kinds):
        choice = choices[kind]
        if choice['recipe_id'] == 'regex':
            artifacts[kind] = choice['recipe_id']
            continue
        matches = []
        for f in (scout.root()/'artifacts').glob('*.json'):
            meta, arrays = storage.load_artifact(f.parent, f.stem)
            if meta['dataset_id'] == dataset and meta['candidate_id'] == kind and meta['engine'] == learning.ALIASES.get(choice['recipe_id'],choice['recipe_id']):
                matches.append((f.stat().st_mtime_ns, f.stem))
        if not matches:
            raise ValueError('missing trained artifact')
        artifacts[kind] = max(matches)[1]
    lock = dict(dataset_id=dataset, source=digest(scout.rows(dataset)), choices=digest(choices), artifacts=artifacts)
    storage.write_json(path, lock)
    return dict(status='FROZEN', dataset_id=dataset)


def evaluate(scout, dataset):
    scout.grant('train', dataset)
    scout.grant('observe', dataset)
    lock = storage.read_json(scout.root()/'evaluation-lock.json')
    target = scout.root()/'operator-holdout.json'
    if lock['dataset_id'] != dataset or target.exists() or (scout.root()/'evaluation-used.json').exists():
        raise ValueError('holdout unavailable')
    rows = scout.rows(dataset)
    if digest(rows) != lock['source'] or digest(storage.read_json(scout.root()/'choices.json')) != lock['choices']:
        raise ValueError('frozen inputs changed')
    # Consume before evaluation, so partial failure never permits repeated probing.
    storage.write_json(scout.root()/'evaluation-used.json', dict(consumed=True))
    results = {}
    for kind, aid in lock['artifacts'].items():
        _, _, test = learning.split([r for r in rows if r['kind'] == kind])
        if aid == 'regex':
            correct = sum(scout._predict_approved('regex', dataset, r)['matched_fields'] == sorted(r.get('entity_types', [] if r['label'] == 'none' else [r['label']])) for r in test)
            results[kind] = dict(accuracy=correct/len(test), n_test=len(test))
        else:
            meta, arrays = storage.load_artifact(scout.root()/'artifacts', aid)
            storage.validate_model(meta, arrays)
            results[kind] = learning.metrics(scout.runtime_model(meta), arrays, test)
    storage.write_json(target, dict(scope='operator_only_final_holdout', results=results))
    return dict(status='EVALUATED', scope='operator_only_final_holdout', results=results)
