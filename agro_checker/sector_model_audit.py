"""Descriptive sector audit. No fitting, threshold search, or operational writes."""
from collections import Counter, defaultdict
from datetime import date, timedelta
import numpy as np


def balanced_mean(rows, values):
    if not rows:
        return None
    counts = Counter(r['issue'] for r in rows)
    return float(np.average(values, weights=[1 / counts[r['issue']] for r in rows]))


def point_quality(rows, prediction, radius):
    if not rows:
        return dict(pairs=0, dates=0, mae=None, persistence_mae=None, per_index={})
    actual = np.array([r['y'] for r in rows])
    now = np.array([r['x'][:2] for r in rows])
    prediction = np.asarray(prediction)
    error = abs(prediction - actual)
    baseline = abs(now - actual)
    out = dict(pairs=len(rows), dates=len({r['issue'] for r in rows}),
               mae=balanced_mean(rows, error.mean(axis=1)),
               persistence_mae=balanced_mean(rows, baseline.mean(axis=1)), per_index={})
    for j, name in enumerate(('ndvi', 'ndmi')):
        event = actual[:, j] - now[:, j] <= -.10
        alarm = prediction[:, j] - now[:, j] <= -.10
        low, high = np.clip(prediction[:, j]-radius[j], -1, 1), np.clip(prediction[:, j]+radius[j], -1, 1)
        detail = dict(mae=balanced_mean(rows, error[:, j]), persistence_mae=balanced_mean(rows, baseline[:, j]),
                      observed_declines=int(event.sum()), tp=int((event & alarm).sum()),
                      fp=int((~event & alarm).sum()), fn=int((event & ~alarm).sum()),
                      coverage=balanced_mean(rows, ((actual[:, j] >= low) & (actual[:, j] <= high)).astype(float)),
                      mean_width=balanced_mean(rows, high-low),
                      predicted_delta_min=float((prediction[:, j]-now[:, j]).min()),
                      predicted_delta_max=float((prediction[:, j]-now[:, j]).max()))
        for label, mask in [('decline', event), ('other', ~event)]:
            rr = [r for r, m in zip(rows, mask) if m]
            detail[label+'_mae'] = balanced_mean(rr, error[mask, j])
            detail[label+'_coverage'] = balanced_mean(rr, ((actual[mask, j] >= low[mask]) & (actual[mask, j] <= high[mask])).astype(float))
        out['per_index'][name] = detail
    return out


def confusion(actual, predicted):
    y, a = np.array(actual, dtype=bool), np.array(predicted, dtype=bool)
    tp, fp, fn, tn = (int(mask.sum()) for mask in (y & a, ~y & a, y & ~a, ~y & ~a))
    return dict(tp=tp, fp=fp, fn=fn, tn=tn,
                recall=tp/(tp+fn) if tp+fn else None,
                precision=tp/(tp+fp) if tp+fp else None)


def observation_opportunities(rows, start, end):
    """Denominator uses only current/past clear observations; never future labels."""
    start, end = date.fromisoformat(start), date.fromisoformat(end)
    rows = sorted([r for r in rows if start <= date.fromisoformat(r['date']) <= end], key=lambda r:r['date'])
    current, opportunities, scorable = [], [], []
    for r in rows:
        d = date.fromisoformat(r['date'])
        previous = [p for p in rows if p['baseline'] == r['baseline']
                    and 2 <= (d-date.fromisoformat(p['date'])).days <= 21]
        current.append(d)
        if previous and d <= end-timedelta(days=8):
            opportunities.append(d)
            if any(t['baseline'] == r['baseline'] and 6 <= (date.fromisoformat(t['date'])-d).days <= 8 for t in rows):
                scorable.append(d)
    dates = sorted(set(current))
    anchors = [start, *dates, end]
    # Calendar elapsed gap, not a count of consecutive cloudy days or sensor revisit time.
    gaps = [(b-a).days for a,b in zip(anchors, anchors[1:])]
    return dict(clear_core_days=len(dates), window_calendar_days=(end-start).days+1,
                first_clear=str(dates[0]) if dates else None, last_clear=str(dates[-1]) if dates else None,
                max_elapsed_gap_days=max(gaps), eligible_issue_days=len(opportunities),
                scorable_6_8_day_issues=len(scorable),
                unscorable_6_8_day_issues=len(opportunities)-len(scorable),
                scorable_fraction=len(scorable)/len(opportunities) if opportunities else None)


def merged_event_windows(rows, j):
    """Connected overlapping forecast windows, not independently verified farm episodes."""
    grouped = defaultdict(list)
    for r in rows:
        if r['y'][j]-r['x'][j] <= -.10:
            grouped[r['fid']].append((r['issue'], r['target']))
    count = 0
    for windows in grouped.values():
        edge = None
        for start, end in sorted(windows):
            if edge is None or start > edge:
                count += 1
                edge = end
            else:
                edge = max(edge, end)
    return count


def block_error_intervals(rows, prediction, block_days=7, repeats=4000, seed=202410):
    """Fixed calendar-block descriptive sensitivity, never used for model selection."""
    if not rows:
        return None
    error = abs(np.asarray(prediction)-np.array([r['y'] for r in rows])).mean(axis=1)
    base = abs(np.array([r['x'][:2] for r in rows])-np.array([r['y'] for r in rows])).mean(axis=1)
    by_day = defaultdict(list)
    for r, diff in zip(rows, error-base):
        by_day[r['issue']].append(float(diff))
    origin = date.fromisoformat(min(by_day))
    blocks = defaultdict(list)
    for d, values in by_day.items():
        blocks[(date.fromisoformat(d)-origin).days//block_days].append(float(np.mean(values)))
    sums = np.array([sum(v) for _,v in sorted(blocks.items())])
    sizes = np.array([len(v) for _,v in sorted(blocks.items())])
    random = np.random.default_rng(seed)
    take = random.integers(0, len(blocks), size=(repeats, len(blocks)))
    samples = sums[take].sum(axis=1)/sizes[take].sum(axis=1)
    return dict(block_days=block_days, nonempty_blocks=len(blocks), repeats=repeats, seed=seed,
                mae_difference=float(np.mean([np.mean(v) for v in by_day.values()])),
                descriptive_95_percentile_interval=np.quantile(samples, [.025, .975]).tolist(),
                limitation='All fragments on a date kept together; overlapping windows and longer temporal dependence remain. Not a proof of significance, transfer or noninferiority.')
