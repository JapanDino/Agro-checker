"""Source coverage and provenance, independent of event outcomes."""
import sys,io,csv,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tmp/georef-deps'),str(ROOT/'tmp/open-crop-deps')]
from agro_checker.farm_context_data import read,dump,digest
from agro_checker.seasonal_calibration import stamp
D=ROOT/'data/farm-research/open-crop-context';S=D/'sources'


if __name__=='__main__':
    if (D/'frozen-selection.json').exists():raise SystemExit('Evidence locked after freeze')
    import shapefile
    manifest=read(D/'source-manifest.json')
    for entry in manifest['entries']:
        if 'sha256' in entry:assert digest(S/entry['name'])==entry['sha256']
    record=read(S/'geoglam-v1-record.json');file=record['files'][0]
    import hashlib
    assert hashlib.md5((S/'geoglam-v1.zip').read_bytes()).hexdigest()==file['checksum'].split(':')[1]
    with zipfile.ZipFile(S/'geoglam-v1.zip') as z:
        stem='GEOGLAM_CM4EW_Calendars_V1.0'
        reader=shapefile.Reader(dbf=io.BytesIO(z.read(stem+'.dbf')))
        records=[r.as_dict() for r in reader.iterRecords()]
        # No Russia records: do not invent a calendar by taking a nearby country.
        matches=[r for r in records if 'russ' in r['country'].lower()]
        geoglam=dict(version='1.0',published=record['metadata']['publication_date'],records=len(records),
            countries=sorted({r['country'] for r in records}),russia_records=matches,
            newer2026_version_not_used=True,dbf_reader_version=shapefile.__version__)
    asap={}
    for level in [0,1]:
        with zipfile.ZipFile(S/f'asap-calendar{level}.zip') as z:
            rr=list(csv.DictReader(io.StringIO(z.read(f'crop_calendar_gaul{level}.csv').decode()),delimiter=';'))
        asap[str(level)]=dict(records=len(rr),countries=sorted({r['name0_shr'] for r in rr}),
            russia_records=[r for r in rr if 'russ' in r['name0_shr'].lower()],
            historical_bytes_available2024_verified=False)
    rdm={}
    for name in ['sector2024','region2024','sector-any-date']:
        values=read(S/f'rdm-{name}.json')
        if not isinstance(values,list):raise ValueError('Unexpected RDM collection schema')
        rdm[name]=dict(collection_count=len(values),collections=[r['collectionId'] for r in values])
    rdm['local_items']={}
    rdm['item_query_failures']=[dict(name=e['name'],error=e['error']) for e in manifest['entries'] if e['name'].startswith('rdm-items-') and 'error' in e]
    for path in S.glob('rdm-items-*.json'):
        if path.name.endswith('.meta.json'):continue
        value=read(path)
        if not isinstance(value,dict) or value.get('type')!='FeatureCollection':raise ValueError('Unexpected RDM item response')
        if value.get('links') and any(x.get('rel')=='next' for x in value['links']):raise ValueError('Pagination needs continuation')
        rdm['local_items'][path.stem.removeprefix('rdm-items-')]=dict(features_returned=len(value['features']),
            number_matched=value.get('numberMatched'),number_returned=value.get('numberReturned'))
    evidence=read(ROOT/'data/farm-research/crop-season-model/crop-evidence.json')
    fragments={fid:dict(crop=None,crop_year=None,harvest_date=None,confirmed_boundary=False,
        actual_phase=None,calendar_source='WorldCereal AEZ2023: regional season end, NOT observed harvest',
        scenarios=entry['scenarios']) for fid,entry in evidence['fragments'].items()}
    out=dict(created_at_utc=stamp(),geoglam=geoglam,asap=asap,rdm=rdm,fragments=fragments,
        confirmed_current_crop_labels=0,confirmed_harvest_dates=0,
        field20_user_evidence=dict(crop2024='soybean',spatially_matched=False,training_label=False),
        worldcereal2021=dict(product_year=2021,not_current_crop_truth=True,assigned_to_fragments=False,
            catalogue_currently_only2021=True),
        regional_report=dict(source='krasnodar-2024-06-27.pdf',page=3,publication_date='2024-06-27',
            usable_after='2024-06-28',expires='2025-01-01',scope='Krasnodar city/periurban farms, NOT Krymsk or partner farm',
            assertion='The report describes earlier-than-usual harvest in the region; it does not date harvest on R01-R27.',
            used_to_assign_field_harvest=False),
        source_availability='AEZ file released2023; crop maps2021 remain historical. Same-year27June report unavailable to6/11June predictions. New2026 products excluded.',
        source_manifest_sha256=digest(D/'source-manifest.json'),
        prior_evidence_sha256=digest(ROOT/'data/farm-research/crop-season-model/crop-evidence.json'))
    dump(D/'crop-evidence.json',out)
    print(dict(geoglam_records=len(records),geoglam_russia=len(matches),asap_russia={k:len(v['russia_records']) for k,v in asap.items()},
        rdm=rdm,confirmed_crop_labels=0,confirmed_harvest_dates=0))
