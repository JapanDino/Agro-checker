from copy import deepcopy
from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from agro_checker.field_store import FieldStore, seed_demo
from agro_checker.forecast_quality import model_quality
from agro_checker.field_decision import forecast_brief
from agro_checker.pipeline import UTC


class ForecastQualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = FieldStore(Path(self.temp.name))
        seed_demo(self.store, datetime(2026, 10, 13, 12, tzinfo=UTC))
        self.f = self.store.records("forecast")[0]["payload"]

    def test_health_does_not_promote_synthetic_data_or_offer_all_clear(self):
        before = deepcopy(self.f)
        q = model_quality(self.f)
        self.assertEqual(q["status"], "synthetic_learning")
        self.assertFalse(q["all_clear_allowed"])
        self.assertFalse(q["local_error_range_verified"])
        self.assertFalse(q["local_correction_applied"])
        self.assertEqual(q["temperature_hours"], 14)
        self.assertEqual(q["complete_weather_feature_hours"], 0)
        self.assertEqual(self.f, before)
        self.assertEqual(forecast_brief(self.f)["model_quality"], q)

    def test_missing_inputs_and_real_gfs_are_not_silently_called_locally_validated(self):
        f = deepcopy(self.f); f["origin"] = "live_gfs"
        f["hours"][0]["temperature_c"] = None
        q = model_quality(f)
        self.assertFalse(q["data_complete"])
        self.assertEqual(q["temperature_hours"], 13)
        self.assertEqual(q["status"], "local_accuracy_unverified")
        self.assertIsNone(q["crop_damage_probability"])

    def test_later_observations_cannot_change_quality_contract(self):
        q = model_quality(self.f)
        self.f["hindsight"] = {"temperature": [-100]*14}
        self.assertEqual(model_quality(self.f), q)
