"""Interactive operator menu: the common actions without remembering flags.

Every change is explained in plain English and confirmed first, and the equivalent command is printed so the same
step can be scripted. The menu never enables anything on its own; it calls the same functions as the flag-based CLI.
"""
import argparse
from pathlib import Path

MASK_SCOPES = ('observe', 'report', 'insights', 'mask')
MODES = {'1': ('background', 'masking and flags as granted, no notices'),
         '2': ('report', 'masking and flags as granted, with short notices'),
         '3': ('on_demand', 'masking and flags as granted, no background collection')}


def _yes(ask, question):
    return ask(question + ' [y/N] ').strip().lower() in ('y', 'yes')


def describe(scout):
    """One status line: mode and what is switched on, never data."""
    config = scout.profile()['config']
    grants = config.get('grants', {})
    on = lambda scope: 'profile' in grants.get(scope, [])
    triage = 'on' if grants.get('answer') and config.get('answer_task') == 'triage' else 'off'
    mode = config.get('mode', 'off')
    masking = 'ON' if mode != 'off' and on('mask') else 'off'
    return f"mode: {mode} · masking: {masking} · insights: {'on' if on('insights') else 'off'} · local triage: {triage}"


def _merged(grants, scopes):
    merged = {key: list(value) for key, value in grants.items()}
    for scope in scopes:
        if 'profile' not in merged.setdefault(scope, []):
            merged[scope].append('profile')
    return merged


def masking_on(scout, ask, say):
    say('Masking on: Local Scout replaces detected personal data, secrets and password-like values in what')
    say('Hermes sends to the model, flags hidden instructions in what the agent reads, and keeps local counters')
    say('for Insights (categories and sources only, never text). Nothing leaves this machine because of it.')
    if not _yes(ask, 'Turn masking on?'):
        return
    config = scout.profile()['config']
    mode = config.get('mode', 'off')
    scout.configure_local('report' if mode == 'off' else mode, grants=_merged(config.get('grants', {}), MASK_SCOPES))
    say('Masking is on. Start a new Hermes session to use it.')
    say('Other grants were kept. On a fresh profile this equals:')
    say('  scout configure --mode report --dataset profile --approve observe,report,insights,mask')


def scout_off(scout, ask, say):
    say('Off: Local Scout stops masking, flagging and counting. Granted permissions and local data are kept.')
    if _yes(ask, 'Turn Local Scout off?'):
        scout.configure_local('off')
        say('Local Scout is off. Same as: scout configure --mode off')


def choose_mode(scout, ask, say):
    for key, (mode, text) in MODES.items():
        say(f'  {key}) {mode:<10} {text}')
    picked = MODES.get(ask('Mode: ').strip())
    if picked:
        scout.configure_local(picked[0])
        say(f'Mode set to {picked[0]}. Same as: scout configure --mode {picked[0]}')


def setup_triage(scout, ask, say):
    path = Path(ask('Labelled tickets file (JSONL with "text" and "label"): ').strip()).expanduser()
    if not path.is_file():
        say('No such file.')
        return
    marker = ask('Answer marker that starts the customer message in your prompts (Enter for none): ').strip() or None
    args = argparse.Namespace(history=path, dataset='support', marker=marker, mask_prompts=_yes(ask, 'Also mask prompts?'),
                              recipe='tfidf_logreg', max_error=None, yes=False, json=False)
    from .setup_triage import run
    result = run(scout, args)  # explains the permissions and asks for confirmation itself
    say('Ready: {examples} examples, {classes} classes; threshold {threshold:.2f}.'.format(**result))
    say('Same as: scout setup-triage --history ' + str(path) + (f' --marker "{marker}"' if marker else '') +
        (' --mask-prompts' if args.mask_prompts else ''))


def show_insights(scout, ask, say):
    from . import insights
    from .presentation import insights_terminal
    insights_terminal(insights.summarize(scout.root(), 30))


ACTIONS = [('Masking on (recommended)', masking_on), ('Local Scout off', scout_off),
           ('Mode: background / report / on demand', choose_mode),
           ('Set up local triage from a labelled file', setup_triage), ('Insights (last 30 days)', show_insights)]


def run(scout, home, ask=input, say=print):
    """Loop until the user quits; a failed action is reported without input details and the menu continues."""
    from .setup_triage import SetupError
    while True:
        say('')
        say(f'Local Scout · profile {home}')
        say(describe(scout))
        for number, (label, _) in enumerate(ACTIONS, 1):
            say(f' {number}) {label}')
        say(' q) Quit')
        try:
            choice = ask('Choose: ').strip().lower()
            if choice in ('q', 'quit', 'exit', ''):
                return
            if choice.isdigit() and 1 <= int(choice) <= len(ACTIONS):
                ACTIONS[int(choice) - 1][1](scout, ask, say)
        except (EOFError, KeyboardInterrupt):
            say('')
            return
        except SetupError as exc:
            say('Scout: ' + str(exc))
        except Exception:
            say('Scout: that step did not complete; input details hidden.')
