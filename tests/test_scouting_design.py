"""Visual rollback links must work without loosening archive API validation."""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from agro_checker.scouting_server import make_server


class DesignRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory=TemporaryDirectory()
        cls.server=make_server(Path(cls.directory.name),0)
        cls.thread=Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.directory.cleanup()

    def status(self,path):
        try:
            with urlopen(self.base+path) as response:
                return response.status,response.read(),response.headers.get_content_type()
        except HTTPError as error:
            return error.code,error.read(),error.headers.get_content_type()

    def test_reload_and_about_support_both_designs(self):
        for path in ('/','/scouting','/scouting/about'):
            for design in ('pulse','atlas','hightech'):
                with self.subTest(path=path,design=design):
                    status,body,mime=self.status(path+'?design='+design)
                    self.assertEqual(status,200)
                    self.assertEqual(mime,'text/html')
                    self.assertIn(b'data-design-sheet="atlas"',body)
                    self.assertIn(b'data-design-sheet="hightech pulse"',body)
                    self.assertIn(b'data-design-sheet="pulse"',body)

    def test_invalid_repeated_and_extra_parameters_stay_rejected(self):
        for query in ('design=unknown','design=','design=atlas&design=hightech','design=atlas&extra=1'):
            with self.subTest(query=query):
                self.assertEqual(self.status('/scouting?'+query)[0],400)
        for path in ('/api/scouting/state','/api/scouting/export','/scouting/hightech.css'):
            with self.subTest(path=path):
                self.assertEqual(self.status(path+'?design=atlas')[0],400)

    def test_design_assets_have_explicit_types_and_no_journal_side_effects(self):
        for file,mime in (('design.js','text/javascript'),('hightech.css','text/css'),('design-controls.css','text/css'),
                          ('pulse.css','text/css'),('pulse-screens.css','text/css'),('pulse.js','text/javascript'),
                          ('assets/fonts/manrope-cyrillic-wght-normal.woff2','font/woff2'),('assets/fonts/manrope-OFL.txt','text/plain')):
            status,body,actual=self.status('/scouting/'+file)
            self.assertEqual(status,200)
            self.assertTrue(body)
            self.assertEqual(actual,mime)
        self.assertEqual(self.server.scouting_store.records(),[])


if __name__=='__main__':unittest.main()
