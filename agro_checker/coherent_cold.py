"""A single minimum-temperature CDF: shared analog calibration or ordinal logistic.

Only probabilities are replaced in this research branch. Timing, hourly events,
crop damage and an orchard guarantee are deliberately not inferred from a CDF.
"""
import math
import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit
from . import product_risk as scenarios


def prior_samples(samples):
    if not samples or any(n['year'] not in [2022, 2023] for n in samples):
        raise ValueError('Only 2022/2023 may enter fit/calibration')
    usable = [n for n in samples if scenarios.complete(n)]
    if len({(n['site_id'], n['date']) for n in usable}) != len(usable):
        raise ValueError('Duplicate site/date')
    for n in usable:
        scenarios.validate(n)
    return usable


def score_logit(score):
    if isinstance(score, bool) or not isinstance(score, (float, int)) or not math.isfinite(score) or not 0 <= score <= 1:
        raise ValueError('Invalid original detector score')
    return float(logit(np.clip(score, 1e-6, 1 - 1e-6)))


def fit_shared(samples, neighbors, protocol):
    usable = prior_samples(samples)
    spec = {'name': 'shared_' + str(neighbors), 'neighbors': neighbors}
    dates = sorted({n['date'] for n in usable})
    records, audits = [], []
    thresholds = protocol['weather_thresholds_c']
    for block in np.array_split(dates, protocol['date_blocks']):
        held = set(block.tolist())
        train = [n for n in usable if n['date'] not in held]
        model = scenarios.fit(train, spec)
        audits.append({'fit_dates': model['fit_dates'], 'held_dates': sorted(held)})
        for n in usable:
            if n['date'] not in held:
                continue
            curves, _ = scenarios.members(model, n)
            records.append({'minimum': min(n['target_temperature_c']),
                            'raw': [scenarios.raw_probability(curves, q) for q in thresholds]})
    events = int(sum(r['minimum'] < 0 for r in records))
    status = 'insufficient_events'
    head = None
    if len(records) >= protocol['minimum_calibration_nights'] and min(events, len(records) - events) >= protocol['minimum_calibration_events']:
        # Each night contributes equal total weight. One positive slope and one
        # intercept are shared across all thresholds: g(F(q)) stays increasing.
        x = np.array([scenarios.logit(p, neighbors) for r in records for p in r['raw']])
        y = np.array([int(r['minimum'] < q) for r in records for q in thresholds])
        edge0, edge1 = scenarios.logit(0, neighbors), scenarios.logit(1, neighbors)
        def loss(theta):
            a, b = theta
            low, high = expit(a * edge0 + b), expit(a * edge1 + b)
            f = np.clip((expit(a * x + b) - low) / max(high-low, 1e-15), 1e-12, 1-1e-12)
            return float((-y*np.log(f)-(1-y)*np.log1p(-f)).sum()/len(thresholds) +
                         .5/protocol['shared_platt_c']*((a-1)**2+.01*b*b))
        m = minimize(loss, [1.,0.], method='L-BFGS-B', bounds=[(.05,10.),(-10.,10.)], options={'maxiter':1000})
        if not m.success or not math.isfinite(m.fun):
            raise ValueError('Shared CDF calibration did not converge')
        head = {'coef': float(m.x[0]), 'intercept': float(m.x[1])}
        status = 'fitted_shared_oof'
    return {'kind': 'shared', 'model': scenarios.fit(usable, spec), 'head': head,
            'status': status, 'thresholds': thresholds, 'events': events,
            'training_nights': len(usable), 'audits': audits}


def fit_ordinal(samples, score_records, inputs, penalty, protocol):
    usable = prior_samples(samples)
    if inputs not in ['gfs', 'score', 'joint'] or penalty <= 0:
        raise ValueError('Invalid ordinal registry setting')
    by_date = {r['date']: r for r in score_records}
    if len(by_date) != len(score_records) or set(by_date) != {n['date'] for n in usable}:
        raise ValueError('OOF scores must match all complete station dates')
    for n in usable:
        r = by_date[n['date']]
        if n['date'] in r['fit_dates'] or not r['fit_dates'] or any(d[:4] not in ['2022', '2023'] for d in r['fit_dates']):
            raise ValueError('Detector OOF leakage')
        score_logit(r['score'])
    thresholds = np.array(protocol['weather_thresholds_c'], dtype=float)
    if len(thresholds) < 2 or np.any(np.diff(thresholds) <= 0):
        raise ValueError('Strictly ordered thresholds required')
    gfs = np.array([min(n['gfs_temperature_c']) for n in usable])
    z = np.array([score_logit(by_date[n['date']]['score']) for n in usable])
    center, scale = float(z.mean()), max(float(z.std()), .1)
    temperature_scale = max(float(gfs.std()), 1.)
    q = np.tile(thresholds, (len(usable), 1))
    if inputs in ['gfs', 'joint']:
        q = q - gfs[:, None]
    q /= temperature_scale
    z = (z - center) / scale if inputs != 'gfs' else np.zeros_like(z)
    categories = np.array([np.searchsorted(thresholds, min(n['target_temperature_c']), side='right') for n in usable])

    def objective(theta):
        a, b, c = theta
        f = expit(a * q + b * z[:, None] + c)
        probs = np.diff(np.column_stack([np.zeros(len(f)), f, np.ones(len(f))]), axis=1)
        likelihood = probs[np.arange(len(probs)), categories]
        return -float(np.log(np.maximum(likelihood, 1e-15)).sum()) + .5 * penalty * (a * a + b * b + .01 * c * c)

    bounds = [(.05, 50.), (0., 0.) if inputs == 'gfs' else (0., 50.), (-30., 30.)]
    result = minimize(objective, [1., 0. if inputs == 'gfs' else 1., -2.], method='L-BFGS-B', bounds=bounds,
                      options={'maxiter': 1000, 'ftol': 1e-12})
    if not result.success or not np.isfinite(result.fun):
        raise ValueError('Ordinal optimization failed: ' + str(result.message))
    a, b, c = map(float, result.x)
    return {'kind': 'ordinal', 'inputs': inputs, 'penalty': penalty,
            'a': a, 'b': b, 'c': c, 'score_center': center, 'score_scale': scale,
            'temperature_scale': temperature_scale, 'thresholds': thresholds.tolist(),
            'training_nights': len(usable), 'fit_dates': sorted(by_date),
            'events': int(sum(min(n['target_temperature_c']) < 0 for n in usable)),
            'optimizer_success': True, 'objective': float(result.fun),
            'status': 'fitted_ordinal_oof_detector',
            'feature_range': [float(gfs.min()), float(gfs.max())],
            'oof_detector_audits': score_records}


def forecast(model, sample, detector_score):
    scenarios.validate(sample)
    z = score_logit(detector_score)
    thresholds = model['thresholds']
    if model['kind'] == 'shared':
        curves, audit = scenarios.members(model['model'], sample)
        raw = [scenarios.raw_probability(curves, q) for q in thresholds]
        # Normalize the shared increasing map to endpoints 0 and 1. This is a
        # genuine finite-support CDF, without inventing mass outside the library.
        head = model['head']
        if head:
            transform = lambda p: float(expit(head['coef'] * scenarios.logit(p, len(curves)) + head['intercept']))
            low, high = transform(0), transform(1)
            probabilities = [min(1., max(0., (transform(p) - low) / (high - low))) for p in raw]
        else:
            probabilities = raw.copy()
        outside = audit['max_abs_z'] > 4
        statuses = ['finite_scenario_support_boundary' if p in [0, 1] else model['status'] for p in raw]
    else:
        minimum = min(sample['gfs_temperature_c'])
        q = np.array(thresholds, dtype=float)
        if model['inputs'] in ['gfs', 'joint']:
            q -= minimum
        q /= model['temperature_scale']
        z = (z - model['score_center']) / model['score_scale'] if model['inputs'] != 'gfs' else 0.
        probabilities = expit(model['a'] * q + model['b'] * z + model['c']).tolist()
        raw = probabilities.copy()
        outside = not model['feature_range'][0] <= minimum <= model['feature_range'][1]
        statuses = [model['status']] * len(thresholds)
    if any(not math.isfinite(v) or not 0 <= v <= 1 for v in probabilities) or any(a > b + 1e-12 for a, b in zip(probabilities, probabilities[1:])):
        raise ValueError('Single CDF invariant broken')
    return {str(int(q)): {'probability': p, 'raw_probability': r, 'threshold_c': q,
                          'calibration_status': status, 'extrapolation': outside,
                          'scope': 'minimum CDF only; no hourly/timing model',
                          'research_only': True, 'crop_damage_probability': None}
            for q, p, r, status in zip(thresholds, probabilities, raw, statuses)}
