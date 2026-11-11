"""Independent deterministic synthetic corpus; no network and no copied tapes."""
import random


def demo_a(seed=20261105):
    rng=random.Random(seed)
    prompts={
        'build':['Implement parser module and stable public interface','Create component with validation and build artifacts','Wire feature into the command pipeline'],
        'test':['Write regression unit tests with fixtures','Verify input boundary and assert returned status','Cover repeated invocation and empty input in tests'],
        'dependency':['Pin package version in the dependency lockfile','Audit compatible library release and license metadata','Update maintained dependency runtime version']}
    notes={
        'build':['Builder creates artifacts from validated inputs','Parser output integrates with command runner','Components expose stable public interfaces'],
        'test':['Unit regression cases cover input boundaries','Fixtures verify repeated invocations and statuses','Tests include empty inputs and failures'],
        'dependency':['Pin compatible dependency package releases','Review library license and lockfile metadata','Check Python runtime version compatibility']}
    labels=list(prompts)
    for kind in ('triage','search','fields'):
        for group in range(12):
            for variant in range(10):
                n=group*10+variant+1
                label=labels[(group+variant)%3]
                if kind=='fields':
                    field=('issue','commit','version')[(group+variant)%3]
                    label=field
                    text={'issue':f'Issue SC-{n:04d} needs review.', 'commit':f'Commit {seed+n:010x} passed review.', 'version':f'Release version 2.{group}.0 is ready.'}[field]
                else:
                    text=rng.choice((notes if kind=='search' else prompts)[label])+f' Example {n}.'
                yield dict(id=f'a-{kind}-{n:03d}',group=f'{kind}-g{group:02d}',kind=kind,text=text,label=label,destination='local')


def demo_b(now):
    # Sensitive synthetic values exist only in the detector's local memory.
    for category in ('pesel','email','phone','address'):
        for index in range(1,7):
            digits='000000'+f'{index:04d}'
            check=(10-sum(int(d)*w for d,w in zip(digits,(1,3,7,9,1,3,7,9,1,3)))%10)%10
            value={'pesel':digits+str(check),'email':f'demo{index}@example.invalid',
                   'phone':f'+48 000 000 {index:03d}','address':f'ul. Testowa {index}, 00-000 Przykładowo'}[category]
            yield dict(id=f'b-{category}-{index}',group=f'b-{category}',kind=category,label=category,
                       text='Synthetic only: '+value,destination='cloud',timestamp=now-index,
                       api_request_id=f'demo-{category}-{index}',dataset_id='profile')
