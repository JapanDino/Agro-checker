"""Agronomist workstation (web/workstation): served by the product server under the same strict CSP."""
from html.parser import HTMLParser
from pathlib import Path
import re
import tempfile
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from agro_checker.scouting_server import make_server

ROOT = Path(__file__).resolve().parents[1]
DESK = ROOT/'web/workstation'


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):return None


class _Inline(HTMLParser):
    """Collects what a `script-src 'self'; style-src 'self'` policy would block."""
    def __init__(self):
        super().__init__();self.blocked = []
        self._script = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'style':self.blocked.append('<style>')
        if tag == 'script':
            if 'src' not in a:self._script = True
        if 'style' in a:self.blocked.append(f'style attribute on <{tag}>')
        if any(k.startswith('on') for k in a):self.blocked.append(f'inline handler on <{tag}>')
        if tag == 'link' and a.get('rel') == 'stylesheet' and re.match(r'https?://', a.get('href', '')):self.blocked.append('external stylesheet')

    def handle_endtag(self, tag):
        if tag == 'script':self._script = False

    def handle_data(self, data):
        if self._script and data.strip():self.blocked.append('inline <script>')


class WorkstationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.server = make_server(Path(cls.directory.name), 0)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True);cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.directory.cleanup()

    def get(self, path):
        try:
            with urlopen(self.base+path) as response:return response.status, response.read(), response.headers
        except HTTPError as error:return error.code, error.read(), error.headers

    def test_routes_and_policy(self):
        for path, mime in [('/workstation/', 'text/html'), ('/workstation/workstation.css', 'text/css'),
                           ('/workstation/workstation.js', 'text/javascript'), ('/workstation/farm-basemap.jpg', 'image/jpeg'),
                           ('/scouting/assets/fonts/manrope-latin-wght-normal.woff2', 'font/woff2')]:
            status, body, headers = self.get(path)
            self.assertEqual((status, headers.get_content_type()), (200, mime), path)
            self.assertGreater(len(body), 0)
            self.assertIn("script-src 'self'; style-src 'self'", headers['Content-Security-Policy'])
        try:
            build_opener(_NoRedirect).open(Request(self.base+'/workstation'))
            self.fail('expected a redirect')
        except HTTPError as error:
            self.assertEqual((error.code, error.headers['Location']), (308, '/workstation/'))
        self.assertEqual(self.get('/workstation/?design=pulse')[0], 400)
        self.assertEqual(self.get('/workstation/../config/field_risk_rules.json')[0], 404)
        status, _, headers = self.get('/scouting/inspections?field=R11')
        self.assertEqual(status, 200)
        self.assertIn("frame-ancestors 'self'", headers['Content-Security-Policy'])
        self.assertEqual(self.get('/scouting/inspections?field=R28')[0], 400)
        self.assertIn("frame-ancestors 'none'", self.get('/scouting')[2]['Content-Security-Policy'])

    def test_page_has_nothing_the_csp_blocks(self):
        page = _Inline();page.feed((DESK/'index.html').read_text(encoding='utf-8'))
        self.assertEqual(page.blocked, [])
        script = (DESK/'workstation.js').read_text(encoding='utf-8')
        # Generated markup must carry styles as data-style (applied through the CSSOM), never as style="…".
        self.assertEqual(re.findall(r'(?<![\w.-])style="', script), [])
        self.assertNotIn('fonts.googleapis', (DESK/'workstation.css').read_text(encoding='utf-8'))
        self.assertNotRegex(script, r'\beval\(|new Function\(')

    def test_page_uses_the_rules_file_thresholds(self):
        """The in-page engine mirrors config/field_risk_rules.json; a threshold change must be made in both."""
        import json
        rules = json.loads((ROOT/'config/field_risk_rules.json').read_text(encoding='utf-8'))
        script = (DESK/'workstation.js').read_text(encoding='utf-8')
        for dz in rules['diseases']:
            self.assertRegex(script, rf"wet:{dz['wet_hours']},tmin:{dz['temp_min']},tmax:{dz['temp_max']},from:'{dz['susceptible_from']}',after:{dz['inspect_after_days']}")
        self.assertIn(f"const LATENT={rules['inspect_window']['latent_days']};", script)
        self.assertIn(f"protDays:{rules['protection']['days']},washMm:{rules['protection']['washoff_rain_mm']},endingSoon:{rules['protection']['ending_soon_days']}", script)


if __name__ == '__main__':unittest.main()
