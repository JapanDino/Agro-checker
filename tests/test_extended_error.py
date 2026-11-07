from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from agro_checker.pipeline import ROOT,UTC
from agro_checker.scenario_study import nights
from agro_checker import no_cloud_error as nc
from agro_checker.conditional_error import calibrate_shape
from agro_checker.extended_archive import historical_url
from agro_checker.gfs_covariates import field_range
from agro_checker.extended_error_study import protocol,choose,selection
from test_minimum_model import season


class ExtendedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p=protocol(ROOT/'config/extended_error.json')
        cls.samples=nights(season(2021)+season(2022))
        cls.point=nc.fit_point(cls.samples)
        cls.records,cls.audit=nc.crossfit(cls.samples)
        cls.spec=next(s for s in cls.p['candidates'] if s['name']=='extended__tails_ridge_10')
        cls.head=nc.fit_head(cls.records,cls.audit,cls.spec)
        cls.valid=nights(season(2024))
        cls.pool=nc.pool(cls.point,cls.head,cls.valid,cls.p,2024)
        cls.cal=calibrate_shape(cls.pool,cls.p)

    def test_historical_layout_and_average_cloud_never_substituted(self):
        self.assertNotIn('/atmos/',historical_url(datetime(2021,3,22,6,tzinfo=UTC),10))
        self.assertIn('/atmos/',historical_url(datetime(2021,3,23,6,tzinfo=UTC),10))
        for y in [2020,2022]:
            with self.assertRaises(ValueError):historical_url(datetime(y,3,1,6,tzinfo=UTC),10)
        index=b'1:0:d=2021030106:TCDC:entire atmosphere:6-10 hour ave fcst:\n2:100:d=2021030106:TMP:surface:10 hour fcst:\n'
        with self.assertRaises(ValueError):field_range(index,datetime(2021,3,1,6,tzinfo=UTC),10,'cloud')

    def test_no_cloud_input_requires_no_placeholder_and_rejects_missing_wind(self):
        n=deepcopy(self.valid[0]);original=nc.inputs(n).tolist()
        for r in n['rows']:r['features_weather']=r['features_weather'][:5]
        self.assertEqual(len(nc.inputs(n)),13);self.assertEqual(original,nc.inputs(n).tolist())
        for r in n['rows']:r['features_weather'].append(float('nan'))
        self.assertEqual(original,nc.inputs(n).tolist())
        n['rows'][0]['features_weather'][4]=None
        with self.assertRaises(ValueError):nc.inputs(n)

    def test_oof_residuals_exclude_entire_held_block(self):
        changed=deepcopy(self.samples);held=set(self.audit[0]['held_dates'])
        for n in changed:
            if n['date'] in held:n['target']['temperature']=[t+100 for t in n['target']['temperature']]
        records,audit=nc.crossfit(changed);old={r['date']:r for r in self.records}
        for r in records:
            if r['date'] in held:self.assertAlmostEqual(r['error_c']-old[r['date']]['error_c'],100)
        for b in audit:self.assertFalse(set(b['fit_dates'])&set(b['held_dates']))

    def test_fitting_and_calibration_reject_control_labels_and_overlap(self):
        for y in [2024,2025,2026]:
            with self.assertRaises(ValueError):nc.fit_point(nights(season(y)))
            with self.assertRaises(ValueError):nc.crossfit(nights(season(y)))
        for y in [2025,2026]:
            with self.assertRaises(ValueError):nc.pool(self.point,self.head,nights(season(y)),self.p,y)
        model=deepcopy(self.point);model['training_dates'].append(self.valid[0]['date'])
        with self.assertRaises(ValueError):nc.pool(model,self.head,self.valid,self.p,2024)
        records=deepcopy(self.records);records[0]['date']='2025-03-01'
        with self.assertRaises(ValueError):nc.fit_head(records,self.audit,self.spec)

    def test_json_roundtrip_and_future_truth_do_not_change_output(self):
        n=deepcopy(self.valid[0]);old=deepcopy(n);r=nc.forecast(self.point,self.head,self.cal,n,self.p)
        self.assertEqual(old,n);n['target']={'temperature':[999]*14}
        self.assertEqual(r,nc.forecast(json.loads(json.dumps(self.point)),json.loads(json.dumps(self.head)),self.cal,n,self.p))
        self.assertFalse(r['coverage_guaranteed']);self.assertIsNone(r['probability_of_crop_damage'])

    def test_loo_missing_truth_and_insufficient_quantiles(self):
        pool=deepcopy(self.pool);date=pool[0]['date'];pool[0]['score']=1e9
        self.assertEqual(calibrate_shape(pool,self.p,date),calibrate_shape(self.pool,self.p,date))
        samples=deepcopy(self.valid);samples[0]['target']['temperature'][0]=None
        self.assertEqual(len(nc.pool(self.point,self.head,samples,self.p,2024)),39)
        with self.assertRaises(ValueError):calibrate_shape(pool[:10],self.p)

    def test_point_mae_and_width_gates_apply_to_promotion_not_diagnostic(self):
        base={'covered':80,'cold_nights':7,'cold_covered':5,'cold_interval_score':8.,'warm_interval_score':6.,
              'mean_width_c':6.,'warm_mean_width_c':6.,'mae_c':1.}
        scores={s['name']:deepcopy(base) for s in self.p['candidates']}
        for name in scores:
            if name.startswith('extended__'):scores[name].update(cold_covered=7,mae_c=2.)
        winner,expanded,pair,gates=choose(scores,self.p)
        self.assertEqual(winner,'legacy_symmetric');self.assertTrue(expanded.startswith('extended__'))
        self.assertEqual(pair,expanded.replace('extended__','recent__'));self.assertFalse(gates[expanded]['point_mae_not_higher'])
        scores[expanded].update(mae_c=1.,mean_width_c=20.,warm_mean_width_c=20.)
        self.assertFalse(choose(scores,self.p)[3][expanded]['overall_width_within_limit'])

    def test_domain_and_ood_guards(self):
        self.assertIsNone(nc.safe_prediction(self.point,self.head,self.cal,self.valid[0],self.p,'orchard')['prediction'])
        n=deepcopy(self.valid[0])
        for r in n['rows']:r['features_weather'][4]=100.
        self.assertEqual(nc.safe_prediction(self.point,self.head,self.cal,n,self.p,'34929')['status'],'extrapolation')
        n['rows'][0]['features_weather'][4]=None
        self.assertIsNone(nc.safe_prediction(self.point,self.head,self.cal,n,self.p,'34929')['prediction'])

    def test_quantile_crossing_is_sorted_and_contains_point_without_negative_correction(self):
        head=deepcopy(self.head);head['spec']['mode']='quantile'
        head['heads']=[{'coef':[0]*13,'intercept':3.},{'coef':[0]*13,'intercept':-2.}]
        r=nc.forecast(self.point,head,{'value':1.},self.valid[0],self.p)
        self.assertAlmostEqual(r['upper_c']-r['lower_c'],7.)
        self.assertLess(r['lower_c'],r['point_c']);self.assertGreater(r['upper_c'],r['point_c'])
        with self.assertRaises(ValueError):nc.forecast(self.point,head,{'value':-1.},self.valid[0],self.p)

    def test_extended_tree_json_matches_sklearn_predictor(self):
        import numpy as np
        from sklearn.ensemble import GradientBoostingRegressor
        spec=next(s for s in self.p['candidates'] if s['name']=='extended__quantile_trees')
        head=nc.fit_head(self.records,self.audit,spec);x=np.asarray([r['x'] for r in self.records])
        z=(x-np.array(head['mean']))/np.array(head['scale']);y=[r['error_c'] for r in self.records]
        predictions=[]
        for q in [.05,.95]:
            est=GradientBoostingRegressor(loss='quantile',alpha=q,n_estimators=spec['estimators'],max_depth=spec['depth'],
                min_samples_leaf=spec['minimum_leaf'],learning_rate=spec['learning_rate'],random_state=spec['seed']).fit(z,y)
            predictions.append(est.predict(z[:1])[0])
        sample=next(n for n in self.samples if n['date']==self.records[0]['date'])
        shape=nc.shape(json.loads(json.dumps(head)),sample,self.p)
        self.assertAlmostEqual(shape['lower'],min(*predictions,0.),places=10)
        self.assertAlmostEqual(shape['upper'],max(*predictions,0.),places=10)

    def test_frozen_selection_verifies_hashes_before_loading_any_observations(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp)/'data';data.mkdir();directory=data.parent/'models/research-extended-errors';directory.mkdir(parents=True)
            (directory/'selection-before-controls.json').write_text(json.dumps({'protocol_sha256':'wrong'}),encoding='utf-8')
            with patch('agro_checker.extended_error_study.parent_assets',return_value={'hashes':{}}),patch('agro_checker.extended_error_study.load_rows') as loader:
                with self.assertRaisesRegex(ValueError,'Frozen'):selection(ROOT/'config/extended_error.json',data)
                loader.assert_not_called()

    def test_full_selector_reads_only_prior_years_and_reuses_immutable_models(self):
        from agro_checker.conditional_error_study import select as original_select
        from agro_checker.minimum_model import fit_minimum
        from agro_checker.extended_archive import archive_config
        main_rows=season(2022)+season(2023)+season(2024);extra=season(2021,92)
        early=[r for r in extra if r['date']<'2021-03-23'];late=[r for r in extra if r['date']>='2021-03-23']
        parent={'point_model':fit_minimum({'name':'huber_1','kind':'huber','alpha':1.},nights(season(2022)+season(2023))), 'hashes':{'test':'fixed'}}
        calls=[]
        def loader(directory,years,config=None):
            calls.append(years)
            self.assertIn(years,[[2022,2023,2024],[2021]])
            return (late,{'test':'extra'}) if years==[2021] else (main_rows,{'test':'prior'})
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp)/'data';(data/'archive-2021').mkdir(parents=True)
            (data/'archive-2021/source-config.json').write_text(json.dumps(archive_config()),encoding='utf-8')
            with patch('agro_checker.conditional_error_study.parent_assets',return_value=parent),patch('agro_checker.conditional_error_study.load_rows',side_effect=loader):
                original_select(ROOT/'config/conditional_error.json',data)
            with patch('agro_checker.extended_error_study.parent_assets',return_value=parent),patch('agro_checker.extended_error_study.load_rows',side_effect=loader),patch('agro_checker.extended_error_study.early_rows',return_value=(early,{'test':'early'})):
                s=selection(ROOT/'config/extended_error.json',data)
                self.assertEqual(s['observations_loaded_years'],[2021,2022,2023,2024])
                directory=data.parent/'models/research-extended-errors';file=directory/s['models'][s['winner']]['head'];before=file.read_bytes()
                self.assertEqual(s,selection(ROOT/'config/extended_error.json',data));self.assertEqual(before,file.read_bytes())
                file.write_text('{}',encoding='utf-8')
                with self.assertRaisesRegex(ValueError,'model'):selection(ROOT/'config/extended_error.json',data)
        self.assertTrue(all(2025 not in y and 2026 not in y for y in calls))


if __name__=='__main__':unittest.main()
