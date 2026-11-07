"""Isolated decline research: fit/select on 2024, freeze, then evaluate once.

The response is a spectral change, never disease or usefulness of an inspection.
Existing mean forecasts, detectors, intervals and journals are read-only.
"""
from datetime import datetime, timezone
from pathlib import Path
import numpy as np

from .farm_context_data import ROOT, read, dump, digest
from .field_decline_experiment import chronological_folds, confusion, events
from .field_index_experiment import date_weights, predict as point_predict

D = ROOT / 'data/farm-research/conservative-decline'
SOURCE = ROOT / 'data/farm-research/crop-season-model/data-2024.json'
LEGACY = ROOT / 'data/farm-research/field-index-model/frozen-model.json'
HOLDOUT = ['R23', 'R24', 'R25', 'R26', 'R27']
NAMES = ['ndvi', 'ndmi']


def key(r):
    return r['fid'], r['issue'], r['target']


def only2024(rows):
    if not rows or any(r['issue'][:4] != '2024' or r['target'][:4] != '2024' for r in rows):
        raise ValueError('Fit and selection accept nonempty 2024 data only')
    if any(r['fid'] in HOLDOUT for r in rows):
        raise ValueError('Reserved spatial fragments cannot fit or select')
    if len({key(r) for r in rows}) != len(rows):
        raise ValueError('Duplicate cohort rows')


def protocol():
    if (D / 'protocol.json').exists():
        return read(D / 'protocol.json')
    candidates = [dict(id='past_momentum', family='momentum', features='basic')]
    for feature_set in ['basic', 'history_season']:
        for c in [.1, 1.]:
            candidates.append(dict(id=f'{feature_set}_logit_{c}', family='logit', features=feature_set, c=c))
        for leaf in [10, 25]:
            candidates.append(dict(id=f'{feature_set}_extra_{leaf}', family='extra', features=feature_set, leaf=leaf))
        for q in [.5, .9]:
            candidates.append(dict(id=f'{feature_set}_quantile_{q}', family='quantile', features=feature_set, q=q))
    p = dict(schema=1, created_at_utc=datetime.now(timezone.utc).isoformat(),
        source_2024_sha256=digest(SOURCE), legacy_sha256=digest(LEGACY), holdout_ids=HOLDOUT,
        target='future core median minus current core median <= -0.10; same baseline, 6-8 days; no damage label',
        candidates=candidates, gates=['none', 'joint_momentum'],
        gate_definition='joint_momentum: both previous slopes *7 <= -0.05, current NDVI>=0.40 / NDMI>=0.00',
        probability_thresholds=[.5, .6, .7, .8, .85, .9, .925, .95, .975, .99, 1.01],
        decline_thresholds=[.10, .125, .15, .175, .20, .25, .30, .40, .50, 2.01],
        folds=['2024-06-01', '2024-07-01', '2024-08-01', '2024-09-01', '2024-10-01'],
        fold_rule='fit target < month start; validation issue in month, target < next month; crossing pairs purged',
        feature_policy='basic: seven existing inputs; history_season: 23 past/history/pixel inputs plus 17 past-only seasonal proxies. No calendar crop guesses, field ID, future target/date/clouds or audit fields.',
        weights='each issue date equal total weight; all fragments of a date stay in same fold',
        fixed_hyperparameters=dict(extra_trees=256, extra_max_depth=4, cat_iterations=160,
            cat_depth=2, cat_learning_rate=.04, cat_l2=10, seed=20241020, threads=2),
        selection='per index: zero false-positive pairs and dates (persistence budget), >=2TP on >=2 issue dates. Maximize date-balanced recall, then TP dates, TP count, prefer candidate order, ungated, higher threshold.',
        diagnostic='Also freeze best zero-FP operating point with >=1TP even if insufficient event dates. Evaluate as rejected diagnostic, never eligible through 2026 results.',
        final_fit='all eligible 2024 nonreserved pairs, March-October; exact same algorithm; no early stopping or test-driven calibration',
        comparators=['persistence: current indices unchanged, zero alarms', 'original frozen Ridge alarms',
            'previous rejected direct classifier: frozen 2024 settings; descriptive comparator only'],
        acceptance='Per index require more TP and no growth of FP pairs or FP dates versus original Ridge AND persistence, on all2026 and spatial2026. Cannot claim superiority to zero-alarm persistence when any FP occurs.',
        test='Only frozen choice and predeclared diagnostic on existing 2026 and reserved 2024; 2025 absent, not fabricated. Previously examined 2026 is not pristine blind.',
        deployment='research only; operational model, point forecast, intervals and journals never changed',
        availability='Retrospective acquisition-date experiment; historical delivery latency and archive availability unproven',
        caveats=['2024 reused for selection across previous experiments; selection scores optimistic.',
            'Nearby fragments and overlapping windows dependent; reserved fragments not external farms.',
            'Scores are not calibrated agronomic probabilities; crop and operations unknown.'])
    D.mkdir(parents=True, exist_ok=True)
    dump(D / 'protocol.json', p)
    protected = {}
    for folder in ['models', 'data/field', 'web/scouting', 'data/farm-research']:
        for path in (ROOT / folder).rglob('*'):
            if not path.is_file() or D in path.parents:
                continue
            if folder == 'data/farm-research' and path.suffix not in ['.json', '.geojson', '.cbm', '.csv']:
                continue
            protected[str(path.relative_to(ROOT))] = digest(path)
    dump(D / 'preservation-before.json', protected)
    return p


def matrix(rows, spec):
    # Explicit whitelist: changing y/audit/target/fid cannot change any input.
    if spec['features'] == 'basic':
        result = [r['x'][:7] for r in rows]
    else:
        result = [r['features'][:23] + r['season'][:17] for r in rows]
    x = np.asarray(result, dtype=float)
    if not np.isfinite(x).all():
        raise ValueError('Nonfinite input')
    return x


def gate(rows, j, name):
    if name == 'none':
        return np.ones(len(rows), bool)
    if name != 'joint_momentum':
        raise ValueError('Unknown gate')
    return np.array([r['x'][2]*7 <= -.05 and r['x'][3]*7 <= -.05
                     and r['x'][j] >= (.4 if j == 0 else 0.) for r in rows])


def train(rows, j, spec, p):
    only2024(rows)
    if spec['family'] == 'momentum':
        return dict(kind='momentum')
    x = matrix(rows, spec)
    y = events(rows, j).astype(int)
    w = date_weights(rows)
    w *= len(rows)/w.sum()
    if spec['family'] != 'quantile' and len(np.unique(y)) < 2:
        return dict(kind='constant', value=float((w@y+.5)/(w.sum()+1)))
    hp = p['fixed_hyperparameters']
    if spec['family'] == 'logit':
        from sklearn.linear_model import LogisticRegression
        # Weighted scaling, learned inside each fit fold only.
        mean = np.average(x, axis=0, weights=w)
        scale = np.sqrt(np.average((x-mean)**2, axis=0, weights=w))
        scale[scale < 1e-9] = 1
        m = LogisticRegression(C=spec['c'], solver='lbfgs', max_iter=2000,
                               random_state=hp['seed'])
        m.fit((x-mean)/scale, y, sample_weight=w)
        return dict(kind='logit', model=m, mean=mean, scale=scale)
    if spec['family'] == 'extra':
        from sklearn.ensemble import ExtraTreesClassifier
        m = ExtraTreesClassifier(n_estimators=hp['extra_trees'], max_depth=hp['extra_max_depth'],
            min_samples_leaf=spec['leaf'], max_features=1., random_state=hp['seed'], n_jobs=hp['threads'])
        m.fit(x, y, sample_weight=w)
        return dict(kind='classifier', model=m)
    if spec['family'] == 'quantile':
        from catboost import CatBoostRegressor
        delta = np.array([r['y'][j]-r['x'][j] for r in rows])
        m = CatBoostRegressor(iterations=hp['cat_iterations'], depth=hp['cat_depth'],
            learning_rate=hp['cat_learning_rate'], l2_leaf_reg=hp['cat_l2'],
            random_seed=hp['seed'], thread_count=hp['threads'],
            loss_function=f"Quantile:alpha={spec['q']}", verbose=False, allow_writing_files=False)
        m.fit(x, delta, sample_weight=w)
        return dict(kind='quantile', model=m)
    raise ValueError('Unknown family')


def score(model, rows, j, spec):
    if model['kind'] == 'momentum':
        return -np.array([7*r['x'][j+2] for r in rows])
    if model['kind'] == 'constant':
        return np.full(len(rows), model['value'])
    x = matrix(rows, spec)
    if model['kind'] == 'logit':
        return model['model'].predict_proba((x-model['mean'])/model['scale'])[:, 1]
    if model['kind'] == 'classifier':
        return model['model'].predict_proba(x)[:, 1]
    return -model['model'].predict(x)


def select(rows, records, p):
    only2024(rows)
    table = []
    selected, diagnostic = [], []
    for j, name in enumerate(NAMES):
        options, safety = [], []
        y = events(rows, j)
        for order, record in enumerate(records):
            spec = record['spec']
            thresholds = p['decline_thresholds'] if spec['family'] in ['momentum', 'quantile'] else p['probability_thresholds']
            values = np.asarray(record['scores'][j])
            if values.shape != (len(rows),) or not np.isfinite(values).all():
                raise ValueError('Scores must cover entire selection cohort')
            for g_order, g in enumerate(p['gates']):
                for threshold in thresholds:
                    m = confusion(rows, y, (values >= threshold) & gate(rows, j, g))
                    safe = m['fp'] == 0 and m['fp_dates'] == 0
                    eligible = safe and m['tp'] >= 2 and m['tp_dates'] >= 2
                    entry = dict(index=name, candidate=spec['id'], gate=g,
                                 threshold=threshold, metrics=m, eligible=eligible)
                    table.append(entry)
                    rank = (-(m['date_balanced_recall'] or 0), -m['tp_dates'], -m['tp'], order, g_order, -threshold)
                    if safe and m['tp']:
                        safety.append((rank, entry))
                    if eligible:
                        options.append((rank, entry))
        def choice(options):
            if not options:
                return dict(index=name, enabled=False, reason='No operating point meets 2024 guards; retain original detector')
            return dict(min(options, key=lambda v: v[0])[1], enabled=True)
        selected.append(choice(options))
        diagnostic.append(choice(safety))
    return selected, diagnostic, table


def check_sources(p):
    if digest(SOURCE) != p['source_2024_sha256'] or digest(LEGACY) != p['legacy_sha256']:
        raise ValueError('Frozen input changed')


def fit():
    if (D / 'frozen-selection.json').exists() or (D / 'report.json').exists():
        raise ValueError('Frozen experiment cannot be retuned in place')
    p = protocol()
    check_sources(p)
    allrows = read(SOURCE)
    rows = [r for r in allrows if r['fid'] not in HOLDOUT]
    only2024(rows)
    folds = list(chronological_folds(rows, p['folds']))
    validation = [r for _, _, vv in folds for r in vv]
    records = []
    for spec in p['candidates']:
        scores = [[], []]
        for _, tt, vv in folds:
            for j in range(2):
                m = train(tt, j, spec, p)
                scores[j].extend(score(m, vv, j, spec).tolist())
        records.append(dict(spec=spec, scores=scores))
        print('2024 OOF:', spec['id'], flush=True)
    selected, diagnostic, table = select(validation, records, p)
    dump(D / 'selection-table.json', table)
    dump(D / 'oof-predictions.json', dict(rows=validation, records=records))
    import joblib
    paths = {}
    for j in range(2):
        for choice in [selected[j], diagnostic[j]]:
            if not choice['enabled']:
                continue
            identifier = f"{j}-{choice['candidate']}"
            if identifier in paths:
                continue
            spec = next(c for c in p['candidates'] if c['id'] == choice['candidate'])
            m = train(rows, j, spec, p)
            path = D / (identifier + '.joblib')
            joblib.dump(m, path)
            paths[identifier] = dict(path=str(path.relative_to(ROOT)), sha256=digest(path))
    frozen = dict(created_at_utc=datetime.now(timezone.utc).isoformat(),
        protocol_sha256=digest(D / 'protocol.json'), source_2024_sha256=digest(SOURCE),
        legacy_sha256=digest(LEGACY), selected=selected, diagnostic=diagnostic, models=paths,
        fit_keys=[list(key(r)) for r in rows], fit_pairs=len(rows), fit_dates=len({r['issue'] for r in rows}),
        fit_max_target=max(r['target'] for r in rows),
        folds=[dict(start=s, fit_max_target=max(r['target'] for r in tt), fit_pairs=len(tt),
            validation_min_issue=min(r['issue'] for r in vv), validation_pairs=len(vv)) for s, tt, vv in folds],
        selection_table_sha256=digest(D / 'selection-table.json'),
        oof_sha256=digest(D / 'oof-predictions.json'), code_sha256=digest(__file__),
        production_promoted=False)
    dump(D / 'frozen-selection.json', frozen)
    print('Frozen choices:', selected, '\nRejected diagnostic:', diagnostic, flush=True)
    return frozen


def infer(rows, p, f, which='selected'):
    import joblib
    point = point_predict(read(LEGACY)['selected_model'], rows)
    old = point-np.array([r['x'][:2] for r in rows]) <= -.10
    alarms = old.copy()
    scores = [[None, None] for _ in rows]
    for j, choice in enumerate(f[which]):
        if not choice['enabled']:
            continue
        entry = f['models'][f"{j}-{choice['candidate']}"]
        path = ROOT / entry['path']
        if digest(path) != entry['sha256']:
            raise ValueError('Frozen model changed')
        m = joblib.load(path)  # Own local file, exact digest checked before loading.
        spec = next(c for c in p['candidates'] if c['id'] == choice['candidate'])
        values = score(m, rows, j, spec)
        alarms[:, j] = (values >= choice['threshold']) & gate(rows, j, choice['gate'])
        for i, value in enumerate(values):
            scores[i][j] = float(value)
    return alarms, scores, point, old


def evaluate():
    p = read(D / 'protocol.json')
    f = read(D / 'frozen-selection.json')
    frozen_sha = digest(D / 'frozen-selection.json')
    check_sources(p)
    if digest(D / 'protocol.json') != f['protocol_sha256'] or digest(__file__) != f['code_sha256']:
        raise ValueError('Protocol/code changed after freeze')
    if (D / 'report.json').exists():
        raise ValueError('Already evaluated; no repeated test-driven search')
    # First access to test data in this experiment, after model/threshold freeze.
    all2026 = read(ROOT / 'data/farm-research/crop-season-model/data-2026.json')
    reserved = [r for r in read(SOURCE) if r['fid'] in HOLDOUT]
    splits = dict(spatial2024=reserved, all2026=all2026,
        temporal2026=[r for r in all2026 if r['fid'] not in HOLDOUT],
        spatial2026=[r for r in all2026 if r['fid'] in HOLDOUT])
    results, predictions = {}, []
    for name, rr in splits.items():
        alarm, scores, point, old = infer(rr, p, f)
        diagnostic, _, _, _ = infer(rr, p, f, 'diagnostic')
        actual = np.array([r['y'] for r in rr])
        now = np.array([r['x'][:2] for r in rr])
        results[name] = {}
        for j, index in enumerate(NAMES):
            y = events(rr, j)
            m = {label: confusion(rr, y, aa) for label, aa in [
                ('persistence', np.zeros(len(rr), bool)), ('legacy', old[:, j]),
                ('new', alarm[:, j]), ('rejected_diagnostic', diagnostic[:, j])]}
            w = date_weights(rr)
            m['point_mae'] = dict(legacy=float(np.average(abs(point[:, j]-actual[:, j]), weights=w)),
                persistence=float(np.average(abs(now[:, j]-actual[:, j]), weights=w)))
            m['passes_vs_legacy'] = (m['new']['tp'] > m['legacy']['tp'] and
                m['new']['fp'] <= m['legacy']['fp'] and m['new']['fp_dates'] <= m['legacy']['fp_dates'])
            m['passes_vs_persistence'] = m['new']['tp'] > 0 and m['new']['fp'] == 0
            results[name][index] = m
        for i, r in enumerate(rr):
            predictions.append(dict(split=name, fid=r['fid'], issue=r['issue'], target=r['target'],
                actual=r['y'], persistence=r['x'][:2], point=point[i].tolist(), legacy_alarm=old[i].tolist(),
                new_alarm=alarm[i].tolist(), rejected_diagnostic_alarm=diagnostic[i].tolist(), scores=scores[i]))
    protected = read(D / 'preservation-before.json')
    changed = [name for name, h in protected.items() if not (ROOT/name).is_file() or digest(ROOT/name) != h]
    if changed or digest(D / 'frozen-selection.json') != frozen_sha:
        raise ValueError('Prior files or frozen selection changed: ' + repr(changed[:5]))
    dump(D / 'test-predictions.json', predictions)
    out = dict(created_at_utc=datetime.now(timezone.utc).isoformat(), frozen_sha256=frozen_sha,
        selected=f['selected'], diagnostic=f['diagnostic'], results=results,
        test_constraints={index: f['selected'][j]['enabled'] and all(
            results[s][index]['passes_vs_legacy'] and results[s][index]['passes_vs_persistence']
            for s in ['all2026', 'spatial2026']) for j, index in enumerate(NAMES)},
        protected_files=len(protected), changed_files=changed, production_promoted=False,
        point_and_intervals_unchanged=True, year2025='unavailable in existing satellite archive; not evaluated',
        evaluation_code_sha256=digest(__file__))
    dump(D / 'report.json', out)
    print('2026 results:', results['all2026'], flush=True)
    return out
