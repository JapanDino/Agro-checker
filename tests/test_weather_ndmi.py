"""Technical fixtures only, not measured farm weather or agronomist observations."""
from datetime import datetime,timedelta
from copy import deepcopy
from unittest.mock import patch
import unittest
import numpy as np
from agro_checker import sector_weather_data as data
from agro_checker import weather_ndmi_experiment as model


def row():return dict(fid='R01',issue='2024-08-01',target='2024-08-08',x=[.5,.3,0,-.01,7,0,1,5,2],
    y=[.5,-.1],residual=[0,-.4],weather=[1,2,3,4,.2,.25,-.01,.18])


class WeatherFeatureTests(unittest.TestCase):
    def hourly(self):
        start=datetime(2024,1,1);n=31*24
        return dict(base=start,time=[(start+timedelta(hours=i)).isoformat(timespec='minutes') for i in range(n)],rain=np.ones(n),soil=np.full(n,.2))

    def test_cutoff_and_future_weather_not_input(self):
        h=self.hourly();a,meta=data.weather_vector(h,'2024-01-20')
        end=(20-1-2)*24;h['rain'][end:]=999;h['soil'][end:]=.9
        b,_=data.weather_vector(h,'2024-01-20')
        self.assertEqual(a,b);self.assertEqual(a[:4],[72.,168.,336.,14.])
        self.assertLess(meta['newest_valid_utc'],meta['cutoff_utc'])

    def test_no_weather_null_to_zero(self):
        h=self.hourly();h['rain'][400]=np.nan
        value,audit=data.weather_vector(h,'2024-01-20')
        self.assertEqual(value,[None]*8);self.assertFalse(audit['complete'])

    def test_history_window_must_be_complete(self):
        values,audit=data.weather_vector(self.hourly(),'2024-01-10')
        self.assertFalse(audit['complete']);self.assertEqual(values,[None]*8)

    def test_qc_rejects_bad_units_and_time_axis(self):
        obj=dict(utc_offset_seconds=0,hourly_units={'precipitation':'mm','soil_moisture_0_to_10cm':'m³/m³'},
            hourly=dict(time=[f'2024-01-01T{i:02d}:00' for i in range(24)],precipitation=[0]*24,soil_moisture_0_to_10cm=[.2]*24))
        self.assertEqual(data.qc(obj,'2024-01-01','2024-01-01')['precipitation']['valid'],24)
        bad=deepcopy(obj);bad['hourly']['time'][1]=bad['hourly']['time'][0]
        with self.assertRaises(ValueError):data.qc(bad,'2024-01-01','2024-01-01')
        bad=deepcopy(obj);bad['hourly_units']['soil_moisture_0_to_10cm']='%'
        with self.assertRaises(ValueError):data.qc(bad,'2024-01-01','2024-01-01')

    def test_2026_weather_locked_until_choice(self):
        with patch.object(data,'D',data.ROOT/'tmp/nonexistent-weather-test'):
            with self.assertRaisesRegex(ValueError,'after choice freeze'):data.collect_year(2026)


class WeatherModelTests(unittest.TestCase):
    def test_ablations_and_no_future_labels_in_matrix(self):
        a=[row()];b=deepcopy(a);b[0].update(y=[1,1],residual=[999,999],target='2026-01-01')
        for family,dim in [('none',11),('rain',15),('soil',15),('both',19)]:
            self.assertEqual(model.matrix(a,family).shape,(1,dim))
            np.testing.assert_array_equal(model.matrix(a,family),model.matrix(b,family))

    def test_weather_missing_uses_explicit_envelope(self):
        r=row();r['weather']=[None]*8
        tail=dict(family='both',style='quantile',fallback=dict(constant=[-.3,.1]),models=[dict(constant=-.9),dict(constant=.8)])
        np.testing.assert_array_equal(model.raw(tail,[r]),[[-.3,.1]])

    def test_fit_rejects_test_year_and_reserved_fields(self):
        for bad in [dict(row(),issue='2026-08-01'),dict(row(),fid='R25')]:
            with self.assertRaises(ValueError):model.fit_tail([bad],'rain','quantile',{},model.D,.2)

    def test_bad_fold_not_hidden_by_pooled_score(self):
        q=dict(coverage=1.,decline_coverage=1.,mean_width=.4,weighted_score=.5)
        table={'legacy':dict(pooled=deepcopy(q),folds={'a':deepcopy(q)})}
        for family in model.FAMILIES:
            table[family+'__envelope']=dict(pooled=deepcopy(q),folds={'a':deepcopy(q)})
        table['rain__envelope']['folds']['a']['decline_coverage']=.5
        choices,primary=model.choose(table,dict(coverage_floor=.9,decline_floor=.9,width_limit=1.1))
        self.assertFalse(choices['rain']['passed_2024']);self.assertEqual(primary,'none__envelope')

    def test_bounds_keep_supplied_point_and_inputs(self):
        rr=[row()];before=deepcopy(rr);point=np.array([[.5,.3]])
        tail=dict(family='none',style='envelope',fallback=dict(constant=[-.3,.1]))
        low,high=model.bounds(tail,dict(adjustment=[0,0]),rr,point)
        self.assertEqual(rr,before);np.testing.assert_array_equal(point,[[.5,.3]])
        np.testing.assert_allclose([low[0],high[0]],[0,.4])


if __name__=='__main__':unittest.main()
