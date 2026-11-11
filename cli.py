"""Local operator CLI: menu, configure explicit profile, replay, freeze, evaluate, delete."""
import argparse
import json
import os
from pathlib import Path
import sys

from .scout.context import LocalContext
from .scout.core import Scout
from .scout import storage


def main(argv=None):
    ap = argparse.ArgumentParser(prog='scout')
    ap.add_argument('--home', type=Path, help='Explicit Hermes profile; alternatively HERMES_HOME, never implicit default')
    sub = ap.add_subparsers(dest='command')
    sub.add_parser('menu', help='Interactive menu: masking on/off, mode, local triage, Insights')
    conf = sub.add_parser('configure')
    conf.add_argument('--mode', choices=['off','background','report','on_demand'],
                      help='Required unless run in a terminal, where omitting it opens the menu')
    conf.add_argument('--dataset', default='demo_a')
    conf.add_argument('--source', type=Path)
    conf.add_argument('--synthetic-pii', action='store_true', help='Mask locally detected PII in this explicitly synthetic dataset')
    conf.add_argument('--approve', default=None, help='Explicit comma-separated observe,report,train,mask,insights,answer; scope@dataset targets another dataset (e.g. mask@profile); absent preserves grants')
    conf.add_argument('--answer-task', choices=['triage'], default=None)
    conf.add_argument('--answer-marker', default=None)
    conf.add_argument('--basal-url', default=None, help='Local basal engine, e.g. http://127.0.0.1:8765 (loopback only); empty string removes it')
    conf.add_argument('--basal-descriptions', type=Path, default=None, help='Optional JSON file {label: one-line description} for basal')
    setup = sub.add_parser('setup-triage')
    setup.add_argument('--history', type=Path, required=True)
    setup.add_argument('--dataset', default='support')
    setup.add_argument('--marker')
    setup.add_argument('--mask-prompts', action='store_true')
    setup.add_argument('--recipe', choices=['tfidf_logreg', 'basal_zero_shot'], default='tfidf_logreg',
                       help='basal_zero_shot needs a local engine registered with configure --basal-url')
    setup.add_argument('--max-error', type=float)
    setup.add_argument('--yes', action='store_true')
    setup.add_argument('--json', action='store_true')
    ins = sub.add_parser('insights')
    ins.add_argument('--days', type=int, default=30)
    ins.add_argument('--html', type=Path, metavar='PLIK')
    ins.add_argument('--preview', action='store_true')
    ins.add_argument('--export', type=Path, metavar='PLIK')
    cal = sub.add_parser('calibrate')
    cal.add_argument('--dataset', default='demo_a')
    cal.add_argument('--history', type=Path, help='Explicit operator-approved JSONL history; text kept only in RAM')
    cal.add_argument('--approve-history', action='store_true')
    cal.add_argument('--model-path', type=Path, help='Existing local sentence-transformers directory; unavailable means LSA')
    cal.add_argument('--threshold', type=float, default=.8)
    cal.add_argument('--min-cases', type=int, default=3)
    cal.add_argument('--seed', type=int, default=20261105)
    cal.add_argument('--tokens-per-task', type=float, default=1000)
    cal.add_argument('--price-per-million', type=float, default=10)
    cal.add_argument('--local-cost', type=float, default=0)
    cal.add_argument('--setup-cost', type=float, default=0)
    cal.add_argument('--error-cost', type=float, default=0)
    cal.add_argument('--currency', choices=['PLN','USD','EUR'], default='PLN')
    cal.add_argument('--html', type=Path)
    cal.add_argument('--json', type=Path)
    cal.add_argument('--interactive', action='store_true')
    handle = sub.add_parser('handle')
    handle.add_argument('--task', choices=['triage','search','fields'], required=True)
    handle.add_argument('--text', required=True)
    inspect = sub.add_parser('inspect')
    inspect.add_argument('--dataset', required=True)
    choose = sub.add_parser('choose')
    choose.add_argument('--task', choices=['triage','search','fields'], required=True)
    choose.add_argument('--recipe', required=True)
    choose.add_argument('--reason', required=True)
    train = sub.add_parser('train')
    train.add_argument('--task', choices=['triage','search'], required=True)
    train.add_argument('--recipe', required=True)
    train.add_argument('--dataset', required=True)
    for command in ('freeze','evaluate'):
        sub.add_parser(command).add_argument('--dataset', default='demo_a')
    sub.add_parser('status')
    sub.add_parser('delete').add_argument('--confirm-delete', action='store_true')
    args = ap.parse_args(argv)
    home = args.home or (Path(os.environ['HERMES_HOME']) if os.environ.get('HERMES_HOME') else None)
    if home is None:
        ap.error('Provide --home or HERMES_HOME; there is no default profile.')
    scout = Scout(LocalContext(home.resolve()))
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if args.command in (None, 'menu') or (args.command == 'configure' and args.mode is None):
        if not interactive:
            ap.error('--mode is required' if args.command == 'configure' else 'choose a command (the menu needs a terminal)')
        from .scout.menu import run as menu
        menu(scout, home)
        return
    try:
        if args.command == 'setup-triage':
            from .scout.setup_triage import run
            result = run(scout, args)
            if args.json:
                print(json.dumps(result, ensure_ascii=False))
            else:
                print('Ready: {examples} examples, {classes} classes; threshold {threshold:.2f}.'.format(**result))
                print('Validation coverage: {:.1%}; accepted error: {:.1%}.'.format(result['metrics']['coverage'], result['metrics']['accepted_error']))
                print('Validation curve (threshold, coverage, accepted error, accepted count):')
                for point in result['validation_curve']:
                    error = 'n/a' if point['accepted_error'] is None else '{:.1%}'.format(point['accepted_error'])
                    print('  {:.2f}  {:.1%}  {}  {}'.format(point['threshold'], point['coverage'], error, point['accepted']))
                if result['title_risk']:
                    print('Title risk:', result['title_risk'])
                print('Next steps:')
                for step in result['next_steps']:
                    print('  ' + step)
        elif args.command == 'configure':
            # `scope@dataset` grants one scope for another dataset (e.g. mask@profile for prompt/tool
            # masking next to answer for the triage dataset); a bare scope uses --dataset. Validation stays in core.
            grants = None
            if args.approve is not None:
                grants = {}
                for item in (s.strip() for s in args.approve.split(',')):
                    if item:
                        scope, _, dataset = item.partition('@')
                        targets = grants.setdefault(scope, [])
                        if (dataset or args.dataset) not in targets:
                            targets.append(dataset or args.dataset)
            descriptions = json.loads(args.basal_descriptions.read_text(encoding='utf-8')) if args.basal_descriptions else None
            scout.configure_local(args.mode, grants=grants, sources={args.dataset:args.source} if args.source else None,
                                  synthetic_pii=args.synthetic_pii, answer_task=args.answer_task,
                                  answer_marker=args.answer_marker, basal_url=args.basal_url, basal_descriptions=descriptions)
            result = dict(status='CONFIGURED', mode=args.mode,
                          effective_scope={k:v for k,v in scout.profile()['config'].get('grants',{}).items()}, title_risk=scout.title_risk())
            if args.basal_url:
                result['basal_notice'] = ('The engine is also asked about uncertain password-like values during masking: one line '
                                          'at a time, unmasked, on loopback, at most 8 s per tool result or prompt. '
                                          'Remove it with --basal-url "".')
            print(json.dumps(result))
        elif args.command == 'status':
            config=scout.profile()['config']
            decisions_path = scout.root()/'decisions.json'
            entries = storage.read_json(decisions_path) if decisions_path.is_file() else []
            outcome = next((e.get('outcome') for e in reversed(entries) if (e.get('outcome') or {}).get('validation_curve')), None)
            print(json.dumps(dict(mode=config['mode'], grants=config.get('grants',{}),
                                  effective_scope=config.get('grants',{}), title_risk=scout.title_risk(), validation_curve=outcome.get('validation_curve') if outcome else None)))
        elif args.command == 'delete':
            if not args.confirm_delete:
                ap.error('--confirm-delete is required')
            scout.delete_local()
            print('{"status":"DELETED"}')
        elif args.command == 'insights':
            from .scout import insights
            from .scout.presentation import insights_terminal, write_insights_html
            summary = insights.summarize(scout.root(), args.days)
            if args.preview or args.export:
                if not insights.export_allowed(scout):
                    raise ValueError
                payload = insights.community_payload(summary)
                if args.preview:
                    print(json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False))
                if args.export:
                    storage.write_json(args.export, payload)
            elif args.html:
                write_insights_html(summary, args.html)
            else:
                insights_terminal(summary)
        elif args.command in ('handle', 'inspect', 'choose', 'train'):
            params = dict(handle=dict(task=getattr(args,'task',None), text=getattr(args,'text',None)),
                          inspect=dict(dataset_id=getattr(args,'dataset',None)),
                          choose=dict(candidate_id=getattr(args,'task',None), recipe_id=getattr(args,'recipe',None), reason=getattr(args,'reason',None)),
                          train=dict(candidate_id=getattr(args,'task',None), recipe_id=getattr(args,'recipe',None), dataset_id=getattr(args,'dataset',None)))
            result = scout.call('scout_'+args.command, params[args.command])
            print(json.dumps(result, ensure_ascii=False))
            if not result['ok']:
                ap.exit(2)
        elif args.command in ('freeze','evaluate'):
            from .scout import operator
            print(json.dumps(getattr(operator,args.command)(scout,args.dataset)))
        else:
            from .scout.calibration import replay
            from .scout.presentation import terminal, write_html
            from .scout.operator import digest
            p = {k:getattr(args,k) for k in ('threshold','min_cases','seed','tokens_per_task','price_per_million','local_cost','setup_cost','error_cost','currency')}
            if args.history:
                if not args.approve_history or args.history.is_symlink() or args.history.stat().st_size > 8*1024*1024:
                    ap.error('Requires --approve-history and a regular JSONL file up to 8 MiB')
                rows = []
                with args.history.open(encoding='utf-8') as stream:
                    for line in stream:
                        if len(line)>65536 or len(rows)>=20000:
                            raise ValueError
                        row=json.loads(line)
                        if row.get('split')=='test':
                            raise ValueError
                        if not isinstance(row.get('text'),str) or len(row['text'])>20000:
                            raise ValueError
                        rows.append(row)
            else:
                if scout.profile()['config']['mode']=='off':
                    raise ValueError
                scout.grant('observe',args.dataset);scout.grant('report',args.dataset)
                all_rows=scout.rows(args.dataset)
                rows=scout.decision_rows(args.dataset, all_rows)
            report = replay(rows,p,args.model_path)
            if not args.history:
                report.update(dataset_id=args.dataset,input_digest=digest(rows),scope='train+validation_only')
                from .scout.calibration import replay_rows
                history, _ = replay_rows(rows, p['seed'])
                report['card_facts'] = {kind:scout.calibration_facts(report,kind,all_rows,history) for kind in ('triage','search','fields')}
                report = scout.save_calibration(report)
            if args.html:
                write_html(report,args.html,detail_root=scout.root())
            if args.json:
                storage.write_json(args.json,report)
            terminal(report,args.interactive,detail_root=scout.root())
    except Exception as exc:
        if args.command == 'setup-triage':
            from .scout.setup_triage import SetupError
            message = str(exc) if isinstance(exc, SetupError) else 'Local setup failed; check file access and local dependencies.'
            ap.exit(2, 'Scout: ' + message + '\n')
        ap.exit(2, 'Scout: request refused or invalid local data; input details hidden.\n')


if __name__ == '__main__':
    main()
