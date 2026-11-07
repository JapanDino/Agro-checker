"""Read-only availability audit of three cached full ISD station-years.
No rounding, observation imputation, model fits or replacement of old datasets.
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import gzip
import json
import hashlib
import math

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT/'data/working-zone-01/regional-sources'


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def site_availability(rows):
    positions = {(r['latitude'], r['longitude'], r['elevation_m']) for r in rows}
    if len(positions) > 1: raise ValueError('Separate physical positions before counting complete nights')
    grouped = defaultdict(list)
    for r in rows:
        if r['temperature_c'] is not None and r['quality'] in ['1', '5']:
            grouped[r['observed_at_utc']].append(r['temperature_c'])
    conflicted = {t for t, values in grouped.items() if len(set(values)) > 1}
    unique = {t: values[0] for t, values in grouped.items() if t not in conflicted}
    complete = 0; partial = 0; slots = 0
    for d in range(92):
        start = datetime(2024, 3, 1, 16, tzinfo=timezone.utc)+timedelta(days=d)
        stamps = [(start+timedelta(hours=h)).isoformat() for h in range(14)]
        n = sum(t in unique for t in stamps)
        complete += n == 14; partial += n > 0; slots += n
    return {'conflicting_qc1_or5_timestamps_including_dawn': len(conflicted),
            'exact_14_hour_pilot_nights_qc1_or5': complete, 'nights_with_any_exact_hour': partial,
            'exact_hour_pilot_slots_present_qc1_or5': slots, 'expected_pilot_slots': 1288}


def audit(sid):
    path = DIRECTORY/'raw'/f'{sid}-2024-full-isd.gz'
    metadata = json.loads(path.with_name(path.name+'.meta.json').read_text(encoding='utf-8'))
    assert sha(path) == metadata['sha256']
    rows = []; annual = 0
    for line in gzip.decompress(path.read_bytes()).decode('ascii').splitlines():
        assert len(line) >= 105 and line[4:15] == sid.replace('-', '')
        t = datetime.strptime(line[15:27], '%Y%m%d%H%M').replace(tzinfo=timezone.utc)
        assert t.year == 2024
        annual += 1
        if t.month not in [3, 4, 5] and not (t.month == 6 and t.day == 1 and t.hour <= 5): continue
        raw_temp = line[87:92]; flag = line[92]
        assert len(raw_temp) == 5 and raw_temp[0] in '+-' and raw_temp[1:].isdigit()
        temp = None if raw_temp == '+9999' else int(raw_temp)/10
        assert temp is None or -93.2 <= temp <= 61.8
        rows.append({'observed_at_utc': t.isoformat(), 'temperature_c': temp, 'quality': flag,
                     'report_type': line[41:46], 'latitude': int(line[28:34])/1000,
                     'longitude': int(line[34:41])/1000, 'elevation_m': int(line[46:51])})
    spring = [r for r in rows if r['observed_at_utc'][5:7] in ['03', '04', '05']]
    primary = [r for r in rows if r['temperature_c'] is not None and r['quality'] in ['1', '5']]
    grouped = defaultdict(list)
    for r in primary: grouped[r['observed_at_utc']].append(r)
    cross_site_times = sum(len({(r['latitude'],r['longitude'],r['elevation_m']) for r in rr}) > 1 for rr in grouped.values())
    cross_site_disagreements = sum(len({(r['latitude'],r['longitude'],r['elevation_m']) for r in rr}) > 1 and len({r['temperature_c'] for r in rr}) > 1 for rr in grouped.values())
    sites = []
    for position in sorted({(r['latitude'],r['longitude'],r['elevation_m']) for r in rows}):
        sub = [r for r in rows if (r['latitude'],r['longitude'],r['elevation_m']) == position]
        ss = [r for r in sub if r['observed_at_utc'][5:7] in ['03','04','05']]
        lat,lon,elev = position; p1,p2=map(math.radians,[45.024106,lat]); dl=math.radians(lon-37.911914)
        distance=6371*2*math.asin(math.sqrt(math.sin((p2-p1)/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2))
        sites.append({'latitude':lat,'longitude':lon,'elevation_m':elev,'distance_to_farm_km':distance,
                      'spring_reports':len(ss),'spring_temperature_qc_counts':dict(Counter(r['quality'] for r in ss)),
                      'report_types':dict(Counter(r['report_type'] for r in ss)),**site_availability(sub)})
    accepted_spring = [r for r in spring if r['temperature_c'] is not None and r['quality'] in ['1', '5']]
    return {'station_id': sid, 'source': metadata, 'annual_records': annual,
            'spring_records': len(spring), 'spring_temperature_present': sum(r['temperature_c'] is not None for r in spring),
            'spring_temperature_qc_counts': dict(Counter(r['quality'] for r in spring)),
            'spring_primary_qc1_or5_reports': len(accepted_spring),
            'spring_unique_temperature_times': len({r['observed_at_utc'] for r in accepted_spring}),
            'spring_minutes': dict(Counter(r['observed_at_utc'][14:16] for r in spring)),
            'spring_report_types': dict(Counter(r['report_type'] for r in spring)),
            'sites_separated_by_position': sites,
            'timestamps_with_multiple_positions':cross_site_times,
            'cross_position_temperature_disagreements':cross_site_disagreements,
            'pooling_positions_permitted':False,
            'not_isd_lite': True, 'timestamps_rounded': False, 'is_orchard_truth': False}


if __name__ == '__main__':
    r = {'working_point': [45.024106, 37.911914], 'year': 2024,
         'stations': [audit(s) for s in ['370010-99999', '349240-99999', '349150-99999']],
         'quality_definition': 'NOAA ISD air-temperature quality, codes1 and5 passed all QC; not Roshydromet QC0/1/2 meanings',
         'format_source': 'https://www.ncei.noaa.gov/pub/data/noaa/isd-format-document.pdf',
         'format_source_sha256': sha(DIRECTORY/'raw/isd-format-document.pdf'),
         'audit_source_sha256': sha(Path(__file__)), 'models_modified': False, 'new_model_fit': False,
         'control_years_accessed_for_tuning': False, 'scope': 'Availability only, not model accuracy'}
    (DIRECTORY/'availability.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'docs/REGIONAL_DATA_AVAILABILITY.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
    for s in r['stations']:
        print({k: s[k] for k in ['station_id','spring_records','spring_temperature_qc_counts','timestamps_with_multiple_positions','cross_position_temperature_disagreements','sites_separated_by_position']})
