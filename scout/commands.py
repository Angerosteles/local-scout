"""Human command surfaces; these handlers are never registered as model tools."""
import argparse

from . import menu


HELP = '\n'.join((
    '/scout status | details | help',
    '/scout masking on|off · names off|basic|deep · addresses on|off',
    '/scout languages on|off · insights on|off · note on|off'))
# Chat names for menu settings; 'note' is the reply note (mode report/background).
NAMES = {'masking': 'masking', 'names': 'names', 'addresses': 'addresses', 'languages': 'languages',
         'insights': 'insights', 'note': 'mode'}
LOCAL_ONLY = 'Settings can only be changed in a local terminal session.\nUse hermes scout setup locally.'
LOCAL_PLATFORMS = ('cli', 'tui', 'local')


def local_terminal(source, platform):
    """Hermes 0.23 passes platform='cli' (classic CLI, source None) or platform='local' with a SessionSource
    whose platform is LOCAL (terminal or desktop client attached to this machine's gateway). Messaging and
    API platforms, conflicting or missing context are refused."""
    origin = getattr(getattr(source, 'platform', None), 'value', source)
    return (isinstance(platform, str) and platform in LOCAL_PLATFORMS
            and (origin is None or origin == '' or (isinstance(origin, str) and origin in LOCAL_PLATFORMS)))


def refused(platform):
    return LOCAL_ONLY + f' (this session: {platform if isinstance(platform, str) and platform else "unknown"})'


def status(scout):
    state = menu.state_for(scout)
    on = menu.display
    settings = (f"addresses {on(state['addresses'])} · other languages {on(state['languages'])} · "
                f"insights {on(state['insights'])} · reply note {on(state['mode'], 'mode')}")
    head = (['Local Scout: masking on', *menu.summary(state), 'Settings: ' + settings] if state['masking'] else
            ['Local Scout: masking off — nothing is masked; data reaches the model as typed.',
             f"When on: names {state['names']} · " + settings])
    return '\n'.join(menu._wrap([*head,
        f'Synthetic test, {menu.HOLDOUT_TEXTS:,} texts. More: /scout details · Commands: /scout help'], 80))


def details_text():
    header, rows, notes = menu.details()
    return '\n'.join(header + rows + notes + ['Full counts: docs/EVIDENCE.md'])


def slash_handler(scout):
    def handle(raw_args='', *, source=None, platform=None):
        args = raw_args.split()
        if args == ['details']:
            return details_text()
        if not args or args == ['status']:
            result = status(scout)
            return result if local_terminal(source, platform) else result + '\n' + refused(platform)
        if not local_terminal(source, platform):
            return status(scout) + '\n' + refused(platform)
        if args == ['help']:
            return HELP + '\n' + LOCAL_ONLY
        if len(args) != 2 or args[0] not in NAMES:
            return HELP
        name, value = args
        option = NAMES[name]
        choices = menu.OPTIONS['names'] if option == 'names' else ('on', 'off')
        if value not in choices:
            return 'Usage: /scout ' + name + ' ' + '|'.join(choices)
        previous = menu.state_for(scout)
        if option == 'mode':
            previous['mode'] = scout.profile()['config'].get('mode', 'off')
        state = dict(previous)
        if option == 'mode':  # note off keeps triage (on_demand), which shows no note either
            state['mode'] = 'report' if value == 'on' else (
                'on_demand' if previous['mode'] == 'on_demand' else 'background')
        else:
            state[option] = value if option == 'names' else value == 'on'
        if state == previous and option != 'insights':
            return 'No changes.' + ('\n' + menu.DEEP_WARNING if (option, value) == ('names', 'deep') else '')
        try:
            messages, _ = menu.save_state(scout, state, previous, changed=option)
        except (OSError, ValueError):
            return 'Scout: settings could not be saved; check local profile access.'
        return '\n'.join(messages)
    return handle


def setup_parser(parser):
    """Pass the tail verbatim to the existing CLI parser."""
    parser.add_argument('scout_args', nargs=argparse.REMAINDER)


def cli_handler(scout):
    def handle(args):
        from ..cli import main
        return main(['--home', str(scout.root().parents[1]), *args.scout_args])
    return handle


def register_commands(ctx, scout):
    register = getattr(ctx, 'register_command', None)
    if callable(register):
        register('scout', slash_handler(scout), description='Local Scout status and local settings')
    register_cli = getattr(ctx, 'register_cli_command', None)
    if callable(register_cli):
        # Hermes 0.23: register_cli_command(name, help, setup_fn, handler_fn=None, description='')
        register_cli('scout', 'Local Scout setup and operator commands', setup_parser, cli_handler(scout),
                     description='`hermes scout` opens the setup screen; other arguments go to the Scout CLI.')
