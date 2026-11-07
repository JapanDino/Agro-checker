from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile
from agro_checker import krymsk_official as subject


STAMP = '2024-03-01T18:00:00+00:00'


def official(temp='-1.2', qc='0', stamp=STAMP):
    return subject.record('37002', stamp, temp, qc)


def provider(value=-1.2):
    return {'station_id': '37002', 'observed_at_utc': STAMP, 'temperature_c': value,
            'provider_model_filled_temperature': False}


def fixed(temp=' -1.2', qc='0'):
    line = list(' '*207); line[:15] = list('370022024030118'); line[15:25] = list('1999010100')
    line[122:127] = list(temp); line[127] = qc
    return ''.join(line).encode('ascii')


class OfficialKrymskTests(unittest.TestCase):
    def test_published_greenwich_fields_not_local_time(self):
        a = subject.parse_fixed_width(fixed())[0]
        self.assertEqual(a['observed_at_utc'], STAMP)
        self.assertEqual(a['primary_temperature_c'], -1.2)

    def test_fixed_width_refuses_shifted_columns_or_implicit_scale(self):
        with self.assertRaises(ValueError): subject.parse_fixed_width(fixed()+b' ')
        with self.assertRaises(ValueError): subject.parse_fixed_width(fixed(temp='  -12'))

    def test_qc_original_restored_and_rejected_stay_separate(self):
        for qc in range(10):
            a = official(qc=str(qc))
            self.assertEqual(a['primary_temperature_c'], -1.2 if qc == 0 else None)
            self.assertEqual(a['reconstructed_temperature_c'], -1.2 if qc in [1, 2] else None)
        self.assertEqual(official(qc='9')['quality_meaning'], 'unknown')

    def test_missing_sentinel_cannot_become_extreme_temperature(self):
        a = official(temp='999.9', qc='5'); self.assertIsNone(a['primary_temperature_c'])
        a = official(temp='999.9', qc='0'); self.assertTrue(a['quality_value_inconsistent'])
        self.assertIsNone(a['primary_temperature_c'])
        a = official(temp='', qc='0'); self.assertTrue(a['quality_value_inconsistent'])
        self.assertIsNone(a['primary_temperature_c'])

    def test_missing_never_becomes_zero_but_zero_is_real(self):
        self.assertIsNone(official(temp='', qc='7')['primary_temperature_c'])
        self.assertEqual(official(temp='0.0')['primary_temperature_c'], 0.)

    def test_wrong_site_naive_timezone_or_nonterm_rejected(self):
        with self.assertRaises(ValueError): subject.record('37001', STAMP, '1.0', '0')
        for stamp in ['2024-03-01T18:00:00', '2024-03-01T21:00:00+03:00', '2024-03-01T18:01:00+00:00', '2024-03-01T19:00:00Z']:
            with self.assertRaises(ValueError): official(stamp=stamp)

    def test_controls_and_unrequested_period_rejected(self):
        for year in [2025, 2026]:
            with self.assertRaises(ValueError): official(stamp=f'{year}-03-01T18:00:00Z')
        with self.assertRaises(ValueError): official(stamp='2024-06-01T06:00:00Z')
        self.assertEqual(len(list(subject.expected_times(2024))), 738)

    def test_normalized_csv_requires_qc_and_refuses_fake_empty_export(self):
        good = b'station_id,observed_at_utc,temperature_c,temperature_qc\n37002,2024-03-01T18:00:00Z,-1.2,0\n'
        self.assertEqual(subject.parse_csv(good)[0]['primary_temperature_c'], -1.2)
        with self.assertRaises(ValueError): subject.parse_csv(good.replace(b',temperature_qc', b''))
        with self.assertRaises(ValueError): subject.parse_csv(good.splitlines()[0]+b'\n')
        with self.assertRaises(ValueError): subject.parse_csv(b'<html>HTTP200 is not observations</html>')

    def test_duplicate_revisions_not_silently_overwritten(self):
        with self.assertRaises(ValueError): subject.compare([official(), official()], [provider()], [STAMP])
        with self.assertRaises(ValueError): subject.compare([official()], [provider(), provider()], [STAMP])

    def test_no_official_rows_means_unavailable_not_zero_error(self):
        r = subject.compare([], [provider()], [STAMP])
        self.assertEqual(r['status'], 'comparison_unavailable_no_qc0_pairs')
        self.assertEqual(r['all_terms']['comparable_qc0_pairs'], 0)
        self.assertIsNone(r['all_terms']['differences'])

    def test_reconstruction_never_counts_as_primary_agreement(self):
        r = subject.compare([official(qc='2')], [provider()], [STAMP])
        self.assertEqual(r['all_terms']['comparable_qc0_pairs'], 0)
        self.assertEqual(r['restored_audit_only']['2']['n'], 1)
        self.assertIsNone(r['restored_audit_only']['1'])

    def test_disagreements_are_listed_without_averaging_targets(self):
        local = provider(value=.4); original = deepcopy(local)
        r = subject.compare([official(temp='-1.2')], [local], [STAMP])
        self.assertAlmostEqual(r['pairs'][0]['delta_c'], -1.6)
        self.assertEqual(r['all_terms']['differences']['opposite_zero_classification'], 1)
        self.assertEqual(len(r['differences_above_precision']), 1)
        self.assertEqual(local, original)

    def test_provider_model_fill_cannot_certify_agreement(self):
        local = {**provider(), 'provider_model_filled_temperature': True}
        r = subject.compare([official()], [local], [STAMP])
        self.assertEqual(r['all_terms']['comparable_qc0_pairs'], 0)

    def test_pilot_scope_has_exact_four_slots_and_dawn_padding(self):
        r = subject.compare([], [])
        self.assertEqual(r['all_terms']['expected_terms'], 2214)
        self.assertEqual(r['pilot_terms']['expected_terms'], 1104)

    def test_empty_xlsx_not_an_observation_dataset(self):
        b = io.BytesIO()
        with ZipFile(b, 'w') as z:
            z.writestr('xl/workbook.xml', '<workbook/>')
            z.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData/></worksheet>')
        r = subject.inspect_workbook(b.getvalue())
        self.assertTrue(r['empty_template']); self.assertFalse(r['is_observation_dataset'])

    def test_import_requires_provenance_and_hash_before_comparing(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'input.csv'; path.write_text('not an export')
            manifest = Path(d)/'meta.json'; manifest.write_text(json.dumps({'station_id': '37002', 'sha256': 'wrong'}))
            with self.assertRaises(ValueError): subject.import_and_compare(path, manifest, 'csv')

    def test_import_saves_separate_comparison_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d); path = directory/'original.csv'
            path.write_text('station_id,observed_at_utc,temperature_c,temperature_qc\n37002,2024-03-01T18:00:00Z,-1.2,0\n', encoding='utf-8')
            m = {'station_id': '37002', 'sha256': subject.sha(path), 'source_url': 'https://institution.example/export',
                 'institution': 'TEST FIXTURE ONLY', 'obtained_at_utc': STAMP, 'qc_definition_url': 'http://meteo.ru/data/basic-parameters/',
                 'original_filename': path.name, 'time_basis': 'UTC', 'temperature_units': 'degC'}
            manifest = directory/'source.json'; manifest.write_text(json.dumps(m), encoding='utf-8')
            original_bytes = path.read_bytes()
            with patch.object(subject, 'DIRECTORY', directory/'output'), patch.object(subject, 'reference', return_value=[provider()]), patch.object(subject, 'preserve', return_value={}):
                result = subject.import_and_compare(path, manifest, 'csv')
                self.assertEqual(result['all_terms']['comparable_qc0_pairs'], 1)
                saved = subject.read(Path(result['output'])/'comparison.json')
                self.assertFalse(saved['production_admitted']); self.assertFalse(saved['existing_targets_modified'])
                self.assertIn('asserted_by_importer', saved['source_authenticity'])
                with self.assertRaises(ValueError): subject.import_and_compare(path, manifest, 'csv')
            self.assertEqual(path.read_bytes(), original_bytes)

    def test_wrong_official_station_cannot_match_on_time_alone(self):
        row = {**official(), 'station_id': '37001'}
        with self.assertRaises(ValueError): subject.compare([row], [provider()], [STAMP])


if __name__ == '__main__': unittest.main()
