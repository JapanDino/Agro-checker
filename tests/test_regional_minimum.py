from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json
import unittest
from unittest.mock import patch
import numpy as np
from agro_checker.pipeline import ROOT
from agro_checker import regional_minimum as model
from agro_checker import regional_minimum_study as study
from agro_checker.product_data import sha


def night(day='2022-03-01',sid='a',cold=False):
    run=datetime.fromisoformat(day+'T06:00:00+00:00');t=[4-i/3 for i in range(14)]
    return {'id':sid,'role':'development','date':day,'year':int(day[:4]),'run_at_utc':run.isoformat(),'issued_at_utc':(run+timedelta(hours=9)).isoformat(),
            'times':[(run+timedelta(hours=i)).isoformat() for i in range(10,24)],
            'gfs':{'temperature':t,'dewpoint':[v-1 for v in t],'rh':[80.]*14,'u10':[1.]*14,'v10':[0.]*14,'cloud':[20.]*14},
            'target_temperature_c':[-1.]*14 if cold else [2.]*14}


class RegionalMinimumTests(unittest.TestCase):
    def setUp(self):
        self.samples=[night(f'2022-03-{i:02}',cold=i<8) for i in range(1,25)]
        self.spec={'scope':'regional','name':'test','kind':'ridge','alpha':100,'cold_weight':1,'balance':'date'}

    def test_inference_never_reads_targets_or_detector(self):
        fit=model.fit(self.samples,self.spec);n=night();a=model.predict(fit,n);del n['target_temperature_c']
        n['future_detector']={'frost':True};self.assertEqual(a,model.predict(fit,n));self.assertTrue(fit['detector_unchanged'])

    def test_future_or_spatial_holdout_cannot_enter_fit(self):
        for y in [2024,2025,2026]:
            samples=deepcopy(self.samples);samples[0]['year']=y
            with self.assertRaises(ValueError):model.fit(samples,self.spec)
        samples=deepcopy(self.samples);samples[0]['role']='heldout_site'
        with self.assertRaises(ValueError):model.fit(samples,self.spec)

    def test_duplicate_site_night_cannot_inflate_sample_size(self):
        with self.assertRaises(ValueError):model.fit(self.samples+[self.samples[0]],self.spec)

    def test_correlated_sites_have_one_total_date_weight(self):
        samples=[night(sid='a'),night(sid='b'),night('2022-03-02',sid='a')];w,_=model.weights(samples,1,'date')
        np.testing.assert_allclose(w,[.5,.5,1]);self.assertAlmostEqual(w.sum(),2)

    def test_episode_weighting_groups_only_training_cold_dates(self):
        samples=[night('2022-03-01',cold=True),night('2022-03-02',cold=True),night('2022-03-06',cold=True),night('2022-03-09')]
        w,ids=model.weights(samples,1,'episode');self.assertEqual(len(set(ids.values())),2);self.assertAlmostEqual(w[0]+w[1],w[2]);self.assertAlmostEqual(w.sum(),4)

    def test_missing_targets_not_filled(self):
        samples=deepcopy(self.samples);samples[0]['target_temperature_c'][2]=None
        fit=model.fit(samples,self.spec);self.assertEqual(fit['training_nights'],23);self.assertEqual(fit['excluded_nights'],1)

    def test_invalid_fields_and_mixed_horizons_rejected(self):
        for key,value in [('temperature',True),('rh',111),('cloud',None),('u10',float('nan'))]:
            n=night();n['gfs'][key][1]=value
            with self.assertRaises((ValueError,TypeError)):model.predict(model.fit(self.samples,self.spec),n)
        n=night();n['times'][4]=n['times'][3]
        with self.assertRaises(ValueError):model.predict(model.fit(self.samples,self.spec),n)

    def test_point_crossing_metrics_not_detector_alerts(self):
        rs=[{'id':'a','date':'2023-03-01','actual':-1,'point':1},{'id':'a','date':'2023-03-02','actual':1,'point':-1}]
        s=model.score(rs);self.assertEqual(s['false_alarms'],1);self.assertEqual(s['misses'],1);self.assertIn('not original detector',s['crossing_scope'])

    def test_bootstrap_resamples_dates_together_for_all_sites(self):
        rs=[{'id':sid,'date':f'2023-03-{i:02}','actual':1,'point':2} for i in range(1,25) for sid in ['a','b']]
        baseline=[{**r,'point':3} for r in rs];b=model.paired_blocks(rs,baseline,100)
        self.assertEqual(b['interval_95_c'],[-1,-1]);self.assertLess(b['calendar_week_blocks'],len(rs))


class FrozenRegionalTests(unittest.TestCase):
    def test_selection_loads_only_prior_development_targets(self):
        if not study.LOCK.exists():self.skipTest('Run selection first')
        load=study.data.load;rows=study.load_rows;seen=[]
        def checked(partition):
            self.assertEqual(partition,'development');seen.append(partition);return load(partition)
        def checked_rows(directory,years,config=None):
            self.assertTrue(set(years)<={2022,2023,2024});return rows(directory,years,config)
        with patch.object(study.data,'load',side_effect=checked),patch.object(study,'load_rows',side_effect=checked_rows):s=study.selection()
        self.assertEqual(seen,['development']);self.assertFalse(s['heldout_targets_loaded_in_selection'])
        for name,file in s['models'].items():
            m=json.loads((study.DIRECTORY/file).read_text(encoding='utf-8'));self.assertEqual(m['training_years'],[2022,2023])
            holdout={site['id'] for site in s['dataset_meta']['sites'] if site['role']=='heldout_site'}
            self.assertFalse(set(m['training_sites'])&holdout)

    def test_control_points_and_original_detector_fully_preserved(self):
        path=ROOT/'docs/REGIONAL_MINIMUM_SUMMARY.json'
        if not path.exists():self.skipTest('Run frozen controls')
        s=json.loads(path.read_text(encoding='utf-8'));file=study.data.DIRECTORY/'control-forecasts.json'
        self.assertEqual(sha(study.LOCK),s['selection_sha256']);self.assertEqual(sha(file),s['forecast_sha256'])
        old={r['date']:r for r in json.loads((ROOT/'data/processed/cold-exposure/night-forecasts.json').read_text(encoding='utf-8'))}
        points={r['date']:r for r in json.loads((ROOT/'data/processed/minimum-study/night-forecasts.json').read_text(encoding='utf-8'))}
        frames=json.loads(file.read_text(encoding='utf-8'));self.assertEqual(len(frames),184)
        for f in frames:
            self.assertEqual(f['legacy_detector'],old[f['date']]['legacy_detector']);self.assertEqual(f['points']['legacy'],points[f['date']]['selected']['point_c'])
            self.assertFalse(f['point_crossings_are_alerts'])


if __name__=='__main__':unittest.main()
