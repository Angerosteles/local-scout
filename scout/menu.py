"""Single-screen local configuration, with state separate from terminal input."""
import shlex
import shutil
import textwrap

from . import insights

# Blind synthetic holdout v2, 1.9.0; basic names, addresses on, other languages off. See docs/EVIDENCE.md.
HOLDOUT_TEXTS = 2400
# Type, fully masked values, labelled target values, visible false masks across all texts.
STRUCTURED = (('e-mail', 200, 200, 32), ('PESEL', 212, 230, 0), ('card number', 200, 200, 2),
              ('SSN', 209, 230, 0), ('IBAN', 200, 200, 0), ('phone', 230, 230, 1),
              ('password/PIN/key', 199, 230, 19), ('address', 199, 230, 6))
# Language: full names, all name mentions, texts. Per mode: full masked, all masked, false names.
NAME_TOTALS = {'EN': (272, 827, 1582), 'PL': (132, 412, 758)}
NAMES = {'basic': {'EN': (233, 462, 252), 'PL': (90, 162, 178)},
         'deep': {'EN': (233, 579, 310), 'PL': (90, 216, 795)}}
SCOPES = ('observe', 'report', 'mask', 'insights')
SETTINGS = ('masking', 'names', 'addresses', 'languages', 'insights', 'mode', 'numbered')
ROWS = SETTINGS + ('details', 'save', 'cancel')
LABELS = {'languages': 'Other languages', 'mode': 'Reply note', 'numbered': 'Numbered masks'}
# 'mode' is shown as the reply note: report = note on, background = note off; on_demand stays a CLI/triage mode.
OPTIONS = {'names': ('off', 'basic', 'deep'), 'mode': ('report', 'background')}
NOTE = {'report': 'on', 'background': 'off', 'on_demand': 'off (triage)'}
DEEP_WARNING = 'Deep is aggressive: 795 false names in 758 PL texts (104.9 per 100).'
HELP = '↑↓ move · ENTER/SPACE change · s save · q quit'
DETAILS_HELP = 'ENTER or ESC returns · full counts: docs/EVIDENCE.md'
DESCRIPTIONS = {
    'numbered': {True: 'Use [EMAIL_1] etc.; equal tokens mean equal values. Nothing is restored.',
                 False: 'Plain masks. Turn on to number names and contacts. Nothing is restored.'},
    'masking': {True: 'Replace detected data with placeholders before the model sees it.',
                False: 'Scout is off: nothing is masked, noted or counted.'},
    'names': {'off': 'Names stay visible. Turn on to mask names in your prompts (not tool results).',
              'basic': 'Full names, a title with a surname, and their repeats.',
              'deep': 'Also single names in context; more false masks, very noisy on Polish.'},
    'addresses': {True: 'Mask street addresses (English and Polish formats).',
                  False: 'Street addresses stay visible.'},
    'languages': {True: 'Also mask names and addresses in other languages (less tested).',
                  False: 'Names and addresses only in English/Polish; other data in every language.'},
    'insights': {True: 'Save local counts (data types found, tool and token use); never text or values.',
                 False: 'Nothing is counted on disk.'},
    'mode': {'report': 'After a reply, a note names the data types detected (never values).',
             'background': 'No note under replies; masking works the same.',
             'on_demand': 'Triage mode (from setup-triage): no reply note. ENTER turns the note on.'},
    'details': 'ENTER opens the table: detection and false masks for every type.',
    'save': 'Write these settings to this Hermes profile.',
    'cancel': 'Leave setup; asks before discarding unsaved changes.',
}


def _percent(count, total):
    return round(100 * count / total)


def _per100(count, texts):
    rate = 100 * count / texts
    return '0' if count == 0 else (f'{rate:.2f}' if rate < 0.1 else f'{rate:.1f}')


def _span(values, suffix=''):
    low, high = min(values, key=float), max(values, key=float)
    return f'{low}{suffix}' if low == high else f'{low}–{high}{suffix}'


def summary(state):
    """Overview shared by the setup screen and /scout status, computed from the same table as Details."""
    if not state['masking']:
        return ['Masking off: nothing is masked; data reaches the model as typed.']
    types = [row for row in STRUCTURED if state['addresses'] or row[0] != 'address']
    lines = ['Masks: ' + ', '.join(name for name, _, _, _ in types),
             f"  Our test: {_span([_percent(n, total) for _, n, total, _ in types], '%')} masked · "
             f"{_span([_per100(f, HOLDOUT_TEXTS) for _, _, _, f in types])} false masks per 100 texts"]
    names = state['names']
    if names == 'off':
        lines.append('Names: off (optional; only in your prompts)')
    else:
        column, scope = (0, 'full names') if names == 'basic' else (1, 'all names')
        rows = NAMES[names]
        lines += [f'Names: {names}, only in your prompts',
                  f"  {scope} {_span([_percent(rows[lang][column], NAME_TOTALS[lang][column]) for lang in rows], '%')}"
                  f" detected · {_span([_per100(rows[lang][2], NAME_TOTALS[lang][2]) for lang in rows])}"
                  ' false masks per 100 texts']
    return lines


def details(width=80):
    """Every measured type in one table: header, rows, notes (no blank lines inside a part)."""
    header = ['Local Scout · measurements',
              f'Own blind synthetic test, {HOLDOUT_TEXTS:,} texts; real data will score lower.',
              'Measured with basic names, addresses on, other languages off.',
              'Detected = fully masked values. Each type has 50 lookalike traps.',
              'False = unlabelled spans / texts; rate per 100 texts.']
    notes = ['Names: only in your prompts; false masks per 100 EN or PL texts.',
             'Names, addresses: EN and PL. CVV: masked after a label, not measured.']
    names = [(f'{mode} {lang}', full, every, false) + NAME_TOTALS[lang]
             for mode, langs in NAMES.items() for lang, (full, every, false) in langs.items()]
    if width < 62:  # compact rows instead of columns that would wrap
        rows = ['  type: detected · false / texts']
        rows += [f'  {name}: {n}/{total} · {f}/{HOLDOUT_TEXTS}' for name, n, total, f in STRUCTURED]
        rows += ['  names (optional): full · all']
        for name, full, every, false, tf, ta, texts in names:
            rows += [f'  {name}: {full}/{tf} · {every}/{ta}',
                     f'    false {false}/{texts} ({_per100(false, texts)}/100)']
        return header, rows, notes
    rows = [f"  {'Type':<18}{'Detected':<15}False / texts · /100"]
    rows += [f'  {name:<18}{f"{n}/{total}":<8}{_percent(n, total):>4}%   {f}/{HOLDOUT_TEXTS} · {_per100(f, HOLDOUT_TEXTS)}'
             for name, n, total, f in STRUCTURED]
    rows += [f"  {'Names (optional)':<18}{'Full names':<15}{'All names':<15}False / texts · /100"]
    rows += [f'  {name:<18}{f"{full}/{tf}":<8}{_percent(full, tf):>4}%  '
             f'{f"{every}/{ta}":<8}{_percent(every, ta):>4}%  {false}/{texts} · {_per100(false, texts)}'
             for name, full, every, false, tf, ta, texts in names]
    return header, rows, notes


def state_for(scout):
    config = scout.profile()['config']
    grants = config.get('grants', {})
    mode = config.get('mode', 'off')
    return dict(masking=mode != 'off' and 'profile' in grants.get('mask', []),
                names=config.get('name_masking', 'off'),
                addresses=config.get('address_masking', 'on') == 'on',
                languages=config.get('other_languages', 'off') == 'on',
                numbered=config.get('pseudonyms', 'off') == 'on',
                insights=mode != 'off' and insights.collection_enabled(config),
                mode=mode if mode != 'off' else 'report')


def transition(state, index, key):
    """Mutate draft only; return selected row and optional save/cancel/details action."""
    if key in tuple(str(i) for i in range(1, len(ROWS) + 1)):
        index, key = int(key) - 1, '\n'
    if key in ('up', 'down'):
        index = (index + (-1 if key == 'up' else 1)) % len(ROWS)
    elif key == 's':
        return index, 'save'
    elif key == '\x03':
        return index, 'discard'
    elif key in ('\x1b', 'q'):
        return index, 'cancel'
    elif key in (' ', '\r', '\n', 'left', 'right'):
        name = ROWS[index]
        if name in ('save', 'cancel', 'details'):
            if key in (' ', '\r', '\n') or (name == 'details' and key == 'right'):
                return index, name
        elif name in OPTIONS:
            options = OPTIONS[name]
            current = state[name] if state[name] in options else options[-1]
            state[name] = options[(options.index(current) + (-1 if key == 'left' else 1)) % len(options)]
        else:
            state[name] = not state[name]
    return index, None


def _wrap(lines, width):
    return [part for line in lines for part in (textwrap.wrap(
        line, width=width, subsequent_indent='   ' if width > 6 else '',
        break_long_words=True, break_on_hyphens=False) or [''])]


def display(value, name=None):
    if name == 'mode':
        return NOTE.get(value, value)
    return ('on' if value else 'off') if isinstance(value, bool) else value.replace('_', ' ')


def label(name):
    return LABELS.get(name, name.title())


def render(state, index=0, width=80, confirming=False):
    """Render complete text at at most 80 columns; narrow screens reflow."""
    width = max(1, min(80, width))
    header = ['Local Scout setup', *summary(state),
              f'Own synthetic test, {HOLDOUT_TEXTS:,} texts; not a guarantee. Details: every type.']
    body = []
    for i, name in enumerate(ROWS):
        text = label(name)
        if name in SETTINGS:
            options = OPTIONS.get(name, (True, False))
            selected = state[name] if state[name] in options else options[-1]
            buttons = '  '.join(f"({'•' if selected == value else ' '}) {display(value, name)}" for value in options)
            if name == 'mode' and state[name] not in options:
                buttons += '  · triage mode'
            text = f'{text:<16} {buttons}'
        elif name == 'details':
            text = f'{text:<16} ▸ detection and false masks per type'
        body.extend(_wrap([(' → ' if i == index else '   ') + text], width))
    name = ROWS[index]
    description = DESCRIPTIONS[name][state[name]] if name in SETTINGS else DESCRIPTIONS[name]
    footer = _wrap(['Save changes? [y/n] · ESC returns to editing' if confirming else description, HELP], width)
    return '\n\n'.join('\n'.join(lines) for lines in (_wrap(header, width), body, footer))


def render_details(width=80):
    width = max(1, min(80, width))
    header, rows, notes = details(width)
    return '\n\n'.join('\n'.join(_wrap(lines, width)) for lines in (header, rows, notes + [DETAILS_HELP]))


def command_and_grants(scout, state, changed=None):
    config = scout.profile()['config']
    grants = {scope: list(targets) for scope, targets in config.get('grants', {}).items()}
    scopes = SCOPES if changed is None else (
        ('insights',) if changed == 'insights' else
        ('observe', 'report', 'mask') if changed == 'masking' else ())
    for scope in scopes:
        targets = [target for target in grants.get(scope, []) if target != 'profile']
        enabled = state['insights'] if scope == 'insights' else state['masking']
        if enabled:
            targets.append('profile')
        if targets or scope == 'insights':
            grants[scope] = targets
        else:
            grants.pop(scope, None)
    mode = state['mode'] if state['masking'] else 'off'
    if changed == 'mode':
        mode = state['mode']
    elif changed is not None and changed != 'masking':
        mode = config.get('mode', 'off')
    approvals = ','.join(f'{scope}@{target}' for scope, targets in sorted(grants.items()) for target in (targets or ['']))
    args = ['scout', 'configure', '--mode', mode, '--approve', approvals,
            '--name-masking', state['names'], '--address-masking', 'on' if state['addresses'] else 'off',
            '--other-languages', 'on' if state['languages'] else 'off',
            '--pseudonyms', 'on' if state['numbered'] else 'off']
    persist = state['insights'] if changed in (None, 'insights') else config.get('persist_observation')
    if persist is not None:
        args += ['--insights', 'on' if persist else 'off']
    return shlex.join(args), grants, mode


def save_state(scout, state, previous, changed=None):
    """Persist menu or slash edits through the same local configuration path."""
    command, grants, mode = command_and_grants(scout, state, changed)
    config = scout.profile()['config']
    scout.configure_local(mode, grants=grants, name_masking=state['names'],
                          address_masking='on' if state['addresses'] else 'off',
                          other_languages='on' if state['languages'] else 'off',
                          pseudonyms='on' if state['numbered'] else 'off',
                          retention_days=config.get('retention_days', 30),
                          synthetic_pii=config.get('synthetic_pii', False),
                          persist_observation=state['insights'] if changed in (None, 'insights')
                          else config.get('persist_observation'))
    changes = [f"{'reply note' if k == 'mode' else k} {display(previous[k], k)} → {display(state[k], k)}"
               for k in SETTINGS if previous[k] != state[k]]
    messages = ['Saved: ' + (', '.join(changes) or 'profile defaults') +
                '. Active from your next message — no restart needed.']
    if previous['masking'] != state['masking'] or previous['numbered'] != state['numbered']:
        messages.append('The masking system-prompt section updates in a new session.')
    if state['names'] == 'deep' and changed == 'names':
        messages.append(DEEP_WARNING)
    return ['\n'.join(_wrap(message.splitlines(), 80)) for message in messages], command


def run_keys(scout, keys, draw, say=print, width=lambda: 80):
    state, index = state_for(scout), 0
    initial = state.copy()
    confirming = viewing = False
    def redraw():
        draw(render_details(width()) if viewing else render(state, index, width(), confirming))
    redraw()
    try:
        for key in keys:
            dirty = state != initial
            if viewing:
                if key == '\x03':
                    break
                if key in ('\x1b', 'q', ' ', '\r', '\n', 'left'):
                    viewing = False
                redraw()
                continue
            if confirming:
                if key.lower() not in ('y', 'n'):
                    if key == '\x1b':
                        confirming = False
                    redraw()
                    continue
                action = 'save' if key.lower() == 'y' else 'discard'
            else:
                index, action = transition(state, index, key)
            if action == 'details':
                viewing = True
                redraw()
                continue
            if action == 'cancel':
                if dirty:
                    confirming = True
                    redraw()
                    continue
                say('No changes.')
                return
            if action == 'discard':
                break
            if action == 'save':
                if not dirty and (scout.root() / 'settings.json').is_file():
                    say('No changes.')
                    return
                messages, command = save_state(scout, state, initial)
                for message in messages:
                    say(message)
                say('Same as: ' + command)
                return
            redraw()
    except (KeyboardInterrupt, EOFError):
        pass
    say('Cancelled. Changes discarded.' if state != initial else 'No changes.')


def masking_on(scout, ask, say):
    """Compatibility for callers of the former guided action."""
    if ask('Turn masking on? [y/N] ').strip().lower() not in ('y', 'yes'):
        return
    state = state_for(scout)
    names = ask('Names: 1 basic, 2 deep, 3 off: ').strip()
    address = ask('Addresses: on/off: ').strip().lower()
    if names not in ('', '1', '2', '3') or address not in ('', 'on', 'off'):
        return
    state.update(masking=True, names={'1': 'basic', '2': 'deep', '3': 'off'}.get(names, state['names']),
                 addresses=(address == 'on') if address else state['addresses'])
    command, grants, mode = command_and_grants(scout, state)
    scout.configure_local(mode, grants=grants, name_masking=state['names'],
                          address_masking='on' if state['addresses'] else 'off',
                          other_languages='on' if state['languages'] else 'off')
    say('Same as: ' + command)


def run(scout, home):
    try:
        import curses
    except ImportError:
        def keys():
            while True:
                value = input('Row # to change; s save; q/ESC quit: ').strip()
                yield {'enter': '\n', 'space': ' ', 'esc': '\x1b', '': '\n'}.get(value.lower(), value.lower())
        def draw(screen):
            header, body, footer = screen.split('\n\n')
            lines = body.splitlines()
            # Number setup rows; the Details table is shown as is (ENTER returns).
            numbered = '\n'.join(f'{i + 1:>2}. {line[3:]}' if '→' in body else line for i, line in enumerate(lines))
            width = max(1, min(80, shutil.get_terminal_size((80, 24)).columns))
            print('\n'.join(_wrap('\n\n'.join((header, numbered, footer)).splitlines(), width)))
        run_keys(scout, keys(), draw)
        return

    def screen(stdscr):
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        stdscr.keypad(True)
        def draw(text):
            stdscr.clear()
            height, width = stdscr.getmaxyx()
            header, body, footer = [part.splitlines() for part in text.split('\n\n')]
            cursor = next((i for i, line in enumerate(body) if line.lstrip().startswith('→')), 0)
            # Keep the selected row and bottom help visible on short terminals.
            foot = footer[-max(0, height - 2):] if height > 2 else []
            if len(header) + len(body) + len(foot) + 2 <= height:
                header, foot = header + [''], [''] + foot
            head = header[:max(0, height - len(foot) - 2)]
            visible = max(1, height - len(head) - len(foot))
            offset = max(0, cursor - visible + 1)
            shown = head + body[offset:offset + visible] + foot
            for y, line in enumerate(shown[:height]):
                attr = curses.A_BOLD if line == header[0] or line.startswith(' → ') else curses.A_NORMAL
                if line.startswith(('Synthetic test', 'Names: only')):
                    attr = curses.A_DIM
                try:
                    stdscr.addnstr(y, 0, line, max(0, width - 1), attr)
                except curses.error:
                    pass
            stdscr.refresh()
        def keys():
            while True:
                key = stdscr.getch()
                yield {curses.KEY_UP: 'up', curses.KEY_DOWN: 'down', curses.KEY_LEFT: 'left',
                       curses.KEY_RIGHT: 'right', ord('k'): 'up', ord('j'): 'down',
                       curses.KEY_ENTER: '\n'}.get(key, chr(key) if 0 <= key < 256 else '')
        run_keys(scout, keys(), draw, messages.append, width=lambda: max(1, stdscr.getmaxyx()[1] - 1))
    messages = []
    try:
        curses.wrapper(screen)
    except KeyboardInterrupt:
        messages = ['Cancelled. Changes discarded.']
    for message in messages:
        print(message)


def non_tty(scout):
    print('Interactive mode requires a terminal.')
