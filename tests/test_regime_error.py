from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from agro_checker.pipeline import ROOT
from agro_checker.scenario_study import nights
from agro_checker import regime_error_v2 as model
from agro_checker.regime_error_study_v2 import protocol,choose,selection
from agro_checker import no_cloud_error as original
from agro_checker.conditional_error import calibrate_shape
from test_minimum_model import season


class RegimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p=protocol(ROOT/'config/regime_error_v2.json');cls.policy=cls.p['policies'][4]
        cls.samples=nights(season(2021)+season(2022)+season(2023))
        cls.point=model.fit_point(cls.samples,cls.policy,cls.p,2023)
        cls.records,cls.audit=model.crossfit(cls.samples,cls.policy,cls.p,2023)
        cls.spec=cls.p['families'][1];cls.head=model.fit_head(cls.records,cls.audit,cls.spec,cls.policy,cls.p,2023)
        cls.valid=nights(season(2024));cls.pool=model.pool(cls.point,cls.head,cls.valid,cls.p,2024);cls.cal=calibrate_shape(cls.pool,cls.p)

    def test_version_registry_uses_run_time_boundaries_and_rejects_future(self):
        self.assertEqual(model.regime('2021-03-22T11:59:00+00:00',self.p),'pre_v16')
        self.assertEqual(model.regime('2021-03-22T12:00:00+00:00',self.p),'v16_before_2022_update')
        self.assertEqual(model.regime('2022-11-29T06:00:00+00:00',self.p),'v16_before_2022_update')
        self.assertEqual(model.regime('2022-11-29T12:00:00+00:00',self.p),'after_2022_update')
        for stamp in ['2021-03-01T06:00:00','2027-03-01T06:00:00+00:00']:
            with self.assertRaises(ValueError):model.regime(stamp,self.p)

    def test_age_version_weights_normalized_and_anchor_never_control_year(self):
        dates=['2021-03-01','2021-04-01','2022-04-01','2023-04-01']
        w=model.weights(dates,self.policy,self.p,2023);raw=np.array([.0625,.125,.25,1.])
        np.testing.assert_allclose(w,raw/raw.mean());self.assertAlmostEqual(w.sum(),4.)
        self.assertLess(model.audit_weights(dates,w,self.p)['effective_nights'],4)
        for anchor in [2024,2025,2026]:
            with self.assertRaises(ValueError):model.weights(dates,self.policy,self.p,anchor)
        with self.assertRaises(ValueError):model.weights(['2024-03-01'],self.policy,self.p,2023)

    def test_constant_version_column_never_uses_roundoff_variance(self):
        x=np.array([[1.,3.],[1.,7.],[1.,8.],[1.,11.]])
        w=np.array([.12,.17,.29,.51]);mean,scale=model.weighted_scale(x,w)
        self.assertEqual(scale[0],1.)
        self.assertTrue(np.isfinite(((np.array([0.,5.])-mean)/scale)).all())
        self.assertLess(abs((0-mean[0])/scale[0]),2.)

    def test_offset_oof_block_missing_regime_produces_finite_reasonable_errors(self):
        policy=self.p['policies'][-1];samples=nights(season(2021)+season(2022))
        records,audit=model.crossfit(samples,policy,self.p,2022)
        self.assertTrue(all(abs(r['error_c'])<100 for r in records))
        self.assertTrue(all(not set(b['fit_dates'])&set(b['held_dates']) for b in audit))

    def test_weight_and_version_features_ignore_future_targets(self):
        n=deepcopy(self.valid[0]);old=model.inputs(n,self.p['policies'][-1],self.p)
        n['target']={'temperature':[999]*14}
        for r in n['rows']:r['target']={'temperature':999}
        np.testing.assert_array_equal(old,model.inputs(n,self.p['policies'][-1],self.p))
        n['rows'][0]['features_weather']=n['rows'][0]['features_weather'][:5]
        np.testing.assert_array_equal(old,model.inputs(n,self.p['policies'][-1],self.p))

    def test_uniform_weight_point_and_residual_heads_reproduce_original(self):
        policy=self.p['policies'][0];point=model.fit_point(self.samples,policy,self.p,2023);ref=original.fit_point(self.samples)
        for key in ['mean','scale','coef','intercept']:np.testing.assert_allclose(point[key],ref[key],rtol=0,atol=1e-12)
        records,audit=model.crossfit(self.samples,policy,self.p,2023);rr,aa=original.crossfit(self.samples)
        head=model.fit_head(records,audit,self.spec,policy,self.p,2023);other=original.fit_head(rr,aa,self.spec)
        np.testing.assert_allclose(head['heads'][0]['coef'],other['heads'][0]['coef'],rtol=0,atol=1e-12)
        np.testing.assert_allclose(model.shape(head,self.valid[0],self.p)['lower'],original.shape(other,self.valid[0],self.p)['lower'],rtol=0,atol=1e-12)

    def test_block_oof_with_weights_excludes_all_held_labels(self):
        changed=deepcopy(self.samples);held=set(self.audit[0]['held_dates'])
        for n in changed:
            if n['date'] in held:n['target']['temperature']=[t+100 for t in n['target']['temperature']]
        records,audit=model.crossfit(changed,self.policy,self.p,2023);old={r['date']:r for r in self.records}
        for r in records:
            if r['date'] in held:self.assertAlmostEqual(r['error_c']-old[r['date']]['error_c'],100)
        for b in audit:self.assertFalse(set(b['fit_dates'])&set(b['held_dates']))

    def test_control_training_and_calibration_overlap_rejected(self):
        for year in [2024,2025,2026]:
            with self.assertRaises(ValueError):model.fit_point(nights(season(year)),self.policy,self.p,2023)
            with self.assertRaises(ValueError):model.crossfit(nights(season(year)),self.policy,self.p,2023)
        for year in [2025,2026]:
            with self.assertRaises(ValueError):model.pool(self.point,self.head,nights(season(year)),self.p,year)
        head=deepcopy(self.head);head['fit_dates'].append(self.valid[0]['date'])
        with self.assertRaises(ValueError):model.pool(self.point,head,self.valid,self.p,2024)
        audit=deepcopy(self.audit);audit[0]['fit_dates'].append(audit[0]['held_dates'][0])
        with self.assertRaises(ValueError):model.fit_head(self.records,audit,self.spec,self.policy,self.p,2023)

    def test_unweighted_calibration_loo_and_missing_truth(self):
        pool=deepcopy(self.pool);date=pool[0]['date'];pool[0]['score']=1e9
        self.assertEqual(calibrate_shape(pool,self.p,date),calibrate_shape(self.pool,self.p,date))
        n=deepcopy(self.valid);n[0]['target']['temperature'][0]=None
        self.assertEqual(len(model.pool(self.point,self.head,n,self.p,2024)),39)
        with self.assertRaises(ValueError):calibrate_shape(pool[:10],self.p)

    def test_json_inference_ignores_truth_and_never_claims_damage_or_coverage(self):
        n=deepcopy(self.valid[0]);r=model.forecast(self.point,self.head,self.cal,n,self.p)
        n['target']={'temperature':[999]*14}
        self.assertEqual(r,model.forecast(json.loads(json.dumps(self.point)),json.loads(json.dumps(self.head)),self.cal,n,self.p))
        self.assertIsNone(r['probability_of_crop_damage']);self.assertFalse(r['coverage_guaranteed'])

    def test_unseen_regime_missing_input_and_site_guards(self):
        point=deepcopy(self.point);point['training_regimes']=['pre_v16','v16_before_2022_update']
        self.assertEqual(model.safe_prediction(point,self.head,self.cal,self.valid[0],self.p,'34929')['status'],'unseen_gfs_regime')
        self.assertIsNone(model.safe_prediction(self.point,self.head,self.cal,self.valid[0],self.p,'orchard')['prediction'])
        n=deepcopy(self.valid[0]);n['rows'][0]['features_weather'][4]=None
        self.assertIsNone(model.safe_prediction(self.point,self.head,self.cal,n,self.p,'34929')['prediction'])

    def test_weighted_tree_json_matches_sklearn(self):
        from sklearn.ensemble import GradientBoostingRegressor
        spec=self.p['families'][-1];head=model.fit_head(self.records,self.audit,spec,self.policy,self.p,2023)
        x=np.array([r['x'] for r in self.records]);z=(x-np.array(head['mean']))/np.array(head['scale']);y=[r['error_c'] for r in self.records]
        w=model.weights([r['date'] for r in self.records],self.policy,self.p,2023);pred=[]
        for q in [.05,.95]:
            est=GradientBoostingRegressor(loss='quantile',alpha=q,n_estimators=spec['estimators'],max_depth=spec['depth'],
                min_samples_leaf=spec['minimum_leaf'],learning_rate=spec['learning_rate'],random_state=spec['seed']).fit(z,y,sample_weight=w)
            pred.append(est.predict(z[:1])[0])
        sample=next(n for n in self.samples if n['date']==self.records[0]['date']);s=model.shape(json.loads(json.dumps(head)),sample,self.p)
        self.assertAlmostEqual(s['lower'],min(*pred,0.),places=10);self.assertAlmostEqual(s['upper'],max(*pred,0.),places=10)

    def test_width_and_point_gates_prevent_promoting_wide_or_inaccurate_candidate(self):
        base={'covered':80,'cold_nights':7,'cold_covered':5,'cold_interval_score':8.,'warm_interval_score':6.,'mean_width_c':6.,'warm_mean_width_c':6.,'mae_c':1.}
        scores={s['name']:deepcopy(base) for s in self.p['candidates']};name='age_1__quantile_trees'
        scores[name].update(cold_covered=7,mean_width_c=20.,warm_mean_width_c=20.,mae_c=2.)
        winner,diagnostic,pair,gates=choose(scores,self.p)
        self.assertEqual(winner,'legacy_symmetric');self.assertFalse(gates[name]['point_mae_not_higher']);self.assertFalse(gates[name]['overall_width_within_limit'])
        self.assertEqual(pair,'uniform__'+diagnostic.split('__')[1])

    def test_freeze_rejects_source_or_protocol_change_before_loading_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp)/'data';data.mkdir();directory=data.parent/'models/research-regime-errors-v2';directory.mkdir(parents=True)
            (directory/'selection-before-controls.json').write_text(json.dumps({'protocol_sha256':'wrong'}),encoding='utf-8')
            with patch('agro_checker.regime_error_study_v2.parent_assets',return_value={'hashes':{}}),patch('agro_checker.regime_error_study_v2.prior_samples') as loader:
                with self.assertRaisesRegex(ValueError,'Frozen'):selection(ROOT/'config/regime_error_v2.json',data)
                loader.assert_not_called()

    def test_prior_loader_reads_only_training_validation_years(self):
        from agro_checker.regime_error_study_v2 import prior_samples
        from agro_checker.extended_archive import archive_config
        main=season(2022)+season(2023)+season(2024);extra=season(2021,92)
        early=[r for r in extra if r['date']<'2021-03-23'];late=[r for r in extra if r['date']>='2021-03-23'];calls=[]
        def loader(directory,years,config=None):
            calls.append(years);self.assertIn(years,[[2022,2023,2024],[2021]])
            return (late,{'year':2021}) if years==[2021] else (main,{'years':years})
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp);(directory/'archive-2021').mkdir()
            (directory/'archive-2021/source-config.json').write_text(json.dumps(archive_config()),encoding='utf-8')
            with patch('agro_checker.regime_error_study_v2.load_rows',side_effect=loader),patch('agro_checker.regime_error_study_v2.early_rows',return_value=(early,{'year':2021})):
                base,added,provenance=prior_samples(directory)
        self.assertEqual(calls,[[2022,2023,2024],[2021]]);self.assertEqual(len(added),92)
        self.assertTrue(all(n['year']<=2024 for n in base+added))


if __name__=='__main__':unittest.main()
