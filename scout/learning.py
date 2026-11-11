"""CPU learning; the validation split alone selects acceptance thresholds."""
import time

ALIASES = {'tfidf_logreg': 'logreg', 'conv1d': 'own_cnn'}
THRESHOLDS = (0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
TOKEN = r'(?u)\b[a-zA-Z0-9][a-zA-Z0-9_]+\b'


class Insufficient(Exception):
    pass


class QualityGateNotMet(Insufficient):
    pass


def split(rows):
    if any('split' in r for r in rows):
        if any(r.get('split') not in ('train','validation','test') for r in rows):
            raise Insufficient
        assignments = {}
        for r in rows:
            previous = assignments.setdefault(r['group'],r['split'])
            if previous != r['split']:
                raise Insufficient
        parts = tuple([r for r in rows if r['split']==name] for name in ('train','validation','test'))
        # User data often has no test split (a holdout is kept separately): require train and validation;
        # a missing test split must not silently empty the dataset.
        if not (parts[0] and parts[1]):
            raise Insufficient
        return parts
    groups = sorted({r['group'] for r in rows})
    if len(groups) < 12:
        raise Insufficient
    train_groups, val_groups = set(groups[:8]), set(groups[8:10])
    return ([r for r in rows if r['group'] in train_groups],
            [r for r in rows if r['group'] in val_groups],
            [r for r in rows if r['group'] not in train_groups | val_groups])


def transform(meta, arrays, texts):
    if meta['engine']=='own_cnn':
        import numpy as np
        import re
        vocabulary = {word:i+1 for i,word in enumerate(meta['vocabulary'])}
        x = np.zeros((len(texts),meta['sequence_length']),dtype=np.int64)
        for i,text in enumerate(texts):
            tokens = re.findall(TOKEN,text.lower())[:meta['sequence_length']]
            x[i,:len(tokens)] = [vocabulary.get(word,0) for word in tokens]
        return x
    from sklearn.feature_extraction.text import CountVectorizer
    from sklearn.preprocessing import normalize
    vectorizer = CountVectorizer(vocabulary={word: i for i, word in enumerate(meta['vocabulary'])}, token_pattern=TOKEN)
    counts = vectorizer.transform(texts).astype(float)
    if meta.get('sublinear_tf'):
        import numpy as np
        counts.data = 1 + np.log(counts.data)
    x = normalize(counts.multiply(arrays['idf']), norm='l2').toarray()
    if meta['engine']=='embed_search':
        x = normalize(x @ arrays['svd'].T, norm='l2')
    return x


def cnn_module(nfeatures, nclasses):
    import torch
    # A fixed reviewed network; no generated Python is evaluated.
    class Network(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.embedding = torch.nn.Embedding(nfeatures+1,24,padding_idx=0)
            self.conv = torch.nn.Conv1d(24,32,3,padding=1)
            self.head = torch.nn.Linear(32,nclasses)
        def forward(self, x):
            return self.head(torch.relu(self.conv(self.embedding(x).transpose(1,2))).max(dim=2).values)
    return Network()


def raw_scores(meta, arrays, x):
    import numpy as np
    from sklearn.metrics.pairwise import cosine_similarity
    family = meta['engine']
    if family in ('tfidf_search','embed_search'):
        score = cosine_similarity(x, arrays['train'])
        best = score.argmax(axis=1)
        return best, score.max(axis=1)
    if family == 'knn':
        similarities = cosine_similarity(x, arrays['train'])
        k = min(meta['neighbors'],len(arrays['train']))
        neighbors = np.argsort(-similarities,axis=1,kind='stable')[:,:k]
        probs = np.zeros((len(x),len(meta['classes'])))
        for i,near in enumerate(neighbors):
            weights = np.maximum(similarities[i,near],0)
            for j,w in zip(near,weights):
                probs[i,arrays['labels'][j]] += w
            if probs[i].sum():
                probs[i] /= probs[i].sum()
        # Acceptance is based on nearest cosine distance calibrated on validation.
        return probs.argmax(axis=1), similarities.max(axis=1)
    if family == 'logreg':
        logits = x @ arrays['coef'].T + arrays['intercept']
        if logits.shape[1] == 1:
            pos = 1/(1+np.exp(-np.clip(logits[:, 0], -700, 700)))
            probs = np.column_stack([1-pos, pos])
        else:
            logits -= logits.max(axis=1, keepdims=True)
            probs = np.exp(logits)
            probs /= probs.sum(axis=1, keepdims=True)
    elif family == 'trees':
        probs = np.zeros((len(x), len(meta['classes'])))
        for tree_index, count in enumerate(meta['forest_sizes']):
            prefix=f'forest_{tree_index}_'
            left=arrays[prefix+'left'];right=arrays[prefix+'right']
            feature=arrays[prefix+'feature'];threshold=arrays[prefix+'threshold']
            values_array=arrays[prefix+'value']
            for i, row in enumerate(x):
                node = 0
                while left[node] != -1:
                    node = left[node] if row[feature[node]] <= threshold[node] else right[node]
                values = values_array[node]
                probs[i] += values / values.sum()
        probs /= len(meta['forest_sizes'])
    elif family == 'own_cnn':
        import torch
        torch.set_num_threads(1)
        model = cnn_module(len(meta['vocabulary']), len(meta['classes']))
        model.load_state_dict({k: torch.tensor(arrays['cnn_' + k]) for k in model.state_dict()})
        model.eval()
        with torch.no_grad():
            probs = torch.softmax(model(torch.tensor(x, dtype=torch.long)), dim=1).numpy()
    else:
        raise ValueError
    return probs.argmax(axis=1), probs.max(axis=1)


def predictions(meta, arrays, rows):
    import numpy as np
    if meta['engine'] == 'basal_zero_shot':
        from . import basal
        decided = basal.decide(meta['basal_url'], [r['text'] for r in rows], meta['classes'], meta.get('descriptions'))
        return [(i or 0, float(s), i is not None and s >= meta['threshold']) for i, s in decided]
    x = transform(meta, arrays, [r['text'] for r in rows])
    best, scores = raw_scores(meta, arrays, x)
    return [(int(i), float(s), bool(np.any(v) and s >= meta['threshold'])) for i,s,v in zip(best,scores,x)]


def metrics(meta, arrays, rows, pred=None):
    pred = predictions(meta, arrays, rows) if pred is None else pred
    accepted = sum(a for _,_,a in pred)
    predicted = [meta['classes'][int(arrays['labels'][i])] if meta['engine'] in ('tfidf_search','embed_search') else meta['classes'][i]
                 for i,_,_ in pred]
    correct = sum(a and label == row['label'] for (_,_,a),label,row in zip(pred,predicted,rows))
    unknown = [row['label']=='unknown' for row in rows]
    refused_unknown = sum(is_unknown and not item[2] for is_unknown,item in zip(unknown,pred))
    accepted_unknown = sum(is_unknown and item[2] for is_unknown,item in zip(unknown,pred))
    refused_known = sum(not is_unknown and not item[2] for is_unknown,item in zip(unknown,pred))
    return dict(accuracy=correct/len(rows), coverage=accepted/len(rows),
                accepted_error=(accepted-correct)/accepted if accepted else None, n_test=len(rows),
                unknown_count=sum(unknown), unknown_rate=sum(unknown)/len(rows),
                correct_unknown_refusals=refused_unknown, false_acceptances_unknown=accepted_unknown,
                refused_known=refused_known)


def select_threshold(meta, classes, va, best, scores, valid, max_error=None):
    """Validation curve and the lowest threshold that passes the quality gate (sets meta, or raises).

    ``valid`` is a boolean mask over ``va``: rows a method may accept at all (non-empty features, or a basal
    answer other than ``other``)."""
    best_gate = None
    max_accepted = 0
    curve = []
    grid = sorted(set(THRESHOLDS + tuple(round(i / 100, 2) for i in range(30, 100, 5))))
    for threshold in grid:
        accepted = (scores >= threshold) & valid
        count = int(accepted.sum())
        max_accepted = max(max_accepted, count)
        wrong = sum(bool(a) and classes[int(i)] != row['label'] for a,i,row in zip(accepted,best,va))
        error = wrong/count if count else None
        curve.append(dict(threshold=threshold, coverage=count/len(va), accepted=count, accepted_error=error))
        if count >= 5 and (best_gate is None or error < best_gate[0]):
            best_gate = (error, count, threshold)
    # Preserve the original default selection grid and quality gate.
    selection_grid = grid if max_error is not None else THRESHOLDS
    limit = max_error if max_error is not None else 0.1
    for point in curve:
        if point['threshold'] in selection_grid and point['accepted'] >= 5 and point['accepted_error'] <= limit:
            meta['threshold'] = point['threshold']
            break
    if meta['threshold'] is None:
        raise QualityGateNotMet(best_gate, len(va), max_accepted)
    meta['validation_curve'] = curve
    meta['threshold_selection'] = dict(source='user_max_error' if max_error is not None else 'default_validation_gate', max_error=limit, minimum_accepted=5)


def train_basal(rows, max_error=None, basal_url=None, descriptions=None):
    """Zero-shot triage through the local basal engine: no fitting, threshold from validation only."""
    import numpy as np
    from . import basal
    started = time.monotonic()
    if not basal_url:
        raise ValueError('basal_url is not configured')
    tr, va, te = split(rows)
    classes = sorted({r['label'] for r in tr if r['label']!='unknown'})
    if len(classes) < 2 or not {r['label'] for r in tr+va if r['label']!='unknown'} <= set(classes) or not va:
        raise Insufficient
    if basal.OTHER in classes:
        raise basal.ReservedLabel(f'the class id {basal.OTHER!r} is reserved for the basal refusal option; rename that class')
    descriptions = {k: v for k, v in basal.check_descriptions(descriptions).items() if k in classes}
    meta = dict(engine='basal_zero_shot', classes=classes,
                dataset_labels=sorted({r['label'] for r in rows if r['label']!='unknown'}), candidate_id='triage',
                corpus_mode=False, descriptions=descriptions,
                document_representation='masked text sent to the local basal engine (loopback only)',
                artifact_disclosure='labels and category descriptions only; no training text is stored',
                threshold=None, threshold_source='validation', split_counts=dict(train=len(tr), validation=len(va), test=len(te)))
    decided = basal.decide(basal_url, [r['text'] for r in va], classes, descriptions)
    best = np.array([i or 0 for i, _ in decided], dtype=np.int64)
    scores = np.array([s for _, s in decided], dtype=float)
    valid = np.array([i is not None for i, _ in decided], dtype=bool)
    select_threshold(meta, classes, va, best, scores, valid, max_error)
    pred = [(int(i), float(s), bool(v and s >= meta['threshold'])) for i, s, v in zip(best, scores, valid)]
    meta['metrics'] = metrics(meta, {}, va, pred=pred)
    meta['metrics_scope'] = 'validation; n_test is legacy count key'
    meta['elapsed_seconds'] = time.monotonic()-started
    return meta, {'class_count': np.array([len(classes)], dtype=np.int64)}


def train(rows, recipe, max_error=None, basal_url=None, descriptions=None):
    import numpy as np
    from sklearn.feature_extraction.text import TfidfVectorizer
    from threadpoolctl import threadpool_limits
    recipe = ALIASES.get(recipe, recipe)
    if recipe == 'basal_zero_shot':
        return train_basal(rows, max_error, basal_url, descriptions)
    started = time.monotonic()
    doc_rows = [r for r in rows if r['kind']=='doc']
    corpus_search = bool(doc_rows)
    if corpus_search:
        queries = [r for r in rows if r['kind']=='search']
        tr, va, te = split(queries)
        indexed = sorted(doc_rows, key=lambda r:r['id'])
        classes = [r['id'] for r in indexed]
        if len(classes)<2 or len(classes)>20_000 or any(r['label']!='unknown' and r['label'] not in classes for r in tr+va+te):
            raise Insufficient
        fit_rows = indexed
        y = np.arange(len(indexed),dtype=np.int64)
    else:
        tr, va, te = split(rows)
        classes = sorted({r['label'] for r in tr if r['label']!='unknown'})
        if len(classes) < 2 or not {r['label'] for r in tr+va if r['label']!='unknown'} <= set(classes):
            raise Insufficient
        fit_rows = [r for r in tr if r['label']!='unknown']
        y = np.array([classes.index(r['label']) for r in fit_rows])
    vectorizer = TfidfVectorizer(token_pattern=TOKEN, max_features=4096, sublinear_tf=corpus_search)
    with threadpool_limits(limits=1):
        try:
            x = vectorizer.fit_transform([(r.get('title','') + ' ' + r['text'][:2000]) if corpus_search else r['text'] for r in fit_rows]).toarray()
        except ValueError:
            raise Insufficient from None
        dataset_labels = sorted({r['label'] for r in rows if r['label']!='unknown'})
        meta = dict(engine=recipe, vocabulary=vectorizer.get_feature_names_out().tolist(), classes=classes,
                    dataset_labels=dataset_labels, candidate_id='search' if recipe in ('tfidf_search','embed_search') else 'triage',
                    corpus_mode=corpus_search, sublinear_tf=corpus_search,
                    document_representation='title + first 2000 characters' if corpus_search else 'full training text',
                    artifact_disclosure='local vocabulary contains training terms; never included in Insights export',
                    threshold=None, threshold_source='validation', split_counts=dict(train=len(tr), validation=len(va), test=len(te)))
        arrays = {'idf': vectorizer.idf_}
        if recipe in ('tfidf_search', 'embed_search', 'knn'):
            if recipe=='embed_search':
                from sklearn.decomposition import TruncatedSVD
                from sklearn.preprocessing import normalize
                if min(x.shape)<3:
                    raise Insufficient
                svd=TruncatedSVD(n_components=min(32,min(x.shape)-1),random_state=20261105)
                x=normalize(svd.fit_transform(x),norm='l2')
                arrays['svd']=svd.components_
            arrays.update(train=x, labels=y)
            if recipe=='knn':
                meta['neighbors']=5
            if recipe in ('tfidf_search','embed_search'):
                meta['documents'] = ([dict(id=r['id'], title=r.get('title',r['id'])) for r in indexed]
                                     if corpus_search else [dict(id=r['id'], title=r['label']) for r in fit_rows])
                # Index returns sources, never document bodies.
                meta['fallback'] = 'TF-IDF + SVD (LSA); no pretrained embedding weights; cosine' if recipe=='embed_search' else 'TF-IDF vectors; no embedding weights; cosine'
        elif recipe == 'logreg':
            from sklearn.linear_model import LogisticRegression
            model = LogisticRegression(C=10, max_iter=500, random_state=20261105).fit(x,y)
            arrays.update(coef=model.coef_, intercept=model.intercept_)
        elif recipe == 'trees':
            from sklearn.ensemble import RandomForestClassifier
            model = RandomForestClassifier(n_estimators=24, random_state=20261105, n_jobs=1).fit(x,y)
            meta['forest_sizes'] = []
            for i,t in enumerate(model.estimators_):
                tree=t.tree_;prefix=f'forest_{i}_'
                arrays[prefix+'left']=tree.children_left.astype(np.int32)
                arrays[prefix+'right']=tree.children_right.astype(np.int32)
                arrays[prefix+'feature']=tree.feature.astype(np.int32)
                arrays[prefix+'threshold']=tree.threshold.astype(np.float32)
                arrays[prefix+'value']=tree.value[:,0,:].astype(np.float32)
                meta['forest_sizes'].append(int(tree.node_count))
            meta['feature_importance'] = [dict(feature_id=int(i), importance=float(model.feature_importances_[i]))
                                          for i in np.argsort(model.feature_importances_)[-10:][::-1]]
        elif recipe == 'own_cnn':
            import torch
            torch.set_num_threads(1)
            torch.manual_seed(20261105)
            meta['sequence_length']=64
            model = cnn_module(len(meta['vocabulary']), len(classes))
            optimizer = torch.optim.Adam(model.parameters(), lr=0.03)
            features = torch.tensor(transform(meta,arrays,[r['text'] for r in fit_rows]),dtype=torch.long)
            labels = torch.tensor(y,dtype=torch.long)
            for _ in range(40):
                optimizer.zero_grad()
                loss = torch.nn.functional.cross_entropy(model(features), labels)
                loss.backward()
                optimizer.step()
            arrays.update({'cnn_' + k: v.detach().numpy() for k,v in model.state_dict().items()})
        else:
            raise ValueError
        # An index has no classifier metrics or published classifier threshold.
        if recipe in ('tfidf_search','embed_search') and not corpus_search:
            meta.update(threshold=0.0, threshold_source='nonzero cosine retrieval', metrics=None)
        else:
            xv = transform(meta, arrays, [r['text'] for r in va])
            best, scores = raw_scores(meta, arrays, xv)
            select_threshold(meta, classes, va, best, scores, np.any(xv, axis=1), max_error)
            if recipe == 'knn':
                meta['max_distance'] = 1-meta['threshold']
            meta['metrics'] = metrics(meta, arrays, va)
            meta['metrics_scope'] = 'validation; n_test is legacy count key'
    meta['elapsed_seconds'] = time.monotonic()-started
    return meta, arrays


def predict(meta, arrays, row):
    result = dict(decision='unknown', label=None, document_id=None, title=None,
                  score=None, threshold=None if meta['engine'] in ('tfidf_search','embed_search') else meta['threshold'], matched_fields=None)
    i, score, accept = predictions(meta, arrays, [row])[0]
    result['score'] = score
    if not accept:
        return result
    result['decision'] = 'accepted'
    if meta['engine'] in ('tfidf_search','embed_search'):
        result.update(document_id=meta['documents'][i]['id'], title=meta['documents'][i]['title'], threshold=None)
        import numpy as np
        from sklearn.metrics.pairwise import cosine_similarity
        x = transform(meta, arrays, [row['text']])
        sims = cosine_similarity(x, arrays['train'])[0]
        result['top_k'] = [dict(document_id=meta['documents'][int(j)]['id'], title=meta['documents'][int(j)]['title'], score=float(sims[j]))
                           for j in np.argsort(-sims, kind='stable')[:3] if sims[j] > 0]
        result['source'] = 'approved_synthetic_corpus'
    else:
        result['label'] = meta['classes'][i]
    return result
