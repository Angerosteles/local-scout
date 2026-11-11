"""Explicit local consent UI and reproducible demos A/B."""
import argparse
import json
from pathlib import Path
import time
from .. import register
from ..scout.context import LocalContext
from .generate import demo_a,demo_b


def main():
    parser=argparse.ArgumentParser(description='Local Scout: synthetic demo, local settings only')
    parser.add_argument('action',choices=['A','B','settings','delete'])
    parser.add_argument('--home',type=Path,required=True,help='Isolated local profile')
    parser.add_argument('--approve',default='',help='Explicit comma-separated observe,report,train')
    parser.add_argument('--mode',choices=['off','background','report','on_demand'],default='off')
    parser.add_argument('--confirm-delete',action='store_true')
    parser.add_argument('--decisions',type=Path,help='Local reviewed JSON decisions; absent means brief only')
    args=parser.parse_args()
    ctx=LocalContext(args.home.resolve());register(ctx)
    scout=ctx.tools['scout_inspect']['handler'].scout
    scopes=[x for x in args.approve.split(',') if x]
    if any(x not in ('observe','report','train') for x in scopes):
        parser.error('Unknown consent scope')
    if args.action=='delete':
        if not args.confirm_delete:
            parser.error('--confirm-delete required')
        scout.delete_local();print('Deleted this local profile data.');return
    if args.action=='settings':
        scout.configure_local(mode=args.mode,grants={s:['profile' if args.action=='settings' else 'demo_a'] for s in scopes})
        print(json.dumps({'mode':args.mode,'granted_scopes':scopes}));return
    needed={'observe','train'} if args.action=='A' else {'observe','report'}
    if not needed<=set(scopes):
        parser.error('Explicit local --approve '+','.join(sorted(needed))+' required')
    if args.action=='A':
        # Only safe synthetic A is persisted; B is always generated in memory.
        args.home.mkdir(parents=True,exist_ok=True)
        path=args.home.resolve()/'synthetic-a.jsonl'
        with path.open('w') as out:
            for row in demo_a(): out.write(json.dumps(row)+'\n')
        scout.configure_local(mode='on_demand',grants={s:['demo_a'] for s in scopes},sources={'demo_a':path})
        rows=list(demo_a())
        def call(name,**params):
            result=json.loads(ctx.tools[name]['handler'](params))
            print(json.dumps({'tool':name,**result},ensure_ascii=False))
            if not result['ok']: raise SystemExit(1)
            return result['data']
        call('scout_inspect',dataset_id='demo_a')
        for candidate in ('triage','search','fields'):
            call('scout_recipes',candidate_id=candidate)
        if args.decisions is None:
            print(json.dumps({'status':'AWAITING_AGENT','note':'Brief ready. No recipe IDs selected by demo code.'}))
            return
        if args.decisions.is_symlink() or args.decisions.stat().st_size>16384:
            parser.error('Invalid local decisions file')
        choices=json.loads(args.decisions.read_text(encoding='utf-8'))
        if not isinstance(choices,list) or len(choices)!=3 or any(not isinstance(c,dict) or set(c)!={'candidate_id','recipe_id','reason'} for c in choices):
            parser.error('Expected three explicit decisions')
        if {c['candidate_id'] for c in choices}!={'triage','search','fields'}:
            parser.error('Expected one decision per candidate')
        print(json.dumps({'decision_provenance':'local caller file; live Hermes agent unverified'}))
        for choice in choices:
            kind=choice['candidate_id'];recipe=choice['recipe_id']
            selected=call('scout_choose',**choice)
            if selected['status']!='EXECUTABLE':
                continue
            query=next(r['id'] for r in rows if r['kind']==kind and r['group'].endswith('g09'))
            aid=recipe if recipe == 'regex' else call('scout_train',candidate_id=kind,recipe_id=recipe,dataset_id='demo_a')['artifact_id']
            call('scout_predict',artifact_id=aid,query_id=query)
            if kind=='triage':
                scout.approve_query_local('unknown_words','zyzzyva quux frobnicate')
                call('scout_predict',artifact_id=aid,query_id='unknown_words')
        call('scout_calculate',n=100,cloud={'amount':.1,'currency':'PLN'},local=.01,p_abstain=.2,fallback=.1,setup=2,error_rate=.1,error_cost=.05)
    else:
        scout.configure_local(mode='report',grants={s:['profile'] for s in scopes})
        for event in demo_b(time.time()): scout.observe(event)
        print(ctx.tools['scout_report']['handler']({'dataset_id':'profile'}))
        notice=scout.notify_output(response_text='Demo B — lokalny raport')
        print(json.dumps({'local_notification':notice,'delivery_to_Hermes_UI_unverified':True},ensure_ascii=False))


if __name__=='__main__':
    main()
