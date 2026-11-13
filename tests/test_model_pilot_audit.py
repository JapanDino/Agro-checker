"""Technical fixtures only; no farm observations or network calls."""
from datetime import date
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import audit_model_pilot_data as audit


class PilotAuditTest(unittest.TestCase):
    def test_missing_journal_is_unknown_and_never_created(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'missing'
            result=audit.journal_counts(path)
            self.assertIsNone(result['visits'])
            self.assertEqual(result['status'],'not_available')
            self.assertFalse(path.exists())

    def test_existing_journal_read_does_not_turn_visits_into_labels(self):
        with TemporaryDirectory() as folder:
            path=Path(folder);db=path/'field.sqlite3'
            with closing(sqlite3.connect(db)) as c:
                c.execute('CREATE TABLE events(kind TEXT,profile_id TEXT)')
                c.executemany('INSERT INTO events VALUES(?,?)',[('farm_scouting_visit','farm-scouting-01'),('farm_scouting_visit','other-stream')])
                c.commit()
            before=hashlib.sha256(db.read_bytes()).hexdigest()
            result=audit.journal_counts(path)
            self.assertEqual(result['visits'],1)
            self.assertIsNone(result['validated_training_labels'])
            self.assertEqual(result['sha256_after'],before)

    def test_timezone_is_explicit(self):
        self.assertEqual(audit.stamp('2026-11-09T00:00:00+03:00').isoformat(),'2026-11-08T21:00:00+00:00')
        with self.assertRaises(ValueError):audit.stamp('2026-11-09T00:00:00')

    def fixture_report(self):
        return {'cutoff_utc':'2026-11-08T21:00:00+00:00','geometry':{'bbox_wgs84':[37.8,44.9,38.1,45.2]}}

    def test_pagination_is_exhausted_but_metadata_is_not_usable_imagery(self):
        feature={'id':'TEST-STAC','collection':audit.COLLECTION,'properties':{'datetime':'2026-11-08T08:00:00Z','eo:cloud_cover':0}}
        pages=[{'features':[feature],'links':[{'rel':'next','href':audit.SEARCH+'?token=test'}]}, {'features':[],'links':[]}]
        with TemporaryDirectory() as folder,patch.object(audit,'fetch',side_effect=[json.dumps(p).encode() for p in pages]):
            result=audit.probe_catalogue(self.fixture_report(),date(2026,10,20),Path(folder))
        self.assertTrue(result['catalogue_complete'])
        self.assertEqual(result['catalogue_items_received'],1)
        self.assertIsNone(result['usable_new_images'])
        self.assertFalse(result['field_cloud_quality_evaluated'])

    def test_foreign_pagination_and_future_items_cannot_claim_complete_catalogue(self):
        cases=[{'features':[],'links':[{'rel':'next','href':'https://example.invalid/secret'}]},
               {'features':[{'id':'TEST-FUTURE','collection':audit.COLLECTION,'properties':{'datetime':'2026-11-09T00:00:00Z'}}],'links':[]}]
        for page in cases:
            with self.subTest(page=page),TemporaryDirectory() as folder,patch.object(audit,'fetch',return_value=json.dumps(page).encode()) as fetch:
                result=audit.probe_catalogue(self.fixture_report(),date(2026,10,20),Path(folder))
                self.assertFalse(result['catalogue_complete'])
                self.assertIsNotNone(result['error'])
                self.assertEqual(fetch.call_count,1)


if __name__=='__main__':unittest.main()
