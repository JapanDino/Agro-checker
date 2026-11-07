"""Prior-year residual heads; supports extended OOF audits without pickle."""
import numpy as np
from .conditional_error import tree_dump


def fit_extended_head(records, audit, spec, allowed_point_years=(2022, 2023)):
    if spec['mode'] in ['legacy', 'lower']: return {'spec': spec}
    if not records or any(int(r['date'][:4]) not in [2021, 2022, 2023] for r in records):
        raise ValueError('Head fitting accepts only2021–2023')
    if len({r['date'] for r in records}) != len(records): raise ValueError('Duplicate residual dates')
    for b in audit:
        if set(b['fit_dates']) & set(b['held_dates']): raise ValueError('Point/residual label overlap')
        if any(int(d[:4]) not in allowed_point_years for d in b['fit_dates']): raise ValueError('Point family fit years changed')
    if sorted(d for b in audit for d in b['held_dates']) != sorted(r['date'] for r in records):
        raise ValueError('Residual audit mismatch')
    x = np.asarray([r['x'] for r in records]); y = np.asarray([r['error_c'] for r in records])
    if not np.isfinite(x).all() or not np.isfinite(y).all(): raise ValueError('Nonfinite residuals')
    mean = x.mean(axis=0); scale = x.std(axis=0); scale[scale == 0] = 1; z = (x-mean)/scale
    head = {'spec': spec, 'mean': mean.tolist(), 'scale': scale.tolist(), 'fit_dates': [r['date'] for r in records],
            'crossfit_audit': audit, 'heads': [], 'training_years': sorted({int(r['date'][:4]) for r in records})}
    if spec['mode'] == 'constant': return head
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
            est = GradientBoostingRegressor(loss='quantile', alpha=[.05, .95][i], n_estimators=spec['estimators'],
                max_depth=spec['depth'], min_samples_leaf=spec['minimum_leaf'], learning_rate=spec['learning_rate'], random_state=spec['seed']).fit(z, target)
        else: raise ValueError('Unknown conditional family')
        if spec['mode'] == 'trees': head['heads'].append({'initial': float(est.init_.predict(z[:1])[0]), 'trees': [tree_dump(t[0]) for t in est.estimators_]})
        else: head['heads'].append({'coef': est.coef_.tolist(), 'intercept': float(est.intercept_)})
    return head
