"""Draft field risk queue: records import, rules, marks, node series. Disposable stores only."""
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from agro_checker import field_risk as r
from agro_checker.field_store import FieldStore
from agro_checker.scouting_server import make_server

NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
HEAD = 'тип;обводка;поле_в_учёте;культура;дата_сева;фаза;дата_фазы;дата;препарат;вид;болезнь;широта;долгота\n'


def rid():return uuid4().hex


def synthetic_days(wet_by_day, temp=18.0, rain_by_day=None):
    """Local day -> 24 hourly rows (hour, temp, rh, rain); wet hours have RH 95 %."""
    rain_by_day = rain_by_day or {}
    out = {}
    start = date(2027, 5, 1)
    for i in range(31):
        d = start+timedelta(days=i);wet = wet_by_day.get(d, 0);rain = rain_by_day.get(d, {})
        out[d] = [(h, temp, 95 if h < wet else 60, rain.get(h, 0.0)) for h in range(24)]
    return out, [{'start': '2027-05-01', 'end': '2027-05-31', 'label': 'Синтетика'}]


class FieldRiskTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.store = FieldStore(Path(self.tmp.name))

    def tearDown(self):self.tmp.cleanup()

    def records(self, rows):return HEAD+'\n'.join(rows)+'\n'

    def save(self, rows, **extra):
        raw = {'request_id': rid(), 'filename': 'agromon.csv', 'csv': self.records(rows), 'confirm_farm_data': True, **extra}
        return r.save_records(self.store, raw, NOW)

    # ── parsing ──
    def test_parsing_accepts_russian_dates_comma_and_english_headers(self):
        text = ('type,outline,field_name,crop,sowing_date,phase,phase_date,date,product,kind,disease,latitude,longitude\n'
                'поле,R11,Поле 11,озимая пшеница,12.10.2026,флаговый лист,08.05.2027,,,,,,\n'
                'обработка,R11,,,,,,2027-05-02,Препарат,фунгицид,,,\n')
        parsed, errors, warnings = r.parse_records(text)
        self.assertEqual(errors, [])
        self.assertEqual(parsed['fields'][0]['sowing_date'], '2026-10-12')
        self.assertEqual(parsed['fields'][0]['phases'], [{'phase': 'флаговый лист', 'date': '2027-05-08'}])
        self.assertEqual(parsed['treatments'][0]['kind'], 'фунгицид')

    def test_row_numbered_errors(self):
        parsed, errors, _ = r.parse_records(self.records([
            'поле;R99;x;озимая пшеница;2026-10-12;;;;;;;;',
            'поле;R11;x;рис;2026-10-12;;;;;;;;',
            'поле;R11;x;озимая пшеница;2026-13-12;;;;;;;;',
            'обработка;R11;;;;;;2027-05-02;x;удобрение;;;',
            'очаг;R11;;;;;;2027-05-02;;;Септориоз;10;10',
            'поле;R11;x;озимая пшеница;2026-10-12;флаговый лист;;;;;;;']))
        self.assertEqual([e.split(':')[0] for e in errors], ['Строка 2', 'Строка 3', 'Строка 4', 'Строка 5', 'Строка 6', 'Строка 7'])
        with self.assertRaisesRegex(ValueError, 'Неизвестные столбцы'):r.parse_records('тип;обводка;лишнее\nполе;R11;1\n')

    def test_example_is_labelled_previewable_and_never_savable(self):
        text = r.records_example().decode('utf-8')
        preview = r.records_preview(self.store, {'filename': 'example.csv', 'csv': text})
        self.assertTrue(preview['is_example']);self.assertEqual(preview['errors'], [])
        self.assertTrue(all('ПРИМЕР' in f['field_name'] for f in preview['fields']))
        for csv_text in (text, text.replace('\n', '\r\n'), r.records_template().decode('utf-8')):
            with self.assertRaisesRegex(ValueError, 'нельзя сохранить|ПРИМЕР'):
                r.save_records(self.store, {'request_id': rid(), 'filename': 'a.csv', 'csv': csv_text, 'confirm_farm_data': True}, NOW)
        self.assertEqual(self.store.records(), [])

    def test_save_requires_confirmation_and_is_idempotent(self):
        rows = ['поле;R11;Поле 11;озимая пшеница;2026-10-12;флаговый лист;2027-05-08;;;;;;']
        raw = {'request_id': rid(), 'filename': 'agromon.csv', 'csv': self.records(rows), 'confirm_farm_data': False}
        with self.assertRaisesRegex(ValueError, 'Подтвердите'):r.save_records(self.store, raw, NOW)
        raw['confirm_farm_data'] = True
        first = r.save_records(self.store, raw, NOW);second = r.save_records(self.store, dict(raw), NOW)
        self.assertEqual(first['id'], second['id']);self.assertEqual(len(self.store.records()), 1)
        self.assertFalse(first['payload']['model_training_eligible'])
        with self.assertRaisesRegex(ValueError, 'другим содержимым'):r.save_records(self.store, {**raw, 'filename': 'b.csv'}, NOW)
        with self.assertRaisesRegex(ValueError, 'Исправьте'):self.save(['поле;R99;x;озимая пшеница;2026-10-12;;;;;;;;'])

    # ── rules on a constructed scenario ──
    def scenario(self, rows, day, wet, rain=None, temp=18.0, source='journal'):
        self.save(rows)
        with mock.patch.object(r, 'weather', return_value=synthetic_days(wet, temp, rain)):
            return {f['id']: f for f in r.risk_view(self.store, day, source)['fields']}

    def test_statuses_phase_protection_and_rain(self):
        field = 'поле;{0};П;озимая пшеница;2026-10-12;флаговый лист;2027-05-01;;;;;;'
        rows = [field.format(x) for x in ['R01', 'R02', 'R03', 'R07']] + [
            'поле;R09;П;озимая пшеница;2026-10-12;кущение;2027-05-01;;;;;;',
            'обработка;R02;;;;;;2027-05-08;Ф;фунгицид;;;',
            'обработка;R03;;;;;;2027-05-08;Ф;фунгицид;;;',
            'обработка;R07;;;;;;2027-05-12;Ф;фунгицид;;;',
            'поле;R10;П;кукуруза;2027-04-28;;;;;;;;']
        wet = {date(2027, 5, 12): 14}
        rain = {date(2027, 5, 8): {13: 6.0, 15: 5.0}}  # 11 mm after R02/R03's treatment day → voided for both
        f = self.scenario(rows, '2027-05-20', wet, rain)
        self.assertEqual(f['R01']['status'], 'risk')
        self.assertEqual(f['R01']['inspect_by'], '2027-05-22')  # rust: 12.05 + 10 days
        self.assertIn('лист влажный 14 ч', f['R01']['reasons'][0])
        self.assertIn('фунгицида не было', f['R01']['reasons'][-1])
        self.assertEqual(f['R02']['status'], 'risk');self.assertIn('смыт дождём 11,0 мм', f['R02']['reasons'][-1])
        self.assertEqual(f['R07']['status'], 'normal')  # protected 12.05–25.05, not ending within 3 days of 20.05
        self.assertEqual(f['R09']['status'], 'normal');self.assertIn('до фазы', f['R09']['reasons'][0])
        self.assertEqual(f['R10']['status'], 'no_rules');self.assertEqual(f['R11']['status'], 'no_data')
        self.assertEqual(f['R01']['data_source'], 'era5_sector')

    def test_protection_ending_soon_and_partial_conditions_are_check(self):
        rows = ['поле;R01;П;озимая пшеница;2026-10-12;флаговый лист;2027-05-01;;;;;;',
                'обработка;R01;;;;;;2027-05-10;Ф;фунгицид;;;',
                'поле;R02;П;озимая пшеница;2026-10-12;флаговый лист;2027-05-01;;;;;;']
        f = self.scenario(rows, '2027-05-21', {date(2027, 5, 12): 14})
        self.assertEqual(f['R01']['status'], 'check');self.assertEqual(f['R01']['inspect_by'], '2027-05-23')
        with mock.patch.object(r, 'weather', return_value=synthetic_days({date(2027, 5, 15): 4})):
            v = {x['id']: x for x in r.risk_view(self.store, '2027-05-20', 'journal')['fields']}
        self.assertEqual(v['R02']['status'], 'check');self.assertIn('близки к порогу', v['R02']['reasons'][0])

    def test_longest_protection_and_renewal(self):
        field = 'поле;{0};П;озимая пшеница;2026-10-12;флаговый лист;2027-05-01;;;;;;'
        rows = [field.format(x) for x in ['R01', 'R02', 'R03']] + [
            'обработка;R01;;;;;;2027-05-08;Ф;фунгицид;;;', 'обработка;R01;;;;;;2027-05-12;Ф;фунгицид;;;',  # both cover 12.05
            'обработка;R02;;;;;;2027-05-08;Ф;фунгицид;;;', 'обработка;R02;;;;;;2027-05-19;Ф;фунгицид;;;',  # renewed after the event
            'обработка;R03;;;;;;2027-05-08;Ф;фунгицид;;;']
        f = self.scenario(rows, '2027-05-20', {date(2027, 5, 12): 14})
        rust = lambda fid: next(d for d in f[fid]['diseases'] if d['id'] == 'brown_rust')
        self.assertEqual(rust('R01')['events'][0]['protected_by'], '2027-05-12')  # protects until 25.05, not 21.05
        self.assertEqual(f['R01']['status'], 'normal')
        self.assertEqual(rust('R02')['events'][0]['protected_by'], '2027-05-08')
        self.assertEqual(f['R02']['status'], 'normal')  # 19.05 treatment extends protection past 21.05
        self.assertEqual(f['R03']['status'], 'check');self.assertEqual(f['R03']['inspect_by'], '2027-05-21')

    def test_inspection_window_starts_after_latent_period(self):
        rows = ['поле;R01;П;озимая пшеница;2026-10-12;флаговый лист;2027-05-01;;;;;;']
        f = self.scenario(rows, '2027-05-20', {date(2027, 5, 12): 14})
        self.assertEqual((f['R01']['inspect_from'], f['R01']['inspect_by']), ('2027-05-19', '2027-05-22'))
        with mock.patch.object(r, 'weather', return_value=synthetic_days({date(2027, 5, 15): 4})):
            v = {x['id']: x for x in r.risk_view(self.store, '2027-05-20', 'journal')['fields']}
        self.assertEqual(v['R01']['status'], 'check')
        self.assertEqual((v['R01']['inspect_from'], v['R01']['inspect_by']), ('2027-05-22', '2027-05-25'))
        with mock.patch.object(r, 'weather', return_value=synthetic_days({})):
            v = {x['id']: x for x in r.risk_view(self.store, '2027-05-20', 'journal')['fields']}
        self.assertEqual(v['R01']['status'], 'normal');self.assertIsNone(v['R01']['inspect_from'])

    def test_temperature_outside_range_is_not_a_condition(self):
        rows = ['поле;R01;П;озимая пшеница;2026-10-12;флаговый лист;2027-05-01;;;;;;']
        f = self.scenario(rows, '2027-05-20', {date(2027, 5, 12): 20}, temp=5.0)
        self.assertEqual(f['R01']['status'], 'normal')

    def test_marks_feed_queue_and_summary(self):
        rows = ['поле;R01;П;озимая пшеница;2026-10-12;флаговый лист;2027-05-01;;;;;;']
        self.save(rows)
        mark = {'request_id': rid(), 'fid': 'R01', 'observed_on': '2027-05-19', 'result': 'focus', 'disease': 'Бурая ржавчина',
                'notes': '', 'actor': 'Агроном', 'real_visit_confirmed': True}
        later = datetime(2027, 6, 1, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, 'будущем'):r.save_mark(self.store, mark, NOW)
        with self.assertRaisesRegex(ValueError, 'Подтвердите'):r.save_mark(self.store, {**mark, 'real_visit_confirmed': False}, later)
        saved = r.save_mark(self.store, mark, later)
        self.assertFalse(saved['payload']['model_training_eligible'])
        with mock.patch.object(r, 'weather', return_value=synthetic_days({date(2027, 5, 12): 14})):
            v = r.risk_view(self.store, '2027-05-14', 'journal')
        f = next(x for x in v['fields'] if x['id'] == 'R01')
        self.assertEqual(f['marks'][0]['result'], 'focus')
        self.assertEqual(v['marks_summary']['signals_total'], 1);self.assertEqual(v['marks_summary']['signals_with_marks'], 1)
        self.assertEqual(v['marks_summary']['focus_total'], 1);self.assertEqual(v['marks_summary']['focus_after_signal'], 1)
        self.assertTrue(any('не переобучается' in x for x in v['limitations']))

    def test_sensor_series_overrides_sector_weather(self):
        rows = ['поле;R01;П;озимая пшеница;2026-10-12;флаговый лист;2027-05-01;;;;;;',
                'поле;R02;П;озимая пшеница;2026-10-12;флаговый лист;2027-05-01;;;;;;']
        lines = ['время;лист_влажный;температура;влажность;осадки']
        for day in range(11, 21):
            for h in range(24):lines.append(f'2027-05-{day:02d}T{h:02d}:00;0;17;70;0')
        with self.assertRaisesRegex(ValueError, 'Строка 2'):
            r.save_sensor_series(self.store, {'request_id': rid(), 'fid': 'R01', 'node_name': 'Узел 3', 'csv': lines[0]+'\n2027-05-11T00:00;2;17;70;0\n'}, datetime(2027, 6, 1, tzinfo=timezone.utc))
        r.save_sensor_series(self.store, {'request_id': rid(), 'fid': 'R01', 'node_name': 'Узел 3', 'csv': '\n'.join(lines)}, datetime(2027, 6, 1, tzinfo=timezone.utc))
        f = self.scenario(rows, '2027-05-20', {date(2027, 5, 12): 14})
        self.assertEqual(f['R01']['data_source'], 'sensor:Узел 3');self.assertEqual(f['R01']['status'], 'normal')
        self.assertEqual(f['R02']['data_source'], 'era5_sector');self.assertEqual(f['R02']['status'], 'risk')

    # ── real archive ──
    def test_real_spring_2024_example_shape(self):
        v = r.risk_view(None, '2024-05-20', 'example')
        self.assertEqual(v['source']['kind'], 'example');self.assertEqual(v['rules']['status'], 'draft_unvalidated')
        self.assertEqual(len(v['fields']), 27);self.assertGreaterEqual(v['summary']['risk'], 1);self.assertGreaterEqual(v['summary']['normal'], 1)
        order = [r.STATUS_ORDER.index(f['status']) for f in v['fields']];self.assertEqual(order, sorted(order))
        for f in v['fields']:
            for key in ['id', 'field_name', 'crop', 'phase', 'phase_date', 'sowing_date', 'status', 'status_label', 'reasons',
                        'inspect_by', 'data_source', 'data_source_label', 'last_fungicide', 'diseases', 'marks']:self.assertIn(key, f)
        empty = r.risk_view(self.store, '2024-05-20', 'journal')
        self.assertEqual(empty['source']['kind'], 'none');self.assertEqual(empty['summary']['no_data'], 27)
        with self.assertRaisesRegex(ValueError, 'Доступно'):r.risk_view(None, '2025-05-20', 'example')
        with self.assertRaises(ValueError):r.risk_view(None, '2024-05-20', 'other')
        self.assertEqual(self.store.records(), [])

    def test_weather_hash_mismatch_is_refused(self):
        r.weather.cache_clear()
        try:
            with mock.patch.object(r.hashlib, 'sha256') as fake:
                fake.return_value.hexdigest.return_value = '0'*64
                with self.assertRaisesRegex(ValueError, 'контрольной суммой'):r.weather()
        finally:r.weather.cache_clear()


class RiskRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.server = make_server(Path(cls.directory.name), 0)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True);cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.directory.cleanup()

    def get(self, path):
        try:
            with urlopen(self.base+path) as response:return response.status, response.read(), response.headers.get_content_type()
        except HTTPError as error:return error.code, error.read(), error.headers.get_content_type()

    def test_routes(self):
        status, body, mime = self.get('/api/scouting/risk?date=2024-05-20&source=example')
        self.assertEqual((status, mime), (200, 'application/json'))
        self.assertEqual(json.loads(body)['source']['kind'], 'example')
        self.assertEqual(self.get('/api/scouting/risk?date=2030-01-01&source=example')[0], 400)
        self.assertEqual(self.get('/api/scouting/risk?date=2024-05-20')[0], 400)
        status, body, mime = self.get('/api/scouting/records/example')
        self.assertEqual((status, mime), (200, 'text/csv'));self.assertIn('ПРИМЕР'.encode(), body)
        self.assertEqual(self.get('/api/scouting/records/template')[0], 200)
        token = json.loads(self.get('/api/scouting/state')[1])['token']
        body = json.dumps({'filename': 'example.csv', 'csv': r.records_example().decode('utf-8')}).encode()
        request = Request(self.base+'/api/scouting/records/preview', data=body, method='POST',
                          headers={'Content-Type': 'application/json', 'X-Field-Token': token})
        with urlopen(request) as response:self.assertTrue(json.loads(response.read())['is_example'])
        self.assertEqual(self.server.scouting_store.records(), [])


if __name__ == '__main__':unittest.main()
