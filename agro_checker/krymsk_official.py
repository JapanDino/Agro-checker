"""Official-source audit and exact-term comparison. No model fitting or routing.

QC0 observations are primary. Official manual/automatic reconstructions remain
separate audit groups. A source specification or empty template is never data.
"""
from collections import Counter
from datetime import datetime, timedelta
import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path
from zipfile import ZipFile, BadZipFile
from xml.etree import ElementTree as ET
from .pipeline import ROOT, UTC, write_json
from .product_data import sha

DIRECTORY = ROOT/'data/working-zone-01/krymsk-official'
RAW = DIRECTORY/'raw'
PROTOCOL = ROOT/'config/krymsk_official_comparison.json'
QUALITY = {0: 'reliable_observation', 1: 'reliable_manually_reconstructed', 2: 'reliable_automatically_reconstructed',
           3: 'doubtful', 4: 'rejected_by_syntax_semantic_control', 5: 'missing_despite_observations',
           6: 'rejected_at_station', 7: 'not_observed'}
CSV_FIELDS = ['station_id', 'observed_at_utc', 'temperature_c', 'temperature_qc']


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def expected_times(year):
    if year not in [2022, 2023, 2024]: raise ValueError('Only2022-2024 terms; controls forbidden')
    t = datetime(year, 3, 1, tzinfo=UTC); end = datetime(year, 6, 1, 3, tzinfo=UTC)
    while t <= end:
        yield t.isoformat()
        t += timedelta(hours=3)


def record(station, stamp, raw_temperature, raw_qc):
    if station != '37002': raise ValueError('Wrong station: never substitute a neighbouring site')
    t = datetime.fromisoformat(stamp)
    if t.tzinfo is None or t.utcoffset() != timedelta(0): raise ValueError('Explicit UTC required, no local-time guessing')
    if t.year not in [2022, 2023, 2024]: raise ValueError('Control or unrequested year forbidden')
    if t.minute or t.second or t.microsecond or t.hour % 3: raise ValueError('Not an exact3-hour observation term')
    if t.isoformat() not in set(expected_times(t.year)): raise ValueError('Outside requested seasonal scope')
    if len(raw_qc) != 1 or raw_qc not in '0123456789': raise ValueError('Per-temperature numeric QC flag required')
    q = int(raw_qc); raw_temperature = raw_temperature.strip(); value = None
    if raw_temperature:
        try: value = float(raw_temperature)
        except ValueError:
            if q in [0, 1, 2]: raise ValueError('Valid-QC value is not a documented number')
        if value is not None and not math.isfinite(value): raise ValueError('Nonfinite temperature')
    valid = value is not None and -67 <= value <= 55
    return {'station_id': station, 'observed_at_utc': t.isoformat(), 'raw_temperature_text': raw_temperature,
            'reported_temperature_c': value, 'temperature_qc': q, 'quality_meaning': QUALITY.get(q, 'unknown'),
            'primary_temperature_c': value if q == 0 and valid else None,
            'reconstructed_temperature_c': value if q in [1, 2] and valid else None,
            'quality_value_inconsistent': q in [0, 1, 2] and not valid,
            'is_orchard_truth': False}


def unique(rows):
    if any(r['station_id'] != '37002' for r in rows): raise ValueError('Wrong official station')
    stamps = [r['observed_at_utc'] for r in rows]
    if len(set(stamps)) != len(stamps): raise ValueError('Duplicate station/UTC term; revisions must be resolved explicitly')
    if not rows: raise ValueError('Empty export is not an observation dataset')
    return rows


def parse_csv(content):
    reader = csv.DictReader(io.StringIO(content.decode('utf-8-sig')))
    if reader.fieldnames != CSV_FIELDS: raise ValueError('Expected normalized four-column schema with temperature QC')
    rows = []
    for r in reader:
        if set(r) != set(CSV_FIELDS) or any(v is None for v in r.values()): raise ValueError('Malformed CSV columns')
        rows.append(record(r['station_id'], r['observed_at_utc'], r['temperature_c'], r['temperature_qc']))
    return unique(rows)


def parse_fixed_width(content):
    """Published207-column spec; not yet verified against a real37002 export.

    Temperature is F5.1 at zero-based122:127, its QC at127. Greenwich
    date/hour occupy5:15. Never use the separate local/source-time columns.
    """
    rows = []
    for line in content.decode('ascii').splitlines():
        if len(line) != 207: raise ValueError('Expected207-character official record; no guessed column offsets')
        try:
            t = datetime(int(line[5:9]), int(line[9:11]), int(line[11:13]), int(line[13:15]), tzinfo=UTC)
        except ValueError: raise ValueError('Invalid Greenwich date/hour')
        value, qc = line[122:127], line[127]
        # The actual export is absent: refuse ambiguous implied decimal scales.
        if qc in '012' and value.strip() and '.' not in value:
            raise ValueError('Ambiguous F5.1 scale: explicit decimal point required')
        rows.append(record(line[:5], t.isoformat(), value, qc))
    return unique(rows)


def source_differences(pairs):
    if not pairs: return None
    d = [p['delta_c'] for p in pairs]
    return {'n': len(d), 'mean_official_minus_provider_c': sum(d)/len(d),
            'mean_absolute_difference_c': sum(abs(v) for v in d)/len(d),
            'max_absolute_difference_c': max(abs(v) for v in d),
            'exact': sum(abs(v) <= 1e-8 for v in d),
            'within_published_precision': sum(abs(v) <= .10000001 for v in d),
            'over_0_1c': sum(abs(v) > .10000001 for v in d), 'over_0_5c': sum(abs(v) > .5 for v in d),
            'opposite_zero_classification': sum((p['official_temperature_c'] < 0) != (p['provider_temperature_c'] < 0) for p in pairs)}


def compare(official, provider, expected=None):
    """No fuzzy joins or target edits, including cases of source disagreement."""
    official = unique(official) if official else []
    if expected is None: expected = [t for y in [2022, 2023, 2024] for t in expected_times(y)]
    expected_set = set(expected); local = {}
    for r in provider:
        if r['station_id'] != '37002': raise ValueError('Wrong reference station')
        stamp = r['observed_at_utc']
        if stamp in local: raise ValueError('Duplicate reference timestamp')
        if datetime.fromisoformat(stamp).year not in [2022, 2023, 2024]: raise ValueError('Control labels in reference')
        local[stamp] = r
    index = {r['observed_at_utc']: r for r in official}; pairs = []; reconstructed = {1: [], 2: []}; exclusions = []
    for stamp in expected:
        a = index.get(stamp); b = local.get(stamp)
        actual = None if b is None or b.get('provider_model_filled_temperature') else b['temperature_c']
        if a is not None and actual is not None:
            def paired(value):
                return {'station_id': '37002', 'observed_at_utc': stamp, 'official_temperature_c': value,
                        'provider_temperature_c': actual, 'delta_c': value-actual, 'official_qc': a['temperature_qc']}
            if a['primary_temperature_c'] is not None: pairs.append(paired(a['primary_temperature_c'])); continue
            if a['reconstructed_temperature_c'] is not None:
                reconstructed[a['temperature_qc']].append(paired(a['reconstructed_temperature_c']))
        reasons = []
        if a is None: reasons.append('official_term_absent')
        elif a['primary_temperature_c'] is None: reasons.append('official_not_original_reliable_qc0')
        if actual is None: reasons.append('provider_term_missing_or_model_filled')
        exclusions.append({'observed_at_utc': stamp, 'reasons': reasons, 'official_qc': None if a is None else a['temperature_qc']})
    def scope(stamps):
        selected = [p for p in pairs if p['observed_at_utc'] in stamps]
        return {'expected_terms': len(stamps), 'comparable_qc0_pairs': len(selected), 'differences': source_differences(selected)}
    pilot = {t for t in expected_set if datetime.fromisoformat(t).hour in [18, 21, 0, 3] and
             not (t[5:10] == '03-01' and datetime.fromisoformat(t).hour in [0, 3])}
    return {'status': 'comparison_available' if pairs else 'comparison_unavailable_no_qc0_pairs',
            'official_rows': len(official), 'official_qc_counts': dict(Counter(str(r['temperature_qc']) for r in official)),
            'provider_valid_terms': sum(t in local and local[t]['temperature_c'] is not None and not local[t].get('provider_model_filled_temperature') for t in expected),
            'all_terms': scope(expected_set), 'pilot_terms': scope(pilot),
            'by_year': {str(y): scope({t for t in expected_set if t.startswith(str(y))}) for y in [2022, 2023, 2024]},
            'restored_audit_only': {str(q): source_differences(v) for q, v in reconstructed.items()},
            'pairs': pairs, 'reconstructed_pairs_audit_only': reconstructed, 'exclusions': exclusions,
            'differences_above_precision': [p for p in pairs if abs(p['delta_c']) > .10000001],
            'provider_outside_requested_scope': sum(t not in expected_set for t in local),
            'model_training_performed': False, 'existing_targets_modified': False, 'production_admitted': False}


def inspect_workbook(content):
    try:
        with ZipFile(io.BytesIO(content)) as z:
            if 'xl/workbook.xml' not in z.namelist(): raise ValueError('Not an XLSX workbook')
            ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            sheets = [n for n in z.namelist() if n.startswith('xl/worksheets/') and n.endswith('.xml')]
            populated = sum(len(ET.fromstring(z.read(n)).findall('.//s:sheetData/s:row/s:c', ns)) for n in sheets)
            return {'type': 'XLSX despite passport saying Word', 'worksheets': len(sheets), 'populated_cells': populated,
                    'is_observation_dataset': False, 'empty_template': populated == 0}
    except (BadZipFile, ET.ParseError): raise ValueError('Invalid workbook')


def reference():
    p = ROOT/'data/working-zone-01/research'; audit = read(p/'data-audit.json')['krymsk']
    path = p/'krymsk-three-hour-observations.json'
    if sha(path) != audit['observations_sha256']: raise ValueError('Frozen reference changed')
    return read(path)


def preserve():
    old = ROOT/'data/working-zone-01/krymsk-observed-time'
    checks = read(old/'preserved-artifacts.json')
    checks['data/working-zone-01/krymsk-observed-time/selection-prior-only.json'] = '48cb8ed9288f8648af78fe9059fac284b9a891670802146eb8cbfc22b3631daa'
    checks['data/working-zone-01/krymsk-observed-time/dataset.json'] = '48fee62bed4205699a0a326becc0c8b8a0fc801c1e89c021ba4c197afcd83a4f'
    if any(sha(ROOT/path) != h for path, h in checks.items()): raise ValueError('Existing detector/model/dataset changed')
    return checks


def audit():
    evidence = {}
    for path in RAW.glob('*.meta.json'):
        m = read(path); original = path.with_name(path.name[:-10])
        if sha(original) != m['sha256']: raise ValueError('Evidence bytes changed')
        evidence[original.name] = {k: m[k] for k in ['url', 'status', 'retrieved_at_utc', 'sha256', 'bytes', 'content_type', 'transport', 'method'] if k in m}
    extraction = read(DIRECTORY/'catalogue-extraction.json')
    if extraction['source_sha256'] != sha(RAW/'basic-station-catalogue.pdf'):
        raise ValueError('Wrong catalogue source')
    workbook = inspect_workbook((RAW/'eip-basic-data').read_bytes())
    result = {'source_audit': evidence, 'catalogue': extraction, 'eip_workbook': workbook,
              'official_observations_obtained': False, 'official_krymsk_year_coverage_verified': False,
              'access': 'Aisori public navigation leads to login/password form. No credentials, registration, password recovery or messages submitted.',
              'archive_routes': ['http://meteo.ru/data/basic-parameters/', 'http://aisori-m.meteo.ru/waisori/index.xhtml?idata=2',
                                 'http://meteo.ru/services/data-providing/', 'https://yugmeteo.donpac.ru/ofd/'],
              'comparison': compare([], reference()), 'preserved_artifacts_sha256': preserve(),
              'protocol_sha256': sha(PROTOCOL), 'source_sha256': sha(ROOT/'agro_checker/krymsk_official.py'),
              'candidate_selection_performed': False, 'control_years_used_for_selection': False,
              'created_at_utc': datetime.now(UTC).isoformat()}
    write_json(DIRECTORY/'audit.json', result)
    # Publish evidence metadata/counts, not raw pages, session headers or rows.
    public = {**result, 'comparison': {k: v for k, v in result['comparison'].items()
              if k not in ['exclusions', 'pairs', 'reconstructed_pairs_audit_only']}}
    public['comparison']['exclusion_reason_counts'] = dict(Counter(reason for e in result['comparison']['exclusions'] for reason in e['reasons']))
    write_json(ROOT/'docs/KRYMSK_OFFICIAL_AUDIT_SUMMARY.json', public)
    return result


def import_and_compare(path, manifest_path, format_name):
    """Require original bytes and provenance; official origin remains asserted,
    not authenticated merely by the presence of a URL in the supplied manifest.
    """
    m = read(manifest_path)
    if m.get('sha256') != sha(path) or m.get('station_id') != '37002': raise ValueError('Export manifest/hash/station mismatch')
    for key in ['source_url', 'institution', 'obtained_at_utc', 'qc_definition_url', 'original_filename']:
        if not m.get(key): raise ValueError('Missing export provenance: '+key)
    if m.get('time_basis') != 'UTC' or m.get('temperature_units') != 'degC': raise ValueError('Explicit UTC and degC required')
    if format_name not in ['csv', 'fixed207']: raise ValueError('Unverified input format')
    original = path.read_bytes(); parser = parse_csv if format_name == 'csv' else parse_fixed_width
    official = parser(original); result = compare(official, reference())
    result.update(export_manifest=m, source_authenticity='asserted_by_importer; verify institutional delivery/download separately',
                  preserved_artifacts_sha256=preserve(), protocol_sha256=sha(PROTOCOL),
                  source_sha256=sha(ROOT/'agro_checker/krymsk_official.py'))
    # Never overwrite a previous comparison or the existing target dataset.
    destination = DIRECTORY/'comparisons'/hashlib.sha256(original).hexdigest()
    if (destination/'comparison.json').exists():
        raise ValueError('Comparison already exists; inspect it instead of overwriting')
    write_json(destination/'comparison.json', result)
    write_json(destination/'official-normalized.json', official)
    return {'status': result['status'], 'output': str(destination), 'all_terms': result['all_terms'], 'pilot_terms': result['pilot_terms']}


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--input', type=Path); p.add_argument('--manifest', type=Path)
    p.add_argument('--format', choices=['csv', 'fixed207'], default='csv'); args = p.parse_args()
    if args.input:
        if not args.manifest: p.error('--manifest is required with an export')
        print(json.dumps(import_and_compare(args.input, args.manifest, args.format), ensure_ascii=False))
    else:
        r = audit(); print(json.dumps({'official_observations_obtained': False, 'catalogue': r['catalogue'],
            'comparison': {k: r['comparison'][k] for k in ['status', 'all_terms', 'pilot_terms', 'provider_valid_terms']},
            'preserved_models': True}, ensure_ascii=False))
