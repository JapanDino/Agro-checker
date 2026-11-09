"""Read-only review card, explicit spatial scopes, no ML dependency or journal seeds."""
from datetime import datetime,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.request import urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError
import json

from agro_checker import scouting_review as r,farm_scouting as scout
from agro_checker.scouting_server import make_server

NOW=datetime(2026,11,9,15,tzinfo=timezone.utc)
BEFORE='S2B_T37TDK_20261105T082407_L2A'
AFTER='S2B_T37TDK_20261108T083052_L2A'


class ReviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.detail=scout.detail('R11','autumn2026',BEFORE,AFTER)
        cls.place=cls.detail['default_location']['id']

    def test_area_coverage_does_not_become_window_evidence(self):
        value=r.review('R11','autumn2026',BEFORE,AFTER,self.place,NOW)
        self.assertGreater(value['observation']['area']['coverage'],.8)
        self.assertEqual(value['status'],'window_comparison_missing')
        self.assertEqual(value['observation']['window']['paired_cells'],0)
        self.assertIsNone(value['observation']['window']['ndmi'])
        self.assertIn('недостаточно',value['question'])
        self.assertEqual(value['latest_usable_place_now']['at'][:10],'2026-11-05')
        self.assertEqual(value['archive_end_exclusive_utc'][:10],'2026-11-09')
        self.assertFalse(value['priority_usefulness_validated']);self.assertIsNone(value['automatic_diagnosis'])

    def test_reject_wrong_fragment_place(self):
        with self.assertRaises(ValueError):r.review('R20','autumn2026',BEFORE,AFTER,self.place,NOW)

    def test_boundary_scope_and_forecast_scope_not_window_accuracy(self):
        value=r.review('R11','autumn2026',BEFORE,AFTER,self.place,NOW)
        self.assertEqual(value['observation']['boundary_sensitivity']['ndmi']['scope'],'whole_fragment_not_60m_window')
        self.assertFalse(value['observation']['boundary_sensitivity']['ndmi']['statistical_interval'])
        self.assertEqual(value['forecast']['status'],'not_in_input_pool')
        self.assertIsNone(value['forecast']['selected_candidate'])
        self.assertEqual(value['forecast']['scope'],'fragment_core_not_selected_60m_window')

    def test_selected_future_forecast_is_pending_not_observation(self):
        v=r.forecast('R20',NOW)
        self.assertTrue(v['selected_baseline']);self.assertTrue(v['selected_candidate'])
        self.assertEqual(v['status'],'pending');self.assertIsNone(v['outcome'])

    def test_missing_forecast_is_not_a_negative_and_corruption_never_falls_back(self):
        with TemporaryDirectory() as directory:
            self.assertEqual(r.forecast('R20',NOW,Path(directory))['status'],'not_available')
            folder=Path(directory)/'releases';folder.mkdir()
            (folder/'2026-11-08.json').write_text('{"payload":{},"sha256":"bad"}')
            self.assertEqual(r.forecast('R20',NOW,Path(directory))['status'],'unavailable')

    def test_endpoint_and_assets_are_readonly_and_strict(self):
        with TemporaryDirectory() as directory:
            server=make_server(Path(directory),0);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                root=f'http://127.0.0.1:{server.server_port}'
                query=urlencode(dict(fid='R11',period='autumn2026',before=BEFORE,after=AFTER,place_id=self.place))
                with urlopen(root+'/api/scouting/review?'+query) as response:
                    self.assertEqual(json.load(response)['status'],'window_comparison_missing')
                with self.assertRaises(HTTPError) as error:urlopen(root+'/api/scouting/review?'+query+'&unexpected=1')
                self.assertEqual(error.exception.code,400)
                for name in ('review-card.js','review-card.css'):
                    with urlopen(root+'/scouting/'+name) as response:self.assertEqual(response.status,200)
                self.assertEqual(scout.listing(server.scouting_store)['tasks'],[])
                self.assertEqual(scout.listing(server.scouting_store)['visits'],[])
            finally:server.shutdown();server.server_close();thread.join()
