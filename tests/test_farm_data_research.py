from html import escape
import unittest
from agro_checker.farm_data_research import parse_krymsk, parse_daily, optional_number


def page(year=2024, month=3, station='37002', wind='2', temp='-1.5'):
    cells = ['С', wind, '10 км', '', 'ясно', temp, '-2.0', '87', '', '', '', '1020', '1015', '-2.2', '', '', '', '']
    return (f'<title>Архив погоды за март {year} года</title><input name="id" value="{station}">'
            f'<table><tr><td>Время (UTC), дата</td></tr><tr><td>00</td><td>1.{month:02}</td></tr></table>'
            '<table><tr><td>Т (С)</td><td>Тmin (С)</td></tr><tr>'+''.join('<td>'+escape(v)+'</td>' for v in cells)+'</tr></table>').encode()


class FarmDataResearchTests(unittest.TestCase):
    def test_temperature_columns_and_timezone_are_exact(self):
        r = parse_krymsk(page(), 2024, 3)[0]
        self.assertEqual(r['temperature_c'], -1.5)
        self.assertEqual(r['observed_at_utc'], '2024-03-01T00:00:00+00:00')
        self.assertEqual(r['published_minimum_c'], -2.2)
        self.assertFalse(r['is_orchard_measurement'])
        self.assertIn('unverified', r['published_minimum_window'])

    def test_wrong_year_or_month_response_rejected(self):
        with self.assertRaises(ValueError): parse_krymsk(page(year=2023), 2024, 3)
        with self.assertRaises(ValueError): parse_krymsk(page(month=10), 2024, 3)

    def test_wrong_station_and_control_year_rejected(self):
        with self.assertRaises(ValueError): parse_krymsk(page(station='34929'), 2024, 3)
        for year in [2025, 2026]:
            with self.assertRaises(ValueError): parse_krymsk(page(year=year), year, 3)

    def test_wind_range_is_not_invented_as_mean_or_gust(self):
        r = parse_krymsk(page(wind='6-11 {12}'), 2024, 3)[0]
        self.assertIsNone(r['wind_ms']); self.assertTrue(r['wind_numeric_unparsed'])
        self.assertEqual(r['temperature_c'], -1.5)

    def test_missing_temperature_remains_missing_not_zero(self):
        self.assertIsNone(parse_krymsk(page(temp=''), 2024, 3)[0]['temperature_c'])
        with self.assertRaises(ValueError): optional_number('nan', -80, 60)

    def test_duplicate_or_unaligned_tables_rejected(self):
        h = page().decode(); h = h.replace('<td>00</td><td>1.03</td></tr>', '<td>00</td><td>1.03</td></tr><tr><td>00</td><td>1.03</td></tr>')
        with self.assertRaises(ValueError): parse_krymsk(h.encode(), 2024, 3)

    def test_italic_temperature_is_model_fill_not_ground_truth(self):
        h = page().decode().replace('<td>-1.5</td>', '<td><i>-1.5</i></td>')
        r = parse_krymsk(h.encode(), 2024, 3)[0]
        self.assertIsNone(r['temperature_c']); self.assertEqual(r['reported_temperature_c'], -1.5)
        self.assertTrue(r['provider_model_filled_temperature'])

    def test_italic_weather_description_does_not_exclude_real_temperature(self):
        h = page().decode().replace('<td>ясно</td>', '<td><i>ясно</i></td>')
        r = parse_krymsk(h.encode(), 2024, 3)[0]
        self.assertEqual(r['temperature_c'], -1.5); self.assertFalse(r['provider_model_filled_temperature'])

    def test_italic_row_is_not_admitted_as_observations(self):
        h = page().decode().replace('<td>С</td>', '<td>С</td>')
        h = h.replace('<tr><td>С</td>', '<tr style="font-style: italic"><td>С</td>')
        self.assertIsNone(parse_krymsk(h.encode(), 2024, 3)[0]['temperature_c'])

    def test_daily_quality_and_source_flags_preserved(self):
        row = 'RSM00034924'+'2024'+'03'+'TMIN'+f'{-15:5d} S'+'S'+f'{-9999:5d}   '*30
        self.assertEqual(len(row), 269)
        r = parse_daily(row.encode(), 'RSM00034924')[0]
        self.assertFalse(r['admitted_descriptive']); self.assertEqual(r['quality_flag'], 'S')
        self.assertEqual(r['source_flag'], 'S'); self.assertFalse(r['is_pilot_night_minimum'])

    def test_html_instead_of_station_csv_is_not_a_daily_dataset(self):
        with self.assertRaises(ValueError): parse_daily(b'<!DOCTYPE html><html>Directory index</html>', 'RSM00034924')


if __name__ == '__main__': unittest.main()
