from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from agro_checker.field_store import FieldStore, digest
from agro_checker.prototype_feedback import save_feedback
from agro_checker.user_test_kit import prepare_user_test


class UserTestKitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'round'
        self.now = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)

    def test_slots_share_frozen_cases_but_have_no_user_evidence(self):
        prepare_user_test(self.path, self.now)
        kit = json.loads((self.path / 'kit.json').read_text(encoding='utf-8'))
        self.assertEqual(kit['actual_user_tests'], 0)
        self.assertEqual([c['facilitator_only_outcome'] for c in kit['cases']], ['hit', 'miss', 'false_alarm'])
        self.assertEqual(len(kit['slots']), 5)
        for slot in kit['slots']:
            store = FieldStore(slot['state_dir'])
            self.assertEqual(store.records('prototype_feedback'), [])
            self.assertEqual(store.records('forecast_read'), [])
            self.assertEqual(store.state(self.now)['field_metrics']['unique_site_nights'], 0)
            p = store.profile_record('orchard-draft')['payload']
            self.assertEqual(p['name'], '')
            self.assertIsNone(p['zones'][0]['latitude'])
            forecasts = {f['id']: f for f in store.records('forecast')}
            for case in kit['cases']:
                self.assertEqual(digest(forecasts[case['forecast_id']]['payload']), case['forecast_sha256'])
            self.assertEqual([r['id'] for r in store.records('journal')], kit['fixture_journal_ids'])
            self.assertIn('НЕ ПРОВЕДЕНА', Path(slot['session_file']).read_text(encoding='utf-8'))

    def test_decisions_and_reviews_stay_in_one_slot_and_survive_resume(self):
        prepare_user_test(self.path, self.now)
        a, b = [FieldStore(self.path / code / 'state') for code in ['P01', 'P02']]
        fore = a.records('forecast')[0]
        frozen = digest(fore['payload'])
        baseline = b.records()
        read = a.acknowledge_forecast('demo-station', fore['id'], self.now)
        a.journal('demo-station', {'forecast_id': fore['id'], 'actor': 'QA01', 'action': 'observe',
                  'record_now': True, 'read_id': read['id']}, self.now)
        first = save_feedback(a, 'demo-station', {'forecast_id': fore['id'], 'participant_code': 'QA01',
                'source': 'engineering_check', 'role': 'other', 'completion': 'not_completed',
                'data_meaning': 'unsure', 'notes': 'Only a temporary engineering fixture',
                'iteration': 'first'}, self.now)
        resumed = FieldStore(a.directory)
        self.assertEqual(resumed.records('prototype_feedback')[0]['id'], first['id'])
        self.assertEqual(digest(resumed.records('forecast')[0]['payload']), frozen)
        self.assertEqual(b.records(), baseline)

    def test_existing_directory_is_never_overwritten_and_cli_requires_target(self):
        self.path.mkdir()
        marker = self.path / 'keep.txt'
        marker.write_text('preserve')
        with self.assertRaises(FileExistsError): prepare_user_test(self.path, self.now)
        self.assertEqual(marker.read_text(), 'preserve')
        self.assertFalse((self.path / 'fixtures').exists())
        result = subprocess.run([sys.executable, '-m', 'agro_checker', 'user-test-prepare'],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('requires --test-dir', result.stderr)
