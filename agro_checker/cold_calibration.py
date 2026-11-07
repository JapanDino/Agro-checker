"""Binary calibration at zero with coherent ordinal spacing at colder thresholds."""
import math
import numpy as np
from scipy.special import expit
from scipy.optimize import minimize
from .coherent_cold import score_logit
from .product_risk import validate


def fit_affine(records, loss, penalty, protocol):
    if loss not in ['brier','logloss'] or penalty<=0 or not records:
        raise ValueError('Invalid calibration setting')
    if len({r['date'] for r in records})!=len(records):raise ValueError('Duplicate calibration dates')
    for r in records:
        if r['year'] not in [2022,2023] or r['date'][:4]!=str(r['year']):raise ValueError('Only prior years may calibrate')
        if not r['fit_dates'] or r['date'] in r['fit_dates'] or any(d[:4] not in ['2022','2023'] for d in r['fit_dates']):
            raise ValueError('Calibration date/fit leakage')
        if r['target'] not in [0,1]:raise ValueError('Known binary event required')
        score_logit(r['score'])
    events=sum(r['target'] for r in records)
    common={'records':len(records),'events':events,'fit_dates':sorted(r['date'] for r in records),
            'loss':loss,'penalty':penalty,'audits':records}
    if len(records)<protocol['minimum_calibration_nights'] or min(events,len(records)-events)<protocol['minimum_calibration_events']:
        return common|{'a':1.,'b':0.,'status':'insufficient_events_identity'}
    x=np.array([score_logit(r['score']) for r in records]);y=np.array([r['target'] for r in records])
    def objective(theta):
        a,b=theta;p=expit(a*x+b)
        data=float(np.square(p-y).sum()) if loss=='brier' else float((np.logaddexp(0,a*x+b)-y*(a*x+b)).sum())
        return data+.5*penalty*((a-1)**2+b*b)
    result=minimize(objective,[1.,0.],method='L-BFGS-B',bounds=[(.25,4.),(-4.,4.)],options={'maxiter':1000,'ftol':1e-12})
    if not result.success or not math.isfinite(result.fun):raise ValueError('Calibration optimizer failed')
    return common|{'a':float(result.x[0]),'b':float(result.x[1]),'status':'fitted_prior_binary','objective':float(result.fun)}


def probability_at_zero(head, raw_score, ordinal_probability):
    x=score_logit(raw_score)
    score_logit(ordinal_probability)
    if head['kind']=='identity':return raw_score
    if head['kind']=='ordinal':return ordinal_probability
    if head['kind']=='mixture':return (1-head['weight'])*raw_score+head['weight']*ordinal_probability
    if head['kind']=='affine':
        cal=head['calibration']
        if cal['status']=='insufficient_events_identity':return raw_score
        return float(expit(cal['a']*x+cal['b']))
    raise ValueError('Unknown calibration family')


def forecast(model, sample, detector_score):
    validate(sample)
    base=model['ordinal_base']
    if base['kind']!='ordinal' or base['inputs']!='score' or base['a']<=0:raise ValueError('Frozen positive score-only ordinal base required')
    z=(score_logit(detector_score)-base['score_center'])/base['score_scale']
    old_at_zero=float(expit(base['b']*z+base['c']))
    p0=probability_at_zero(model['head'],detector_score,old_at_zero)
    # One anchor and one positive slope preserve the CDF order; no separate
    # threshold calibrators. Degenerate machine-score boundaries are flagged.
    slope=base['a']/base['temperature_scale']
    if p0 in [0.,1.]:probabilities=[p0]*4
    else:probabilities=[float(expit(math.log(p0)-math.log1p(-p0)+slope*q)) if q else p0 for q in [-3,-2,-1,0]]
    if any(a>b+1e-12 for a,b in zip(probabilities,probabilities[1:])):raise ValueError('CDF order violation')
    outside=not base['feature_range'][0]<=min(sample['gfs_temperature_c'])<=base['feature_range'][1]
    return {str(q):{'probability':p,'threshold_c':q,'calibration_status':'detector_score_boundary' if p0 in [0,1] else model['head'].get('status','research_prior_calibration'),
                    'raw_detector_score_at_zero':detector_score,'original_ordinal_probability_at_zero':old_at_zero,
                    'research_only':True,'extrapolation':outside,'crop_damage_probability':None,
                    'scope':'minimum probabilities only; original alarms/timing unchanged'}
            for q,p in zip([-3,-2,-1,0],probabilities)}
