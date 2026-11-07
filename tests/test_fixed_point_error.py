from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from agro_checker.pipeline import ROOT
from agro_checker.scenario_study import nights
from agro_checker.minimum_model import fit_minimum,point_minimum
from agro_checker.conditional_error import calibrate_shape,fit_head as old_head,crossfit_residuals,forecast as old_forecast,calibration_pool
from agro_checker import fixed_point_error as model
from agro_checker.fixed_point_error_study import protocol,choose,selection,prior_samples
from agro_checker.extended_archive import archive_config
from test_minimum_model import season


class FixedPointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p=protocol(ROOT/'config/fixed_point_error.json');cls.policy=cls.p['policies'][4]
        cls.samples=nights(season(2022)+season(2023));cls.extra=[n for n in nights(season(2021,92)) if n['date']>='2021-03-23']
        cls.point=fit_minimum({'name':'huber_1','kind':'huber','alpha':1.},cls.samples)
        cls.records,cls.audit=model.residuals(cls.samples,cls.extra,cls.point,cls.policy,cls.p,2023)
        cls.spec=next(s for s in cls.p['candidates'] if s['name']=='age_version_1__tails_ridge_10')
        cls.head=model.fit_error(cls.records,cls.audit,cls.spec,cls.policy,cls.p,2023)
        cls.valid=nights(season(2024));cls.pool=model.pool(cls.point,cls.head,cls.valid,cls.p,2024);cls.cal=calibrate_shape(cls.pool,cls.p)

    def test_every_error_policy_preserves_exact_point_and_model_bytes(self):
        before=json.dumps(self.point,sort_keys=True)
        for policy in self.p['policies']:
            records,audit=model.residuals(self.samples,self.extra,self.point,policy,self.p,2023)
            head=model.fit_error(records,audit,self.spec,policy,self.p,2023)
            cal=calibrate_shape(model.pool(self.point,head,self.valid,self.p,2024),self.p)
            for n in self.valid:self.assertEqual(model.forecast(self.point,head,cal,n,self.p)['point_c'],point_minimum(self.point,n)[0])
        self.assertEqual(before,json.dumps(self.point,sort_keys=True))

    def test_weighted_or_no_cloud_point_fitters_are_never_called(self):
        with patch('agro_checker.regime_error_v2.fit_point',side_effect=AssertionError('Point must remain original')),patch('agro_checker.no_cloud_error.fit_point',side_effect=AssertionError('No substitute point')):
            records,audit=model.residuals(self.samples,self.extra,self.point,self.policy,self.p,2023)
            head=model.fit_error(records,audit,self.spec,self.policy,self.p,2023)
            self.assertEqual(model.forecast(self.point,head,self.cal,self.valid[0],self.p)['point_c'],point_minimum(self.point,self.valid[0])[0])

    def test_early2021_cold_with_missing_cloud_is_rejected_not_imputed(self):
        extra=nights(season(2021))[0];extra['target']['temperature']=[-9.]*14
        for row in extra['rows']:row['features_weather']=row['features_weather'][:5]
        with self.assertRaisesRegex(ValueError,'cloud'):model.residuals(self.samples,[extra],self.point,self.policy,self.p,2023)
        n=deepcopy(self.extra[0]);n['rows'][0]['features_weather'][5]=None
        with self.assertRaises(ValueError):model.residuals(self.samples,[n],self.point,self.policy,self.p,2023)

    def test_original_oof_error_labels_are_identical_across_age_version_policies(self):
        old,audit=crossfit_residuals(self.samples,self.point['spec'])
        errors={r['date']:r['error_c'] for r in old}
        for policy in self.p['policies']:
            records,aa=model.residuals(self.samples,[],self.point,policy,self.p,2023)
            self.assertEqual(errors,{r['date']:r['error_c'] for r in records})
            self.assertTrue(all(not set(b['fit_dates'])&set(b['held_dates']) for b in aa))

    def test_recent_uniform_reproduces_original_point_conditional_head(self):
        policy=self.p['policies'][0];records,audit=model.residuals(self.samples,[],self.point,policy,self.p,2023)
        original,aa=crossfit_residuals(self.samples,self.point['spec']);spec=self.p['families'][1]
        head=model.fit_error(records,audit,spec,policy,self.p,2023);ref=old_head(original,aa,spec)
        cal=calibrate_shape(model.pool(self.point,head,self.valid,self.p,2024),self.p)
        other=calibrate_shape(calibration_pool(self.point,ref,self.valid,self.p,2024),self.p)
        for n in self.valid:
            a=model.forecast(self.point,head,cal,n,self.p);b=old_forecast(self.point,ref,other,n,self.p)
            for key in ['point_c','lower_c','upper_c']:self.assertAlmostEqual(a[key],b[key],places=10)

    def test_control_targets_and_calibration_overlap_never_enter_fit(self):
        for year in [2024,2025,2026]:
            with self.assertRaises(ValueError):model.residuals(nights(season(year)),self.extra,self.point,self.policy,self.p,2023)
        for year in [2025,2026]:
            with self.assertRaises(ValueError):model.pool(self.point,self.head,nights(season(year)),self.p,year)
        point=deepcopy(self.point);point['training_dates'].append(self.valid[0]['date'])
        with self.assertRaises(ValueError):model.pool(point,self.head,self.valid,self.p,2024)

    def test_loo_excludes_held_score_and_missing_truth_not_filled(self):
        pool=deepcopy(self.pool);date=pool[0]['date'];pool[0]['score']=1e9
        self.assertEqual(calibrate_shape(pool,self.p,date),calibrate_shape(self.pool,self.p,date))
        valid=deepcopy(self.valid);valid[0]['target']['temperature'][0]=None
        self.assertEqual(len(model.pool(self.point,self.head,valid,self.p,2024)),39)
        with self.assertRaises(ValueError):calibrate_shape(pool[:10],self.p)

    def test_inference_ignores_truth_json_roundtrip_and_has_no_damage_claim(self):
        n=deepcopy(self.valid[0]);r=model.forecast(self.point,self.head,self.cal,n,self.p)
        n['target']={'temperature':[999]*14}
        self.assertEqual(r,model.forecast(self.point,json.loads(json.dumps(self.head)),self.cal,n,self.p))
        self.assertIsNone(r['probability_of_crop_damage']);self.assertFalse(r['coverage_guaranteed'])

    def test_guards_preserve_original_cloud_requirement_and_version_domain(self):
        self.assertIsNone(model.safe_prediction(self.point,self.head,self.cal,self.valid[0],self.p,'orchard')['prediction'])
        head=deepcopy(self.head);head['training_regimes']=['v16_before_2022_update']
        self.assertEqual(model.safe_prediction(self.point,head,self.cal,self.valid[0],self.p,'34929')['status'],'unseen_gfs_regime')
        n=deepcopy(self.valid[0]);n['rows'][0]['features_weather'][5]=None
        self.assertIsNone(model.safe_prediction(self.point,self.head,self.cal,n,self.p,'34929')['prediction'])

    def test_prior_loader_uses_only_compatible2021_and_prior_validation_years(self):
        main=season(2022)+season(2023)+season(2024);late=[r for r in season(2021,92) if r['date']>='2021-03-23'];calls=[]
        def loader(directory,years,config=None):
            calls.append(years);self.assertIn(years,[[2022,2023,2024],[2021]])
            return (late,{'year':2021}) if years==[2021] else (main,{'years':years})
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp);(data/'archive-2021').mkdir();(data/'archive-2021/source-config.json').write_text(json.dumps(archive_config()),encoding='utf-8')
            with patch('agro_checker.fixed_point_error_study.load_rows',side_effect=loader):base,extra,provenance=prior_samples(data)
        self.assertEqual(calls,[[2022,2023,2024],[2021]]);self.assertEqual(len(extra),70)

    def test_selector_rejects_changed_point_and_excessive_width(self):
        base={'covered':80,'cold_nights':7,'cold_covered':5,'cold_interval_score':8.,'warm_interval_score':6.,'mean_width_c':6.,'warm_mean_width_c':6.,'mae_c':1.}
        scores={s['name']:deepcopy(base) for s in self.p['candidates']};name='age_1__quantile_trees'
        scores[name].update(cold_covered=7,mean_width_c=20.,warm_mean_width_c=20.)
        winner,diagnostic,pair,gates=choose(scores,self.p)
        self.assertEqual(winner,'legacy_symmetric');self.assertFalse(gates[name]['overall_width_within_limit'])
        scores[name]['mae_c']=1.01
        with self.assertRaisesRegex(ValueError,'Point MAE'):choose(scores,self.p)

    def test_freeze_rejects_changes_before_any_observation_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp)/'data';data.mkdir();directory=data.parent/'models/research-fixed-point-errors';directory.mkdir(parents=True)
            (directory/'selection-before-controls.json').write_text(json.dumps({'protocol_sha256':'wrong'}),encoding='utf-8')
            with patch('agro_checker.fixed_point_error_study.parent_assets',return_value={'hashes':{}}),patch('agro_checker.fixed_point_error_study.prior_samples') as loader:
                with self.assertRaisesRegex(ValueError,'Frozen'):selection(ROOT/'config/fixed_point_error.json',data)
                loader.assert_not_called()

    def test_frozen_head_tampering_is_rejected_before_loading_targets(self):
        lock=ROOT/'models/research-fixed-point-errors/selection-before-controls.json'
        if not lock.exists():self.skipTest('Run prior selection first')
        frozen=json.loads(lock.read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp)/'data';data.mkdir();directory=data.parent/'models/research-fixed-point-errors';directory.mkdir(parents=True)
            (directory/'selection-before-controls.json').write_text(json.dumps(frozen),encoding='utf-8')
            from agro_checker.scenario_study import sha as real_sha
            def digest(path):
                if path.parent==directory:return 'tampered'
                if path.name=='selection-before-controls.json' and path.parent.name=='research-conditional-errors':return frozen['previous_selection_sha256']
                if path.name=='selection-before-controls.json' and path.parent.name=='research-extended-errors':return frozen['archive_reference_sha256']
                return real_sha(path)
            with patch('agro_checker.fixed_point_error_study.sha',side_effect=digest),patch('agro_checker.fixed_point_error_study.parent_assets',return_value={'hashes':frozen['parent_hashes']}),patch('agro_checker.fixed_point_error_study.prior_samples') as loader:
                with self.assertRaisesRegex(ValueError,'artifact'):selection(ROOT/'config/fixed_point_error.json',data)
                loader.assert_not_called()

    def test_tree_heads_work_with_original_features_and_version_indicators(self):
        spec=next(s for s in self.p['candidates'] if s['name']=='version_offsets__quantile_trees')
        for policy in [self.p['policies'][0],self.p['policies'][5]]:
            records,audit=model.residuals(self.samples,self.extra,self.point,policy,self.p,2023)
            head=model.fit_error(records,audit,spec,policy,self.p,2023)
            self.assertEqual(len(head['mean']),17 if policy['version_offsets'] else 15)
            cal=calibrate_shape(model.pool(self.point,head,self.valid,self.p,2024),self.p)
            for n in self.valid[:5]:
                prediction=model.forecast(self.point,head,cal,n,self.p)
                self.assertEqual(prediction,model.forecast(self.point,json.loads(json.dumps(head)),cal,n,self.p))
                self.assertEqual(prediction['point_c'],point_minimum(self.point,n)[0])
                self.assertLessEqual(prediction['lower_c'],prediction['point_c']);self.assertGreaterEqual(prediction['upper_c'],prediction['point_c'])

    def test_real_control_snapshots_keep_every_point_mask_and_timing(self):
        file=ROOT/'data/processed/fixed-point-error-study/night-forecasts.json'
        if not file.exists():self.skipTest('Run frozen controls first')
        frames=json.loads(file.read_text(encoding='utf-8'))
        original={f['date']:f for f in json.loads((ROOT/'data/processed/minimum-study/night-forecasts.json').read_text(encoding='utf-8'))}
        masks={f['date']:f for f in json.loads((ROOT/'data/processed/scenario-study/night-forecasts.json').read_text(encoding='utf-8'))}
        self.assertEqual(len(frames),184)
        for f in frames:
            old=original[f['date']]
            self.assertEqual(f['cold_mask'],masks[f['date']]['previous_detector_cold_mask'])
            self.assertEqual(f['previous_detector'],old['previous_detector'])
            for name in ['selected','error_only_candidate','matched_uniform']:
                self.assertEqual(f['forecasts'][name]['point_c'],old['selected']['point_c'])


if __name__=='__main__':unittest.main()
