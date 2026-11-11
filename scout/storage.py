"""Bounded JSON/NPZ storage. No pickle, joblib or executable deserialization."""
import hashlib
import io
import json
import fcntl
import math
import os
from pathlib import Path
import time
import uuid
import zipfile

MAX_BYTES = 8 * 1024 * 1024


def digest(data):
    # SHA-256 encoded as a..p nibbles avoids digit runs resembling national IDs.
    return hashlib.sha256(data).hexdigest().translate(str.maketrans('0123456789abcdef', 'abcdefghijklmnop'))


def atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.' + uuid.uuid4().hex + '.pending')
    try:
        with temp.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def read_json(path):
    if path.is_symlink() or path.stat().st_size > MAX_BYTES:
        raise ValueError
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, value):
    data = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    if len(data) > MAX_BYTES:
        raise ValueError
    path = Path(path)
    if path.name == 'aggregates.json' and isinstance(value, dict) and isinstance(value.get('events'), list):
        write_aggregates(path, value['events'])
        return
    atomic(path, data)


def write_aggregates(path, incoming, now=None):
    """Merge cached event snapshots under a profile-wide lock and prune on every write."""
    now = time.time() if now is None else now
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name('.aggregates.lock')
    if path.is_symlink() or lock_path.is_symlink():
        raise ValueError
    flags = os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(lock_path, flags, 0o600)
    try:
        with os.fdopen(fd, 'r+b', closefd=True) as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                try:
                    current = read_json(path).get('events', [])
                except (OSError, ValueError, TypeError, AttributeError):
                    current = []
                config = {}
                try:
                    config = read_json(path.with_name('settings.json'))
                except (OSError, ValueError, TypeError):
                    pass
                retention = config.get('retention_days', 30)
                if type(retention) is not int or not 1 <= retention <= 30:
                    retention = 30
                cutoff = now - retention * 86400

                def valid(events):
                    return [event for event in events if isinstance(event, dict) and
                            type(event.get('timestamp')) in (int, float) and
                            math.isfinite(event['timestamp']) and event['timestamp'] >= cutoff]

                # A cache writes a snapshot, not a delta. Multiset union keeps genuine repeated
                # events while preventing a stale cache from duplicating the disk snapshot.
                current_counts = {}
                incoming_counts = {}
                encoded = {}
                for event in valid(current):
                    key = json.dumps(event, sort_keys=True, separators=(',', ':'), allow_nan=False)
                    current_counts[key] = current_counts.get(key, 0) + 1
                    encoded[key] = event
                for event in valid(incoming):
                    key = json.dumps(event, sort_keys=True, separators=(',', ':'), allow_nan=False)
                    incoming_counts[key] = incoming_counts.get(key, 0) + 1
                    encoded[key] = event
                merged = []
                for key in set(current_counts) | set(incoming_counts):
                    merged.extend([encoded[key]] * max(current_counts.get(key, 0), incoming_counts.get(key, 0)))
                merged.sort(key=lambda event: event['timestamp'])
                merged = merged[-10000:]
                encoded_data = json.dumps({'events': merged}, sort_keys=True, ensure_ascii=False,
                                          allow_nan=False).encode()
                while len(encoded_data) > MAX_BYTES and merged:
                    merged.pop(0)
                    encoded_data = json.dumps({'events': merged}, sort_keys=True, ensure_ascii=False,
                                              allow_nan=False).encode()
                atomic(path, encoded_data)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    except Exception:
        # If fdopen failed before owning the descriptor, close it here.
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def save_artifact(root, meta, arrays):
    import numpy as np
    buffer = io.BytesIO()
    np.savez_compressed(buffer, **arrays)
    blob = buffer.getvalue()
    if len(blob) > MAX_BYTES:
        raise ValueError
    aid = 'model-' + uuid.uuid4().hex.translate(str.maketrans('0123456789abcdef', 'abcdefghijklmnop'))
    meta = dict(meta, format=1, array_sha256=digest(blob))
    encoded = json.dumps(meta, sort_keys=True, allow_nan=False).encode()
    # A final descriptor is the commit point; incomplete files never resolve as artifacts.
    atomic(root / (aid + '.npz'), blob)
    try:
        write_json(root / (aid + '.json'), {'metadata': meta, 'sha256': digest(encoded)})
    except Exception:
        (root / (aid + '.npz')).unlink(missing_ok=True)
        raise
    return aid


def load_artifact(root, aid):
    import numpy as np
    descriptor = read_json(root / (aid + '.json'))
    meta = descriptor['metadata']
    encoded = json.dumps(meta, sort_keys=True, allow_nan=False).encode()
    if meta['format'] != 1 or digest(encoded) != descriptor['sha256']:
        raise ValueError
    path = root / (aid + '.npz')
    if path.is_symlink() or path.stat().st_size > MAX_BYTES:
        raise ValueError
    blob = path.read_bytes()
    if digest(blob) != meta['array_sha256']:
        raise ValueError
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        if sum(item.file_size for item in archive.infolist()) > 32 * MAX_BYTES:
            raise ValueError
    with np.load(io.BytesIO(blob), allow_pickle=False) as saved:
        arrays = {key: saved[key] for key in saved.files}
    if any(a.dtype.kind not in 'fiu' or not np.isfinite(a).all() for a in arrays.values()):
        raise ValueError
    return meta, arrays


def validate_model(meta, arrays):
    import math
    import re
    import numpy as np
    safe_id=re.compile(r'^[a-z0-9_-]{1,64}$')
    safe_label=re.compile(r'^[a-z0-9_:.-]{1,64}$')
    if meta.get('engine') == 'basal_zero_shot':
        return _validate_basal(meta, arrays)
    engines={'logreg','knn','trees','own_cnn','tfidf_search','embed_search'}
    if meta.get('engine') not in engines or not safe_id.fullmatch(meta['dataset_id']) or meta['candidate_id'] not in ('triage','search'):
        raise ValueError
    vocab=meta['vocabulary'];classes=meta['classes'];n=len(vocab);c=len(classes)
    if not 1<=n<=4096 or len(set(vocab))!=n or any(not isinstance(w,str) or not re.fullmatch(r'[a-zA-Z0-9_]+',w) for w in vocab):
        raise ValueError
    corpus_mode=meta.get('candidate_id')=='search' and meta.get('corpus_mode') is True
    label_limit=20_000 if corpus_mode else 256
    class_limit=20_000 if corpus_mode else 256
    label_set=meta.get('dataset_labels')
    if (not isinstance(label_set,list) or not 2<=len(label_set)<=label_limit or len(set(label_set))!=len(label_set) or
            any(not isinstance(label,str) or not safe_label.fullmatch(label) for label in label_set) or
            not 2<=c<=class_limit or len(set(classes))!=c or any(not isinstance(label,str) or not safe_label.fullmatch(label) for label in classes) or
            not set(classes)<=set(label_set)):
        raise ValueError
    if arrays['idf'].shape!=(n,) or not (arrays['idf']>0).all() or not isinstance(meta['threshold'],(int,float)) or not math.isfinite(meta['threshold']) or not 0<=meta['threshold']<=1:
        raise ValueError
    engine=meta['engine']
    if engine=='logreg':
        out=1 if c==2 else c
        if arrays['coef'].shape!=(out,n) or arrays['intercept'].shape!=(out,): raise ValueError
    if engine=='embed_search':
        if arrays['svd'].ndim!=2 or arrays['svd'].shape[1]!=n or not 1<=arrays['svd'].shape[0]<=32: raise ValueError
    if engine=='knn' and (type(meta.get('neighbors')) is not int or not 1<=meta['neighbors']<=25): raise ValueError
    if engine in ('knn','tfidf_search','embed_search'):
        x=arrays['train'];labels=arrays['labels']
        if x.ndim!=2 or x.shape[1]!=(arrays['svd'].shape[0] if engine=='embed_search' else n) or not 1<=len(x)<=20000 or labels.shape!=(len(x),) or labels.dtype.kind not in 'iu' or not ((labels>=0)&(labels<c)).all(): raise ValueError
    if engine in ('tfidf_search','embed_search'):
        from .privacy import categories
        documents=meta['documents']
        if len(documents)!=len(arrays['train']): raise ValueError
        for d in documents:
            if (not isinstance(d,dict) or set(d)!={'id','title'} or not isinstance(d['id'],str) or
                    not safe_id.fullmatch(d['id']) or not isinstance(d['title'],str) or not 1<=len(d['title'])<=512 or categories(d['title'])):
                raise ValueError
            if meta.get('corpus_mode'):
                if d['id'] not in label_set: raise ValueError
            elif d['title'] not in classes:
                raise ValueError
    if engine=='own_cnn':
        if meta.get('sequence_length')!=64: raise ValueError
        shapes={'cnn_embedding.weight':(n+1,24),'cnn_conv.weight':(32,24,3),'cnn_conv.bias':(32,), 'cnn_head.weight':(c,32),'cnn_head.bias':(c,)}
        if any(arrays[k].shape!=shape for k,shape in shapes.items()): raise ValueError
    if engine=='trees':
        sizes=meta.get('forest_sizes')
        if not isinstance(sizes,list) or not 1<=len(sizes)<=64: raise ValueError
        for tree_index,count in enumerate(sizes):
            if type(count) is not int or not 1<=count<=39999: raise ValueError
            prefix=f'forest_{tree_index}_'
            left=arrays[prefix+'left'];right=arrays[prefix+'right']
            feature=arrays[prefix+'feature'];threshold=arrays[prefix+'threshold']
            values_array=arrays[prefix+'value']
            if any(array.shape!=(count,) for array in (left,right,feature,threshold)) or values_array.shape!=(count,c):
                raise ValueError
            for i in range(count):
                lchild,rchild=left[i],right[i]
                if lchild==-1 and rchild==-1: pass
                elif not (i<lchild<count and i<rchild<count and 0<=feature[i]<n): raise ValueError
                values=values_array[i]
                if values.shape!=(c,) or not np.isfinite(values).all() or (values<0).any() or values.sum()<=0: raise ValueError
                if not math.isfinite(threshold[i]): raise ValueError


def _validate_basal(meta, arrays):
    """A basal artifact holds labels, optional descriptions and a validation threshold; nothing learned."""
    import math
    import re
    from .basal import OTHER, check_descriptions
    safe_id = re.compile(r'^[a-z0-9_-]{1,64}$')
    safe_label = re.compile(r'^[a-z0-9_:.-]{1,64}$')
    classes, labels = meta.get('classes'), meta.get('dataset_labels')
    if (not isinstance(meta.get('dataset_id'), str) or not safe_id.fullmatch(meta['dataset_id'])
            or meta.get('candidate_id') != 'triage' or meta.get('corpus_mode') or 'basal_url' in meta
            or not isinstance(classes, list) or not 2 <= len(classes) <= 256 or len(set(classes)) != len(classes)
            or not isinstance(labels, list) or not set(classes) <= set(labels) or len(labels) > 256
            or any(not isinstance(x, str) or not safe_label.fullmatch(x) for x in classes + labels)):
        raise ValueError
    threshold = meta.get('threshold')
    if type(threshold) not in (int, float) or not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError
    if not set(check_descriptions(meta.get('descriptions'))) <= set(classes) or OTHER in classes:
        raise ValueError
    if set(arrays) != {'class_count'} or arrays['class_count'].tolist() != [len(classes)]:
        raise ValueError
