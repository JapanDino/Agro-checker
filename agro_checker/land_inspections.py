"""User-entered landmark inspections, pinned to immutable satellite evidence.

No seeded observations, identity verification, diagnosis or forecast scoring.
"""
import base64
import binascii
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import re
import os
from uuid import uuid4

from .field_store import digest, encoded, stamp
from .product_data import sha

KIND='land_inspection'
STREAM='landmark-01'
MAX_PHOTOS=3
MAX_PHOTO_BYTES=5_000_000
MAX_BODY_BYTES=21_000_000
MIME={'JPEG':'image/jpeg','PNG':'image/png','WEBP':'image/webp'}


def reference_for(result, period):
    if period=='recent':
        from .sentinel_updates import DIRECTORY
        manifest=sha(DIRECTORY/'runs'/result['observation_version']/'manifest.json')
    else: manifest=result['observation_version']
    return {'period':period,'before':result['before'].get('map_key') or result['before']['id'],
            'after':result['after'].get('map_key') or result['after']['id'],
            'observation_version':result['observation_version'],'manifest_sha256':manifest,
            'comparison_source_sha256':sha(Path(__file__).with_name('land_change.py'))}


def resolve(reference):
    """Resolve the exact published version, even after the live window has changed."""
    if not isinstance(reference,dict) or set(reference)!={'period','before','after','observation_version','manifest_sha256','comparison_source_sha256'}:
        raise ValueError('Нужна сохранённая ссылка на пару снимков')
    if any(not isinstance(v,str) or len(v)>100 for v in reference.values()): raise ValueError('Некорректная ссылка на снимки')
    if reference['comparison_source_sha256']!=sha(Path(__file__).with_name('land_change.py')):
        raise ValueError('Расчёт сравнения обновлён. Повторите сравнение и выбор места перед сохранением')
    from .land_change import compare, comparison
    if reference['period']=='archive':
        d=comparison('archive',reference['before'],reference['after'])
        if d['observation_version']!=reference['observation_version'] or d['observation_version']!=reference['manifest_sha256']:
            raise ValueError('Версия архивных наблюдений не совпадает')
    elif reference['period']=='recent':
        from .sentinel_updates import DIRECTORY, _load
        version=reference['observation_version'];manifest=reference['manifest_sha256']
        if not re.fullmatch('[a-f0-9]{32}',version) or not re.fullmatch('[a-f0-9]{64}',manifest):
            raise ValueError('Некорректная версия наблюдений')
        snapshot,maps=_load(str(DIRECTORY),version,manifest)
        lookup={s.get('map_key'):s for s in snapshot['scenes'] if s.get('map_key')}
        a,b=reference['before'],reference['after']
        if a not in lookup or b not in lookup: raise ValueError('Нет выбранной пары в этой версии')
        ma,mb=maps[a],maps[b]
        d=compare(lookup[a],lookup[b],ma,mb,snapshot['reference'])
        d.update(observation_version=version,maps={'before':ma,'after':mb})
    else: raise ValueError('Неизвестный период')
    d['inspection_reference']=dict(reference)
    return d


def photo_input(raw):
    if not isinstance(raw,dict) or set(raw)!={'filename','data_base64'}: raise ValueError('Некорректное приложение')
    name=raw['filename'];value=raw['data_base64']
    if not isinstance(name,str) or not 1<=len(name)<=200 or any(ord(c)<32 for c in name): raise ValueError('Некорректное имя фотографии')
    if not isinstance(value,str) or len(value)>((MAX_PHOTO_BYTES+2)//3)*4: raise ValueError('Фото должно быть не больше 5 МБ')
    try: data=base64.b64decode(value,validate=True)
    except (binascii.Error,ValueError): raise ValueError('Фотография не прочитана') from None
    if not 0<len(data)<=MAX_PHOTO_BYTES: raise ValueError('Фото должно быть не больше 5 МБ')
    from PIL import Image, UnidentifiedImageError
    try:
        with Image.open(io.BytesIO(data),formats=list(MIME)) as image:
            width,height=image.size;fmt=image.format
            if width*height>32_000_000 or max(width,height)>12000: raise ValueError('Фото должно быть не больше 32 мегапикселей')
            if getattr(image,'n_frames',1)!=1: raise ValueError('Нужна обычная фотография без анимации')
            image.load()
    except (UnidentifiedImageError,OSError,SyntaxError,Image.DecompressionBombError):
        raise ValueError('Нужен исправный файл JPEG, PNG или WebP') from None
    key=__import__('hashlib').sha256(data).hexdigest()
    return {'id':key,'sha256':key,'mime':MIME[fmt],'bytes':len(data),'width':width,'height':height,
            'original_name':name,'photograph_time_or_gps_verified':False},data


def _blob(directory, key, suffix, content):
    path=directory/(key+suffix);directory.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if sha(path)!=key: raise ValueError('Нарушена целостность сохранённого приложения')
    else:
        temporary=directory/(key+'.'+uuid4().hex+'.tmp')
        try:
            with temporary.open('xb') as out:
                out.write(content);out.flush();os.fsync(out.fileno())
            if path.exists():
                if sha(path)!=key: raise ValueError('Конфликт сохранённого приложения')
            else: temporary.rename(path)
        finally: temporary.unlink(missing_ok=True)
    return path


def _record(store, key):
    if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{32}',key): raise KeyError('Нет такой записи осмотра')
    item=next((r for r in store.records(KIND,STREAM) if r['id']==key),None)
    if item is None: raise KeyError('Нет такой записи осмотра')
    return item


def listing(store):
    return list(reversed(store.records(KIND,STREAM)))


def saved_comparison(store, key):
    item=_record(store,key);h=item['payload']['comparison_artifact_sha256']
    if not re.fullmatch('[a-f0-9]{64}',h): raise ValueError('Некорректная ссылка на сравнение')
    path=store.directory/'land-inspection-contexts'/(h+'.json')
    if sha(path)!=h: raise ValueError('Сохранённое сравнение не прошло проверку целостности')
    return json.loads(path.read_text(encoding='utf-8'))


def saved_photo(store, key, photo_id):
    item=_record(store,key)
    photo=next((p for p in item['payload']['photos'] if p['id']==photo_id),None)
    if photo is None or not re.fullmatch('[a-f0-9]{64}',photo_id): raise KeyError('Нет такой фотографии')
    path=store.directory/'land-inspection-media'/(photo_id+'.bin')
    if sha(path)!=photo['sha256']: raise ValueError('Фотография не прошла проверку целостности')
    return path.read_bytes(),photo['mime']


def save(store, raw, now=None):
    now=now or datetime.now(timezone.utc)
    allowed={'request_id','reference','window_id','metric','threshold','actor','observed_at','notes','photos'}
    if not isinstance(raw,dict) or set(raw)!=allowed: raise ValueError('Нужны место, дата, автор, заметки и выбранная пара снимков')
    request_id=raw['request_id']
    if not isinstance(request_id,str) or not re.fullmatch('[a-f0-9]{32}',request_id): raise ValueError('Обновите черновик записи')
    request_hash=digest(raw)
    # Fast retry path: a lost response must not duplicate a real observation.
    for item in store.records(KIND,STREAM):
        if item['payload']['request_id']==request_id:
            if item['payload']['request_sha256']!=request_hash: raise ValueError('Этот запрос уже сохранён с другим содержимым')
            return item
    actor=raw['actor'];notes=raw['notes'];when=stamp(raw['observed_at'])
    if not isinstance(actor,str) or not 1<=len(actor.strip())<=120: raise ValueError('Укажите, кто проводил осмотр')
    if not isinstance(notes,str) or not 1<=len(notes.strip())<=10000: raise ValueError('Запишите фактические наблюдения, до 10 000 символов')
    if when>now or when.year<2000: raise ValueError('Дата фактического осмотра должна быть в прошлом, с 2000 года')
    if raw['metric'] not in ['ndvi','ndmi'] or type(raw['threshold']) not in [int,float] or raw['threshold'] not in [.05,.1,.15,.2]:
        raise ValueError('Неизвестная настройка сравнения')
    if not isinstance(raw['photos'],list) or len(raw['photos'])>MAX_PHOTOS: raise ValueError('Можно приложить до трёх фотографий')
    photos=[photo_input(p) for p in raw['photos']]
    if len({p['id'] for p,b in photos})!=len(photos): raise ValueError('Одна и та же фотография выбрана несколько раз')
    d=resolve(raw['reference']);window=next((b for b in d['blocks'] if b['id']==raw['window_id']),None)
    if window is None: raise ValueError('Нет выбранного окна в этой паре снимков')
    from .land_change import inspection
    artifact=encoded(d).encode();artifact_hash=__import__('hashlib').sha256(artifact).hexdigest()
    payload={'schema_version':1,'request_id':request_id,'request_sha256':request_hash,
             'actor':actor.strip(),'observed_at_utc':when.isoformat(),'notes':notes.strip(),
             'reference':d['reference'],'location_source':'selected_60m_window_not_measured_gps',
             'orchard_boundary_known':False,'selected_window':window,
             'comparison_artifact_sha256':artifact_hash,'comparison_reference':d['inspection_reference'],
             'before':d['before'],'after':d['after'],'comparison_policy':d['policy'],
             'metric':raw['metric'],'display_threshold':raw['threshold'],
             'passed_inspection_filter':inspection(window,raw['metric'],raw['threshold'],d['processing_comparable']),
             'photos':[p for p,b in photos],'observation_source':'manual_unverified_report',
             'automatic_diagnosis':None,'forecast_scoring_eligible':False,'model_training_eligible':False}
    # Recheck idempotency inside a write transaction, including parallel processes.
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        for key,body in c.execute('SELECT id,payload FROM events WHERE kind=? AND profile_id=?',(KIND,STREAM)):
            old=json.loads(body)
            if old['request_id']==request_id:
                if old['request_sha256']!=request_hash: raise ValueError('Этот запрос уже сохранён с другим содержимым')
                return _record(store,key)
        _blob(store.directory/'land-inspection-contexts',artifact_hash,'.json',artifact)
        for p,b in photos: _blob(store.directory/'land-inspection-media',p['id'],'.bin',b)
        return store.append(KIND,STREAM,payload,now,c)
