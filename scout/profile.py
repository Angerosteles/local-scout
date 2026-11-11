"""Decision facts computed exclusively on train and validation, never test content."""
from collections import Counter
import re
from .learning import split, Insufficient, TOKEN
from .privacy import fields

GUIDE = ("Choose independently from the full catalog without a ranking. Compare train/validation "
         "profiles with requires. Give verifiable premises such as label_count>=2; do not quote data. "
         "A missing fact is unverified. Record scout_choose, then call scout_train separately for a "
         "compatible EXECUTABLE card requiring training: choosing alone creates no artifact. Match the "
         "card task to the family goal and read next_step. Check validation and reconsider after failure. "
         "Only the operator may access the final holdout after all decisions are frozen. Calibration "
         "provides counts and proxy errors, not a recommendation or a measure of recipe quality.")


def facts(rows, kind):
    try:
        train, validation, _ = split(rows)
    except Insufficient:
        return dict(profile_scope='train+validation', profile_status='insufficient_data')
    return from_parts(train, validation, kind)


def from_parts(train, validation, kind):
    """Facts for a family subset, preserving the original train/validation assignments."""
    decision = train + validation
    labels = Counter()
    for row in train:
        if row['label']=='unknown':
            continue
        if kind=='fields' and 'entity_types' in row:
            labels.update(row['entity_types'] or [row['label']])
        else:
            labels[row['label']]+=1
    tokens = [re.findall(TOKEN, r['text'].lower()) for r in decision]
    texts = [r['text'] for r in decision]
    unknown_count=sum(r['label']=='unknown' for r in decision)
    def detected(row):
        markers={name.lower() for name in re.findall(r'\[([A-Za-z0-9][A-Za-z0-9_:.-]{0,63})\]',row['text'])}
        found=set(row.get('_detected_types',())) | markers
        if 'entity_types' not in row:
            found |= set(fields(row['text']))
        return sorted(found)
    return dict(profile_scope='train+validation', profile_status='measured' if train and validation else 'partial_family', records=len(decision),
                groups=len({r['group'] for r in decision}), train_records=len(train),
                validation_records=len(validation), labels_present=bool(labels), label_count=len(labels),
                min_examples_per_label_train=min(labels.values(), default=0),
                unknown_examples=unknown_count, unknown_rate=unknown_count/len(decision) if decision else 0,
                label_balance_train=min(labels.values()) / max(labels.values()) if labels else None,
                mean_tokens=sum(map(len, tokens))/len(tokens) if tokens else 0,
                vocab_train=len({t for r in train for t in re.findall(TOKEN, r['text'].lower())}),
                duplicate_text_ratio=1-len(set(texts))/len(texts) if texts else 0,
                rule_match_ratio=sum(bool(detected(r)) for r in decision)/len(decision) if decision else 0,
                rule_label_agreement=sum(r['label'] in detected(r) for r in decision)/len(decision) if decision else 0,
                modality='text', goal={'triage':'classification','search':'search','fields':'extraction'}[kind])
