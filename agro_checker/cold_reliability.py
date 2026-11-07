"""Cluster-aware cold-tail envelopes, separate from point forecasts and alerts.

Conditional-on-observed-cold calibration is a safety stress envelope, not a
distribution-free conditional coverage or crop-damage probability claim.
"""
from collections import defaultdict
from datetime import date, timedelta
import math
import numpy as np
from .cold_interval import finite_quantile, calibrate_envelope


def registry(p):
    return [{'name': 'previous', 'mode': 'previous'}] + [
        {'name': f'cold{cold:g}_risk{risk:g}_q{q:g}_alt{alt if alt is not None else "all"}',
         'mode': 'cold_tail', 'cold_pool_limit_c': cold, 'risk_threshold_c': risk,
         'quantile': q, 'altitude_window_m': alt}
        for cold in p['cold_pool_limits_c'] for risk in p['risk_thresholds_c']
        for q in p['tail_quantiles'] for alt in p['altitude_windows_m']]


def week_window(day, purge_days):
    d = date.fromisoformat(day)
    monday = d-timedelta(days=d.weekday())
    return ((monday-timedelta(days=purge_days)).isoformat(),
            (monday+timedelta(days=6+purge_days)).isoformat())


def check_bank(bank):
    if not bank or len({(r['id'], r['date']) for r in bank}) != len(bank):
        raise ValueError('Empty/duplicate station-night bank')
    for r in bank:
        if r['year'] not in [2023, 2024] or int(r['date'][:4]) != r['year'] or r.get('role') == 'heldout_site':
            raise ValueError('Only development2023/2024 calibration labels')
        for k in ['actual_c', 'point_huber_c', 'point_gfs_c', 'elevation_m']:
            if isinstance(r[k], bool) or not isinstance(r[k], (int, float)) or not math.isfinite(r[k]):
                raise ValueError('Nonfinite calibration record')
        if r['point_fit_last_year'] >= r['year']:
            raise ValueError('Calibration labels overlap point training year')


def matching(rows, elevation, window, minimum_sites):
    selected = rows if window is None else [r for r in rows if abs(r['elevation_m']-elevation) <= window]
    fallback = len({r['id'] for r in selected}) < minimum_sites
    return (rows if fallback else selected), fallback


def calibrate(bank, spec, p, *, anchor, query_id, elevation_m, exclude_day=None):
    """All labels consumed here. At inference only point, GFS and metadata enter."""
    check_bank(bank)
    if anchor not in ['huber', 'gfs'] or not math.isfinite(elevation_m):
        raise ValueError('Invalid anchor or metadata')
    rows = list(bank)
    excluded_window = week_window(exclude_day, p['purge_days']) if exclude_day else None
    if excluded_window:
        rows = [r for r in rows if not excluded_window[0] <= r['date'] <= excluded_window[1]]
    # Reference baseline must reproduce the existing local range. Other stations
    # use GFS and a regional empirical baseline; never the reference Huber point.
    if anchor == 'huber':
        local = [r for r in rows if r['id'] == query_id]
        pool = [{'date': r['date'], 'risk_temperature_c': min(r['point_huber_c'], r['point_gfs_c']),
                 'absolute_error_c': abs(r['point_huber_c']-r['actual_c']),
                 'overprediction_c': r['point_huber_c']-r['actual_c']} for r in local]
        old = calibrate_envelope(pool, {'name': 'risk_5_lower_95', 'mode': 'lower',
                                  'risk_threshold_c': 5.0, 'quantile': .95},
                                 {'base_coverage': p['coverage'], 'minimum_pool_nights': p['minimum_pool_nights']})
        baseline = {'base_radius_c': old['base_radius_c'], 'lower_radius_c': old['lower_radius_c'],
                    'upper_radius_c': old['upper_radius_c'], 'risk_threshold_c': 5.0,
                    'fallback': old['fallback_to_global']}
    else:
        rows = [r for r in rows if r['id'] != query_id]
        near, fallback = matching(rows, elevation_m, p['baseline_altitude_window_m'], p['minimum_matching_sites'])
        if len(near) < p['minimum_pool_nights']:
            raise ValueError('Too few prior baseline residuals')
        q = finite_quantile([abs(r['point_gfs_c']-r['actual_c']) for r in near], p['coverage'])
        if q is None: raise ValueError('No finite baseline radius')
        baseline = {'base_radius_c': q['value'], 'lower_radius_c': q['value'],
                    'upper_radius_c': q['value'], 'risk_threshold_c': None, 'fallback': fallback}
    result = {'spec': spec, 'baseline': baseline, 'anchor': anchor,
              'query_id': query_id, 'elevation_m': elevation_m, 'excluded_window': excluded_window,
              'calibration_years': sorted({r['year'] for r in rows}), 'tail_radius_c': None,
              'tail_dates': 0, 'tail_sites': 0, 'tail_unavailable': False,
              'coverage_guaranteed': False}
    if spec['mode'] == 'previous': return result
    # Matching reference is never retained as its own regional tail donor.
    donors = [r for r in rows if r['id'] != query_id]
    donors, fallback = matching(donors, elevation_m, spec['altitude_window_m'], p['minimum_matching_sites'])
    cold = [r for r in donors if r['actual_c'] < spec['cold_pool_limit_c']]
    by_date = defaultdict(list)
    for r in cold: by_date[r['date']].append(r['point_'+anchor+'_c']-r['actual_c'])
    q = finite_quantile([max(v) for v in by_date.values()], spec['quantile']) if len(by_date) >= p['minimum_tail_dates'] else None
    result.update(tail_dates=len(by_date), tail_sites=len({r['id'] for r in cold}),
                  tail_matching_fallback=fallback, tail_unavailable=q is None,
                  tail_radius_c=max(0., q['value']) if q else None,
                  tail_rank=q['rank'] if q else None,
                  donor_dates=sorted(by_date), donor_sites=sorted({r['id'] for r in cold}))
    return result


def forecast(cal, *, point_c, gfs_minimum_c, date_string, query_id, elevation_m):
    """No observed temperatures, cold labels, detector or future rows accepted."""
    for v in [point_c, gfs_minimum_c, elevation_m]:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValueError('Invalid forecast/metadata')
    if query_id != cal['query_id'] or elevation_m != cal['elevation_m']:
        raise ValueError('Wrong calibration station metadata')
    y = date.fromisoformat(date_string).year
    if y not in [2023, 2024, 2025, 2026] or date_string[5:7] not in ['03', '04', '05']:
        raise ValueError('Unsupported season/year')
    if max(cal['calibration_years']) >= y and not (cal['excluded_window'] and cal['excluded_window'][0] <= date_string <= cal['excluded_window'][1]):
        raise ValueError('Future/unpurged calibration at inference')
    b = cal['baseline']; risk = min(point_c, gfs_minimum_c)
    active = b['risk_threshold_c'] is None or risk <= b['risk_threshold_c']
    lower = b['lower_radius_c'] if active else b['base_radius_c']
    upper = b['upper_radius_c'] if active else b['base_radius_c']
    spec = cal['spec']; extension = spec['mode'] != 'previous' and risk <= spec['risk_threshold_c']
    if extension and cal['tail_radius_c'] is not None: lower = max(lower, cal['tail_radius_c'])
    return {'point_c': point_c, 'lower_c': point_c-lower, 'upper_c': point_c+upper,
            'tail_extension_active': extension, 'tail_unavailable': extension and cal['tail_unavailable'],
            'lower_radius_c': lower, 'upper_radius_c': upper, 'risk_temperature_c': risk,
            'coverage_guaranteed': False, 'probability_of_crop_damage': None,
            'scope': 'research minimum envelope; no hourly timing, alerts or orchard support'}


def score(records):
    from .cold_interval_study import summarize
    from .regional_minimum import episode_ids
    result = summarize(records)
    usable = [r for r in records if r['actual_minimum_c'] is not None]
    cold = [r for r in usable if r['actual_minimum_c'] < 0]
    groups = episode_ids([r['date'] for r in cold])
    episodes = defaultdict(list)
    for r in cold: episodes[groups[r['date']]].append(r)
    covered = lambda r: r['forecast']['lower_c'] <= r['actual_minimum_c'] <= r['forecast']['upper_c']
    sites = {sid: [r for r in usable if r['id'] == sid] for sid in sorted({r['id'] for r in usable})}
    by_site = {sid: summarize(v) for sid, v in sites.items()}
    result.update(cold_dates=len(groups), cold_episode_groups=len(episodes),
                  entirely_covered_cold_episode_groups=sum(all(covered(r) for r in v) for v in episodes.values()),
                  site_macro_coverage=float(np.mean([s['coverage'] for s in by_site.values()])) if sites else None,
                  site_macro_cold_coverage=float(np.mean([s['cold_coverage'] for s in by_site.values() if s['cold_nights']])) if cold else None,
                  by_site=by_site,
                  tail_unavailable_nights=sum(r['forecast'].get('tail_unavailable', False) for r in usable))
    return result
