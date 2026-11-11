"""Bounded agent evidence and immutable local detail reports."""
import json
import re

from . import storage
from .calibration import costs

MAX_RESPONSE_BYTES = 6 * 1024


def read_details(report, root=None):
    if isinstance(report.get('events'), list):
        return report
    ref = report.get('detail_ref')
    if root is None or not isinstance(ref, str) or not re.fullmatch(r'calibrations/cal-[a-p]{64}\.json', ref):
        raise ValueError
    path = root / ref
    if path.parent.is_symlink():
        raise ValueError
    detail = storage.read_json(path)
    if detail_ref(detail) != ref:
        raise ValueError
    return detail


def detail_ref(report):
    data = json.dumps(report, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    return 'calibrations/cal-' + storage.digest(data) + '.json'


def bounded(report):
    """Keep the required fields; every omission is counted and recoverable."""
    ref = detail_ref(report)
    p = report['parameters']
    thresholds = sorted({0., .5, .9, 1., p['threshold']})
    points = lambda es: [[c['accepted'], round(c['coverage'], 4), c['errors'], c['unknown_errors']]
                         for c in (costs(es, p, t) for t in thresholds)]
    groups = []
    for family in report['families']:
        es = [e for e in report['events'] if e['family'] == family['id']]
        cards = {kind:next(f['cards_satisfying_conditions'] for f in facts['families'] if f['id'] == family['id'])
                 for kind, facts in report.get('card_facts', {}).items()
                 if any(f['id'] == family['id'] for f in facts['families'])}
        groups.append(dict(id=family['id'], count=len(es),
            noticed_at=next((e['t'] for e in es if e['noticed']), None),
            local_at=next((e['t'] for e in es if e['route'] == 'local'), None),
            points=points(es), cards_satisfying_conditions=cards))
    result = {k:report[k] for k in ('version','backend','model_fallback','order','scope','dataset_id',
                                   'parameters','assumptions','protocol','summary') if k in report}
    result.update(detail_ref=ref, events=dict(count=len(report['events']),
        noticed=sum(e['noticed'] for e in report['events']),
        local=sum(e['route'] == 'local' for e in report['events']), detail_ref=ref),
        families=groups, families_total=len(groups), families_omitted=0,
        curves=dict(thresholds=thresholds, fields=['accepted','coverage','errors','unknown_errors'],
                    points=points(report['events']), detail_ref=ref),
        moments='1-based replay position; null=never; local_at=would take over at selected threshold',
        card_scope='per kind within family; all requires true; facts, no ranking or recommendation')
    # Account for the actual tool envelope and default JSON formatting, including UTF-8.
    while len(json.dumps(dict(ok=True, data=result), ensure_ascii=False, allow_nan=False).encode()) > MAX_RESPONSE_BYTES:
        if not groups:
            raise ValueError
        groups.pop()
        result['families_omitted'] += 1
    return result


def save_report(root, report):
    summary = bounded(report)
    path = root / summary['detail_ref']
    if not path.exists():
        storage.write_json(path, report)
    storage.write_json(root / 'calibration.json', dict(detail_ref=summary['detail_ref']))
    return summary
