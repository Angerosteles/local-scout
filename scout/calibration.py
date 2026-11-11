"""Offline prefix replay. Only generated IDs, fixed families and numbers leave RAM."""
from collections import Counter
from datetime import datetime
import math
import random
from pathlib import Path
import re

from .calculator import calculate

DEFAULTS = dict(seed=20261105, min_cases=3, family_similarity=.55,
                threshold=.8, components=24, max_features=1024,
                tokens_per_task=1000., price_per_million=10., local_cost=0.,
                setup_cost=0., error_cost=0., currency='PLN')
KINDS = {'triage', 'search', 'fields'}
LABEL = re.compile(r'^[a-z0-9_:.-]{1,64}$')


def parameters(overrides=None):
    p = dict(DEFAULTS)
    if overrides:
        if set(overrides)-set(p):
            raise ValueError
        p.update(overrides)
    for key in ('seed', 'min_cases', 'components', 'max_features'):
        if type(p[key]) is not int or p[key] < 1:
            raise ValueError
    if not (p['seed'] < 2**32 and p['min_cases'] <= 1000 and p['components'] <= 64 and p['max_features'] <= 4096):
        raise ValueError
    for key in ('family_similarity', 'threshold', 'tokens_per_task', 'price_per_million', 'local_cost', 'setup_cost', 'error_cost'):
        if type(p[key]) not in (int, float) or not math.isfinite(p[key]) or p[key] < 0:
            raise ValueError
    if any(p[k] > 1 for k in ('family_similarity', 'threshold')) or p['currency'] not in ('PLN', 'USD', 'EUR'):
        raise ValueError
    return p


def ordered(rows):
    if not rows or len(rows) > 20000:
        raise ValueError
    stamped = ['timestamp' in r for r in rows]
    if any(stamped) and not all(stamped):
        raise ValueError
    def stamp(row):
        value = row['timestamp']
        if isinstance(value, str):
            dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if dt.tzinfo is None:
                raise ValueError
            return dt.timestamp()
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError
        return float(value)
    result = sorted(enumerate(rows), key=lambda pair: (stamp(pair[1]), pair[0])) if all(stamped) else list(enumerate(rows))
    for _, row in result:
        if not isinstance(row.get('text'), str) or len(row['text']) > 20000:
            raise ValueError
    return [r for _, r in result], 'timestamp_stable' if all(stamped) else 'file_order_no_timestamps'


def replay_rows(rows, seed):
    """Seeded stratified round-robin sample, returned in original time order."""
    rows, order = ordered(rows)
    if len(rows) <= 500:
        return rows, order
    rng = random.Random(seed)
    strata = {}
    for i, row in enumerate(rows):
        strata.setdefault((row.get('kind', 'history'), row.get('label', '')), []).append(i)
    keys = sorted(strata)
    rng.shuffle(keys)
    for indices in strata.values():
        rng.shuffle(indices)
    selected = []
    while len(selected) < 500:
        for key in keys:
            if strata[key]:
                selected.append(strata[key].pop())
                if len(selected) == 500:
                    break
    return [rows[i] for i in sorted(selected)], order


def local_encoder(path):
    if not path or not Path(path).is_dir():
        return None
    try:
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer(str(Path(path).resolve()), device='cpu', local_files_only=True, trust_remote_code=False)
    except Exception:
        return None


def prefix_vectors(texts, p, encoder=None):
    import numpy as np
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.decomposition import TruncatedSVD
    from sklearn.preprocessing import normalize
    if encoder is not None:
        vectors = np.asarray(encoder.encode(texts, show_progress_bar=False, normalize_embeddings=True))
        if vectors.ndim != 2 or len(vectors) != len(texts) or not np.isfinite(vectors).all():
            raise ValueError
        return normalize(vectors)
    # Fit on strictly earlier text. Current task is transformed with that vocabulary.
    if len(texts) == 1:
        return np.zeros((1, 2))
    vec = TfidfVectorizer(max_features=p['max_features'], token_pattern=r'(?u)\b\w\w+\b')
    try:
        past = vec.fit_transform(texts[:-1])
    except ValueError:
        return np.zeros((len(texts), 2))
    current = vec.transform(texts[-1:])
    from scipy.sparse import vstack
    both = vstack([past, current])
    if min(past.shape) < 3:
        return normalize(both).toarray()
    svd = TruncatedSVD(n_components=min(p['components'], min(past.shape)-1), random_state=p['seed'])
    svd.fit(past)
    return normalize(svd.transform(both))


def costs(events, p, threshold, setup=None):
    local = [e for e in events if e['noticed'] and e['similarity'] >= threshold and e['neighbor'] is not None]
    labeled = [e for e in local if e['wrong'] is not None]
    wrong = sum(e['wrong'] for e in labeled)
    error_rate = wrong / len(labeled) if labeled else None
    coverage = len(local) / len(events) if events else 0.
    cloud = p['tokens_per_task'] * p['price_per_million'] / 1_000_000
    calc = calculate(dict(n=len(events), cloud=dict(amount=cloud, currency=p['currency']),
        local=p['local_cost'], p_abstain=1-coverage, fallback=cloud,
        setup=p['setup_cost'] if setup is None else setup,
        error_rate=error_rate if error_rate is not None else 0., error_cost=p['error_cost']))
    # Cost of unknown errors cannot be estimated when an error penalty is nonzero.
    unknown = len(local) != len(labeled)
    if unknown and p['error_cost'] > 0:
        calc['net'] = calc['break_even_n'] = None
    return dict(threshold=threshold, count=len(events), accepted=len(local), coverage=coverage,
        errors=wrong if labeled else None, labeled_accepted=len(labeled), accepted_error=error_rate,
        unknown_errors=len(local)-len(labeled), estimated_tokens_saved=len(local)*p['tokens_per_task'],
        estimated_net=calc['net'], break_even_n=calc['break_even_n'], currency=p['currency'])


def replay(rows, overrides=None, model_path=None):
    import numpy as np
    from sklearn.cluster import DBSCAN
    from sklearn.metrics.pairwise import cosine_similarity
    from sklearn.decomposition import PCA
    from threadpoolctl import threadpool_limits
    p = parameters(overrides)
    n_total = len(rows)
    rows, order = replay_rows(rows, p['seed'])
    encoder = local_encoder(model_path)
    events, texts = [], []
    with threadpool_limits(limits=1):
        for pos, row in enumerate(rows):
            texts.append(row['text'])
            v = prefix_vectors(texts, p, encoder)
            sims = np.clip(cosine_similarity(v[-1:], v[:-1])[0], 0, 1) if pos else np.array([])
            # sklearn density grouping on current prefix only; zero vectors stay novel.
            distances = np.clip(1-cosine_similarity(v), 0, 1)
            np.fill_diagonal(distances, 0)
            clusters = DBSCAN(eps=max(1e-9, 1-p['family_similarity']), min_samples=1, metric='precomputed', n_jobs=1).fit_predict(distances)
            linked = [j for j in range(pos) if clusters[j] == clusters[-1] and sims[j] > 0 and sims[j] >= p['family_similarity']]
            best = max(linked, key=lambda j: (sims[j], -j)) if linked else None
            family = events[best]['family'] if best is not None else 'family-%04d' % (pos+1)
            earlier = [j for j, e in enumerate(events) if e['family'] == family]
            near = max(earlier, key=lambda j: (sims[j], -j)) if earlier else None
            similarity = float(sims[near]) if near is not None else 0.
            count = len(earlier)+1
            noticed = count >= p['min_cases']
            accepted = noticed and near is not None and similarity > 0 and similarity >= p['threshold']
            value = row.get('label')
            label = value if isinstance(value,str) and LABEL.fullmatch(value) else None
            previous_label = rows[near].get('label') if near is not None else None
            wrong = int(label != previous_label) if label is not None and isinstance(previous_label,str) and LABEL.fullmatch(previous_label) else None
            events.append(dict(id='task-%06d' % (pos+1), t=pos+1, family=family,
                kind=row.get('kind') if row.get('kind') in KINDS else 'history', prior_cases=len(earlier),
                similarity=similarity, distance=1-similarity, neighbor=events[near]['id'] if near is not None else None,
                noticed=noticed, first_notice=noticed and count == p['min_cases'],
                status='local' if accepted else 'noticed' if noticed else 'remote_novel',
                route='local' if accepted else 'remote', wrong=wrong))
        # Retrospective PCA is presentation only; it never feeds replay or thresholds.
        if len(rows) >= 3 and min(v.shape) >= 2 and np.any(np.var(v, axis=0)):
            xy = PCA(n_components=2, svd_solver='full').fit_transform(v)
        else:
            xy = np.zeros((len(rows), 2))
    curves = [costs(events, p, t) for t in sorted(set([0., .25, .5, .6, .7, .8, .85, .9, .95, 1., p['threshold']]))]
    families = []
    for family in sorted({e['family'] for e in events}):
        subset = [e for e in events if e['family'] == family]
        families.append(dict(id=family, kinds=dict(Counter(e['kind'] for e in subset)),
                             **costs(subset, p, p['threshold'])))
    return dict(version=1, backend='local_encoder' if encoder else 'lsa_tfidf_svd',
        sampled=len(rows) < n_total, n_total=n_total, n_replay=len(rows),
        sampling='seeded_kind_label_round_robin_max500; estimates describe sample only',
        model_fallback=bool(model_path and encoder is None), order=order, parameters=p,
        assumptions=dict(tokens_per_task=p['tokens_per_task'], price_per_million=p['price_per_million'],
            currency=p['currency'], setup_cost=p['setup_cost'], local_cost_per_attempt=p['local_cost'],
            error_cost=p['error_cost'], savings='ESTIMATE; nearest prior label proxy, not trained model accuracy; unknown errors are not zero',
            family_setup='setup charged once globally; per-family standalone setup shown separately',
            token_saving='gross avoided remote tokens; excludes corrections and Scout prompt overhead'),
        protocol='prefix_fit_past_only; DBSCAN current_prefix; immutable historical assignments; PCA retrospective_only',
        events=events, families=families, curves=curves, summary=costs(events, p, p['threshold']),
        map=[dict(id=e['id'], family=e['family'], status=e['status'], x=float(x), y=float(y)) for e, (x,y) in zip(events, xy)])
