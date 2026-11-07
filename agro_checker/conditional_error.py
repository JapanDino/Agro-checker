"""Forecast-only residual scale/tails; blocked OOF fitting, JSON-only inference."""

import math
import numpy as np
from .minimum_model import features, target_minimum, fit_minimum, point_minimum
from .cold_interval import finite_quantile


def crossfit_residuals(samples, point_spec, blocks=6):
    if not samples or any(n['year'] not in [2022, 2023] for n in samples):
        raise ValueError('Residual fitting accepts only 2022/2023')
    ordered = sorted((n for n in samples if target_minimum(n) is not None), key=lambda n: n['date'])
    if len({n['date'] for n in ordered}) != len(ordered): raise ValueError('Duplicate fit nights')
    if blocks != 6 or len(ordered) < 40: raise ValueError('Need six blocks and sufficient nights')
    records = []; audit = []
    for indices in np.array_split(np.arange(len(ordered)), blocks):
        held = set(int(i) for i in indices)
        train = [n for i, n in enumerate(ordered) if i not in held]
        model = fit_minimum(point_spec, train)
        test = [ordered[i] for i in sorted(held)]
        audit.append({'fit_dates': model['training_dates'], 'held_dates': [n['date'] for n in test]})
        for n in test:
            records.append({'date': n['date'], 'x': features(n).tolist(),
                            'error_c': target_minimum(n)-point_minimum(model, n)[0]})
    return sorted(records, key=lambda r: r['date']), audit


def tree_dump(estimator):
    t = estimator.tree_
    return {'left': t.children_left.tolist(), 'right': t.children_right.tolist(),
            'feature': t.feature.tolist(), 'threshold': t.threshold.tolist(),
            'value': t.value[:, 0, 0].tolist()}


def fit_head(records, audit, spec):
    if spec['mode'] in ['legacy', 'lower']: return {'spec': spec}
    if not records or any(int(r['date'][:4]) not in [2022, 2023] for r in records):
        raise ValueError('Head fitting accepts only prior training years')
    if len({r['date'] for r in records}) != len(records): raise ValueError('Duplicate residual dates')
    for block in audit:
        if set(block['fit_dates']) & set(block['held_dates']): raise ValueError('OOF overlap')
    held_dates = [d for b in audit for d in b['held_dates']]
    if sorted(held_dates) != sorted(r['date'] for r in records): raise ValueError('OOF audit mismatch')
    x = np.asarray([r['x'] for r in records]); y = np.asarray([r['error_c'] for r in records])
    if not np.isfinite(x).all() or not np.isfinite(y).all(): raise ValueError('Invalid OOF residuals')
    mean = x.mean(axis=0); scale = x.std(axis=0); scale[scale == 0] = 1
    z = (x-mean)/scale
    head = {'spec': spec, 'mean': mean.tolist(), 'scale': scale.tolist(),
            'fit_dates': [r['date'] for r in records], 'crossfit_audit': audit, 'heads': []}
    if spec['mode'] == 'scale': targets = [np.log1p(np.abs(y))]
    elif spec['mode'] == 'asymmetric': targets = [np.log1p(np.maximum(-y, 0)), np.log1p(np.maximum(y, 0))]
    else: targets = [y, y]
    for i, target in enumerate(targets):
        if spec['mode'] in ['scale', 'asymmetric']:
            from sklearn.linear_model import Ridge
            est = Ridge(alpha=spec['alpha'], solver='svd').fit(z, target)
        elif spec['mode'] == 'quantile':
            from sklearn.linear_model import QuantileRegressor
            est = QuantileRegressor(quantile=[.05, .95][i], alpha=spec['alpha'], solver='highs').fit(z, target)
        elif spec['mode'] == 'trees':
            from sklearn.ensemble import GradientBoostingRegressor
            est = GradientBoostingRegressor(loss='quantile', alpha=[.05, .95][i],
                    n_estimators=spec['estimators'], max_depth=spec['depth'], min_samples_leaf=spec['minimum_leaf'],
                    learning_rate=spec['learning_rate'], random_state=spec['seed']).fit(z, target)
        else: raise ValueError('Unknown residual mode')
        if spec['mode'] == 'trees':
            head['heads'].append({'initial': float(est.init_.predict(z[:1])[0]),
                                 'trees': [tree_dump(t[0]) for t in est.estimators_]})
        else: head['heads'].append({'coef': est.coef_.tolist(), 'intercept': float(est.intercept_)})
    return head


def shape(head, night, p):
    x = features(night)  # No target access, no weather imputation.
    z = (x-np.asarray(head['mean']))/np.asarray(head['scale'])
    outputs = []
    for h in head['heads']:
        if 'coef' in h: value = h['intercept']+float(z @ np.asarray(h['coef']))
        else:
            value = h['initial']
            for t in h['trees']:
                index = 0
                while t['left'][index] != -1:
                    index = t['left'][index] if z[t['feature'][index]] <= t['threshold'][index] else t['right'][index]
                value += head['spec']['learning_rate']*t['value'][index]
        if not math.isfinite(value): raise ValueError('Nonfinite residual head')
        outputs.append(value)
    mode = head['spec']['mode']
    if mode in ['scale', 'asymmetric']:
        # Clip the log before exp to avoid overflow on unsupported extrapolations.
        scales = [float(np.clip(np.expm1(np.clip(v, 0, math.log1p(p['scale_ceiling_c']))),
                               p['scale_floor_c'], p['scale_ceiling_c'])) for v in outputs]
        lo, hi = (-scales[0], scales[-1])
    else: lo, hi = min(*outputs, 0.), max(*outputs, 0.)
    return {'lower': lo, 'upper': hi, 'max_abs_z': float(np.max(np.abs(z)))}


def calibration_pool(point_model, head, samples, p, allowed_year):
    if allowed_year not in [2023, 2024] or not samples or any(n['year'] != allowed_year for n in samples):
        raise ValueError('Calibration must use one prior validation year')
    dates = {n['date'] for n in samples}
    if len(dates) != len(samples): raise ValueError('Duplicate calibration night')
    fit_dates = set(point_model['training_dates']) | set(head.get('fit_dates', []))
    if fit_dates & dates or any(int(d[:4]) >= allowed_year for d in fit_dates): raise ValueError('Calibration overlaps/follows fit')
    rows = []
    for n in samples:
        actual = target_minimum(n)
        if actual is None: continue
        error = actual-point_minimum(point_model, n)[0]; s = shape(head, n, p)
        if head['spec']['mode'] in ['scale', 'asymmetric']:
            score = max(-error/-s['lower'], error/s['upper'], 0.)
        else: score = max(s['lower']-error, error-s['upper'], 0.)
        rows.append({'date': n['date'], 'score': score})
    return rows


def calibrate_shape(pool, p, exclude_date=None):
    rows = [r for r in pool if r['date'] != exclude_date]
    if len(rows) < p['minimum_calibration_nights']: raise ValueError('Insufficient calibration')
    q = finite_quantile([r['score'] for r in rows], p['coverage'])
    if q is None: raise ValueError('No finite quantile; never cap rank')
    return {**q, 'excluded_date': exclude_date}


def forecast(point_model, head, calibration, night, p):
    point, ood = point_minimum(point_model, night); s = shape(head, night, p)
    q = calibration['value']
    if not math.isfinite(q) or q < 0: raise ValueError('Invalid calibrated score')
    if head['spec']['mode'] in ['scale', 'asymmetric']: lo, hi = s['lower']*q, s['upper']*q
    else: lo, hi = s['lower']-q, s['upper']+q
    return {'point_c': point, 'lower_c': point+lo, 'upper_c': point+hi,
            'max_abs_z': ood, 'residual_head_max_abs_z': s['max_abs_z'],
            'extrapolation': max(ood, s['max_abs_z']) > 4, 'coverage_guaranteed': False,
            'probability_of_crop_damage': None,
            'threshold_state': 'cold_range' if point+hi < 0 else 'warm_range' if point+lo >= 0 else 'crosses_zero'}


def safe_conditional_prediction(point_model, head, calibration, night, p, station_id):
    if station_id != '34929': return {'status': 'unsupported_site', 'prediction': None}
    try: result = forecast(point_model, head, calibration, night, p)
    except (ValueError, KeyError, TypeError, IndexError): return {'status': 'invalid_or_unsupported_inputs', 'prediction': None}
    if result['extrapolation']: return {'status': 'extrapolation', 'prediction': None}
    return {'status': 'research_only', 'prediction': result}
