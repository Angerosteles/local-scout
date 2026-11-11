"""Local, consent-based orchestration of the existing triage pipeline."""
from collections import Counter
import hashlib
import json
import math
import sys

from . import privacy, storage
from .core import ID, LABEL_ID, ScoutError

class SetupError(ValueError):
    pass


INTRO = '''This setup runs entirely on your computer. It saves a local copy of your training examples and a model in your profile.
Observe lets Scout read the approved dataset. Report lets Scout calculate local reports.
Train lets Scout train a local model. Insights lets you see local usage summaries.
Answer lets Scout answer eligible triage requests locally.
{mask}You can turn Scout off with: python -m plugin.cli --home HOME configure --mode off'''


def prepare(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
        raise SetupError('Choose a regular JSONL file up to 8 MiB, without a symlink.')
    rows = []
    masked_rows = 0
    try:
        with path.open(encoding='utf-8') as stream:
            for number, line in enumerate(stream, 1):
                if len(line) > 65536 or number > 20000:
                    raise SetupError('Use at most 20,000 lines and 65,536 characters per line.')
                try:
                    row = json.loads(line)
                except (ValueError, UnicodeError):
                    raise SetupError(f'Line {number} is not valid JSON; use one JSON object per line.') from None
                if not isinstance(row, dict) or not isinstance(row.get('text'), str) or not row['text'].strip() or len(row['text']) > 20000:
                    raise SetupError(f'Line {number}: provide a nonempty text string of at most 20,000 characters.')
                if not isinstance(row.get('label'), str) or not LABEL_ID.fullmatch(row['label']) or privacy.categories(row['label']):
                    raise SetupError(f'Line {number}: provide a label using up to 64 letters, digits, _ : . or -.')
                # real support histories contain phone numbers/e-mails; mask locally instead of
                # rejecting the whole file (the stored copy and the model never see the raw values).
                # The full mask (including password-like values, codes and PINs), not only the regex categories.
                masked_text, masked_categories = privacy.mask_local_details(row['text'])
                if masked_categories:
                    row = dict(row, text=masked_text)
                    masked_rows += 1
                if 'split' in row and row['split'] not in ('train', 'validation'):
                    raise SetupError(f'Line {number}: split must be train or validation; test data is not allowed.')
                rows.append(row)
    except UnicodeError:
        raise SetupError('Save the JSONL file as UTF-8.') from None
    if len(rows) < 20:
        raise SetupError('Provide at least 20 labeled examples.')
    counts = Counter(r['label'] for r in rows)
    # `unknown` = out-of-scope examples (the trainer already handles them); not a named class.
    named = {label: n for label, n in counts.items() if label != 'unknown'}
    if len(named) < 2 or len(named) > 256:
        raise SetupError('Provide 2 to 256 named classes (label unknown is allowed for out-of-scope examples).')
    if min(named.values()) < 2:
        raise SetupError('Provide at least two examples per class, including training examples.')
    if any('split' in r for r in rows) and not all('split' in r for r in rows):
        raise SetupError('Specify split on every line, or omit it on every line.')
    automatic = 'split' not in rows[0]
    normalized = []
    assignments = {}
    for i, row in enumerate(rows):
        digest = hashlib.sha256(('20261105\0' + row['text']).encode()).hexdigest()
        part = ('validation' if int(digest[:8], 16) % 5 == 0 else 'train') if automatic else row['split']
        previous = assignments.setdefault(digest, part)
        if previous != part:
            raise SetupError('Identical text must have the same split; correct the split assignments.')
        # hex digits in IDs can look like phone/card numbers to the PII detector, which makes
        # Scout reject the source; encode IDs with letters only (a–p).
        letters = digest[:32].translate(str.maketrans('0123456789abcdef', 'abcdefghijklmnop'))
        row_id = ''.join('abcdefghijklmnop'[int(c)] for c in f'{i:05d}')
        normalized.append(dict(id='row-'+row_id, group='g-'+letters, kind='triage', text=row['text'], label=row['label'], destination='unknown', split=part))
    train = Counter(r['label'] for r in normalized if r['split'] == 'train')
    validation = [r for r in normalized if r['split'] == 'validation']
    train_named = {label: n for label, n in train.items() if label != 'unknown'}
    if set(train_named) != set(named) or min(train_named.values(), default=0) < 2:
        raise SetupError('Provide at least two training examples for every class; adjust the split or add examples.')
    if len(validation) < 5:
        raise SetupError('Provide at least five validation examples; adjust the split or add examples.')
    return normalized, automatic, len(named), masked_rows


def run(scout, args):
    stream = sys.stderr if args.json else sys.stdout
    print(INTRO.format(mask='Mask protects prompts and tool output by replacing detected personal information.\n' if args.mask_prompts else ''), file=stream, flush=True)
    if not args.yes:
        if not sys.stdin.isatty():
            raise SetupError('Confirmation required: rerun with --yes or confirm in an interactive terminal.')
        print('Enable these local features and continue? [y/N] ', end='', file=stream, flush=True)
        if input().strip().lower() not in ('y', 'yes'):
            raise SetupError('Setup cancelled; no settings were changed.')
    if not ID.fullmatch(args.dataset):
        raise SetupError('Dataset name must use lowercase letters, digits, underscores or hyphens (up to 64).')
    if args.max_error is not None and (not math.isfinite(args.max_error) or not 0 <= args.max_error <= 1):
        raise SetupError('--max-error must be a number from 0 to 1, such as 0.10.')
    rows, automatic, classes, masked_rows = prepare(args.history)
    if masked_rows:
        print(f'{masked_rows} examples contained detected personal information; it was masked locally before saving and training.', file=stream)
    if automatic:
        print('No split provided: a deterministic text-hash split assigns about 80% to training and 20% to validation.', file=stream)
    if args.marker is not None and not 1 <= len(args.marker) <= 256:
        raise SetupError('Marker must contain 1 to 256 characters.')
    source = scout.root() / 'setup-triage' / (args.dataset + '.jsonl')
    # On failure, settings and the previous source copy are restored; calibrations, decisions and models
    # written before the failure stay. On success, previously granted scopes (e.g. mask) are kept.
    settings = scout.root() / 'settings.json'
    previous_settings = settings.read_bytes() if settings.is_file() else None
    previous_source = source.read_bytes() if source.is_file() else None
    previous_grants = scout.profile()['config'].get('grants', {})
    try:
        return _apply(scout, args, rows, automatic, classes, masked_rows, source, previous_grants)
    except BaseException:
        try:
            if previous_settings is None:
                settings.unlink(missing_ok=True)
            else:
                storage.atomic(settings, previous_settings)
            if previous_source is None:
                source.unlink(missing_ok=True)
            else:
                storage.atomic(source, previous_source)
            scout.profiles.pop(str(scout.root()), None)
        except Exception:
            pass
        raise


def _apply(scout, args, rows, automatic, classes, masked_rows, source, previous_grants):
    storage.atomic(source, ''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in rows).encode('utf-8'))
    grants = {scope: list(datasets) for scope, datasets in previous_grants.items() if isinstance(datasets, list)}
    for scope in ('observe','report','train','insights','answer'):
        if args.dataset not in grants.setdefault(scope, []):
            grants[scope].append(args.dataset)
    if args.mask_prompts:
        # Prompt masks are recorded for the profile; the consent text above covers Insights,
        # so Insights can count them (categories and sources only, never text).
        for scope in ('mask', 'insights'):
            if 'profile' not in grants.setdefault(scope, []):
                grants[scope].append('profile')
    if not grants.get('mask'):
        grants.pop('mask', None)
    sources = dict(scout.profile()['config'].get('sources', {}))
    sources[args.dataset] = source
    scout.configure_local('on_demand', grants=grants, sources=sources, answer_task='triage', answer_marker=args.marker)
    try:
        scout.scout_calibrate(args.dataset, .8, 3, 20261105)
        choice = scout.scout_choose('triage', args.recipe, 'User selected the setup triage recipe.')
        if choice['status'] != 'EXECUTABLE' or choice['audit']['verdict'] in ('premise_false', 'against_card_conditions'):
            raise SetupError('This recipe does not meet the dataset requirements; add examples or review the recipe conditions.')
        result = scout.scout_train('triage', args.recipe, args.dataset, max_error=args.max_error)
    except ScoutError as exc:
        code = exc.args[0]
        if code == 'QUALITY_GATE_NOT_MET':
            raise SetupError('No validation threshold met the error limit with at least five accepted examples; add representative labeled data or adjust --max-error.') from None
        if code == 'INSUFFICIENT_DATA':
            raise SetupError('Training needs more usable examples per class and a representative validation split.') from None
        raise SetupError('Setup could not complete (' + str(code) + '); check dataset consent and local configuration.') from None
    return dict(status='READY', examples=len(rows), classes=classes, automatic_split=automatic, masked_examples=masked_rows,
                split_counts=result['split_counts'], threshold=result['threshold'], metrics=result['metrics'],
                validation_curve=result['validation_curve'], threshold_selection=result['threshold_selection'],
                title_risk=scout.title_risk(), next_steps=['python -m plugin.cli --home HOME handle --task triage --text "your message"', 'python -m plugin.cli --home HOME insights'])
