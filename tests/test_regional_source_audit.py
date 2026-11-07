from datetime import datetime, timedelta, timezone
import unittest
from agro_checker.regional_source_audit import site_availability


def night():
    start = datetime(2024, 3, 1, 16, tzinfo=timezone.utc)
    return [{'observed_at_utc': (start+timedelta(hours=i)).isoformat(), 'temperature_c': 0., 'quality': '1',
             'latitude': 45.002, 'longitude': 37.347, 'elevation_m': 53} for i in range(14)]


class RegionalSourceAuditTests(unittest.TestCase):
    def test_different_positions_cannot_form_one_complete_night(self):
        rows = night(); rows[-1]['latitude'] = 44.883
        with self.assertRaises(ValueError): site_availability(rows)

    def test_exact_complete_night_and_duplicate_agreement(self):
        rows = night(); rows.append(dict(rows[0])); result = site_availability(rows)
        self.assertEqual(result['exact_14_hour_pilot_nights_qc1_or5'], 1)
        self.assertEqual(result['exact_hour_pilot_slots_present_qc1_or5'], 14)

    def test_missing_or_suspect_hour_is_not_filled(self):
        rows = night(); rows[0]['quality'] = '2'; result = site_availability(rows)
        self.assertEqual(result['exact_14_hour_pilot_nights_qc1_or5'], 0)
        self.assertEqual(result['exact_hour_pilot_slots_present_qc1_or5'], 13)

    def test_subhourly_reports_never_rounded(self):
        rows = night()
        for row in rows: row['observed_at_utc'] = row['observed_at_utc'].replace(':00:00+', ':30:00+')
        result = site_availability(rows)
        self.assertEqual(result['exact_hour_pilot_slots_present_qc1_or5'], 0)

    def test_conflicting_duplicate_cannot_supply_temperature(self):
        rows = night(); rows.append({**rows[0], 'temperature_c': 1.})
        result = site_availability(rows)
        self.assertEqual(result['conflicting_qc1_or5_timestamps_including_dawn'], 1)
        self.assertEqual(result['exact_14_hour_pilot_nights_qc1_or5'], 0)
        self.assertEqual(result['exact_hour_pilot_slots_present_qc1_or5'], 13)


if __name__ == '__main__': unittest.main()
