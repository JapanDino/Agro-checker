from copy import deepcopy
from datetime import datetime,timedelta,timezone
import unittest
from agro_checker.product_advice import preparation,wet_bulb,local_update,daily_eto,weather_work_windows,economic_scenario


class AdviceTests(unittest.TestCase):
    def setUp(self):
        self.start=datetime(2026,3,1,16,tzinfo=timezone.utc)
        self.hours=[{'valid_at_utc':(self.start+timedelta(hours=i)).isoformat(),'temperature_c':5-i*.5} for i in range(14)]
        self.sensor={'id':'s1','measurement_type':'air_2m','height_m':2,'calibration_status':'checked'}
        self.asof=(self.start+timedelta(hours=2)).isoformat()
        self.measurements=[{'id':str(i),'effective_status':'accepted','recorded_at_utc':(self.start+timedelta(hours=i)).isoformat(),
          'payload':{'observed_at_utc':(self.start+timedelta(hours=i)).isoformat(),'sensor_id':'s1','temperature_c':6-i*.6}} for i in range(3)]

    def test_preparation_uses_early_edge_and_unknown_stays_unknown(self):
        times=[h['valid_at_utc'] for h in self.hours];issue=(self.start-timedelta(hours=1)).isoformat()
        f={'early_onset_index':4,'onset_left_censored':False}
        self.assertEqual(preparation(f,times,issue,2,issue)['slack_hours'],3)
        self.assertEqual(preparation(f,times,self.asof,3,issue)['status'],'late')
        f['onset_left_censored']=True;self.assertIsNone(preparation(f,times,issue,2,issue)['slack_hours'])

    def test_wet_bulb_guard_and_saturation(self):
        self.assertEqual(wet_bulb(-3,100)['value_c'],-3)
        self.assertIsNone(wet_bulb(-30,80)['value_c']);self.assertIsNone(wet_bulb(-10,10)['value_c'])
        self.assertAlmostEqual(wet_bulb(20,50)['value_c'],13.7,delta=.15)
        with self.assertRaises(ValueError):wet_bulb(20,110)

    def test_update_never_rewrites_original_and_uses_future_hours_only(self):
        before=deepcopy(self.hours);r=local_update(self.hours,self.measurements,self.asof,self.sensor,'not_used')
        self.assertEqual(r['status'],'shadow_only');self.assertEqual(self.hours,before)
        self.assertTrue(all(h['valid_at_utc']>self.asof for h in r['hours']));self.assertEqual(len(r['hours']),6)
        self.assertLess(r['hours'][-1]['temperature_c']-before[8]['temperature_c'],r['hours'][0]['temperature_c']-before[3]['temperature_c'])

    def test_future_record_and_future_qc_review_cannot_enter_update(self):
        m=deepcopy(self.measurements);m[0]['recorded_at_utc']=(self.start+timedelta(hours=4)).isoformat()
        self.assertIsNone(local_update(self.hours,m,self.asof,self.sensor,'not_used')['hours'])
        m=deepcopy(self.measurements);m[0]['reviewed_at_utc']=(self.start+timedelta(hours=4)).isoformat()
        self.assertIsNone(local_update(self.hours,m,self.asof,self.sensor,'not_used')['hours'])

    def test_protection_and_unverified_sensor_block_update(self):
        for status in ['unknown','used']:self.assertIsNone(local_update(self.hours,self.measurements,self.asof,self.sensor,status)['hours'])
        for key,value in [('height_m',1),('measurement_type','canopy'),('calibration_status','unknown')]:
            s={**self.sensor,key:value};self.assertIsNone(local_update(self.hours,self.measurements,self.asof,s,'not_used')['hours'])

    def test_eto_cannot_use_partial_night_and_never_returns_irrigation_dose(self):
        inp={'period_hours':24,'t_mean_c':20,'wind_2m_ms':2,'net_radiation_mj_m2_day':10,'pressure_kpa':101.3,
          'saturation_vapour_pressure_kpa':2.34,'actual_vapour_pressure_kpa':1.4}
        r=daily_eto(inp);self.assertTrue(2<r['eto_mm_day']<6);self.assertIsNone(r['irrigation_dose_mm'])
        inp['period_hours']=14
        with self.assertRaises(ValueError):daily_eto(inp)

    def test_missing_rain_or_gust_cannot_look_like_good_work_window(self):
        h={**self.hours[0],'wind_ms':1}
        self.assertIsNone(weather_work_windows([h],{'max_wind_ms':3,'max_gust_ms':5,'max_precipitation_mm':0})[0]['suitable'])

    def test_economics_never_claims_confirmed_savings(self):
        inp={'protection_cost_rub':5000,'season_price_rub':10000}
        self.assertIsNone(economic_scenario(inp)['estimated_season_net_rub'])
        r=economic_scenario({**inp,'damaging_event_probability':.2,'avoided_loss_if_event_rub':100000,'decisions_per_season':3})
        self.assertEqual(r['estimated_season_net_rub'],35000);self.assertIsNone(r['confirmed_savings_rub'])


if __name__=='__main__':unittest.main()
