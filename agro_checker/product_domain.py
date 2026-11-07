"""Product invariants; preserve raw research outputs when refusing a prediction."""
import math


def threshold_order_valid(forecasts):
    values=[(float(q),f.get('probability')) for q,f in forecasts.items()]
    if not values or any(p is None or not isinstance(p,(int,float)) or isinstance(p,bool) or not math.isfinite(p) or not 0<=p<=1 for _,p in values):return False
    values.sort()
    return all(a<=b+1e-10 for (_,a),(_,b) in zip(values,values[1:]))
