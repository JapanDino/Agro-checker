"""Package browser-captured PNGs without modifying their pixels or inventing UI data."""
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import re
import zipfile

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/scouting-design-v2'
BASE = 'http://127.0.0.1:8770/scouting'
SCREENS = [
    ('01-desktop-farm.png','Карта хозяйства','Все приблизительные площади и реальное архивное изменение R11.','map','ndmi'),
    ('02-desktop-selected-area.png','Выбранная площадь','Фокус на R11 с окружающей спутниковой подложкой.','map','ndmi'),
    ('03-desktop-ndvi.png','Изменение NDVI','Съёмки 21 сентября и 14 октября 2026; пропуски сохранены.','changes','ndvi'),
    ('04-desktop-ndmi.png','Изменение NDMI','Та же пара дат; индекс не является измерением влажности почвы.','changes','ndmi'),
    ('05-desktop-task.png','Задача на осмотр','Пустая форма и отдельные сводки площади и выбранного окна.','task','ndmi'),
    ('06-desktop-empty-history.png','История без записей','Фактических посещений пока нет; история не заполнена примерами.','history','ndmi'),
    ('07-desktop-result-preview.png','Форма результата','Режим просмотра: поля пустые, сохранение отключено.','history','ndmi'),
    ('08-mobile-ndmi.png','Снимки на телефоне','Компактная навигация, две карты и закреплённое действие.','changes','ndmi'),
    ('09-mobile-task.png','Задача на телефоне','Основание раскрывается отдельно; поля не заполнены выдуманными данными.','task','ndmi'),
    ('10-mobile-empty-history.png','Пустая история на телефоне','Следующий шаг понятен до первого фактического осмотра.','history','ndmi'),
    ('11-mobile-result-preview.png','Форма осмотра на телефоне','Видна отметка просмотра; реальная запись не создаётся.','history','ndmi'),
    ('12-mobile-selected-area.png','Карта на телефоне','Приближение R11 и видимая активная площадь в списке; страница прокручена.','map','ndmi'),
    ('13-mobile-result-actions.png','Действия формы результата','Фото и подтверждение реальности; сохранение недоступно в просмотре.','history','ndmi'),
]


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    items=[]
    for name,title,description,step,metric in SCREENS:
        p=OUT/'screens'/name
        with Image.open(p) as image:
            image.verify()
        with Image.open(p) as image:width,height=image.size
        items.append(dict(file='screens/'+name,title=title,description=description,
                          width_px=width,height_px=height,sha256=sha(p),
                          captured_at_utc=datetime.fromtimestamp(p.stat().st_mtime,timezone.utc).isoformat(),
                          layout='mobile' if 'mobile' in name else 'desktop',
                          preview_only='preview' in name or 'result-actions' in name,
                          source_url=f'{BASE}#{step}?area=R11&period=recent2026&metric={metric}'))
    css=(ROOT/'web/scouting/styles.css').read_text(encoding='utf-8')
    palette=dict(re.findall(r'--([\w-]+):\s*(#[0-9a-fA-F]+)',css.split('}',1)[0]))
    tokens=dict(palette=palette,fonts=dict(interface='Segoe UI, Arial, sans-serif',headlines='Georgia, Times New Roman, serif'),
                radius_px=dict(card=18,button=10,input=9),spacing_px=[4,8,12,16,18,22,26,28],
                source='web/scouting/styles.css')
    (OUT/'design-tokens.json').write_text(json.dumps(tokens,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    manifest=dict(schema=1,created_at_utc=datetime.now(timezone.utc).isoformat(),
                  product='Onlyfarms scouting UX v2',capture_method='Actual browser screenshots; pixels are not edited',
                  source_fragment='R11, approximate screenshot fragment; not confirmed field no. 11',
                  archive_dates=['2026-09-21','2026-10-14'],real_field_visits_created=0,tasks_created_for_screens=0,
                  measured_quality_on_farm=False,model_changed=False,
                  viewport_targets_css_px=dict(desktop=[1440,1000],mobile=[390,844]),
                  dimensions_note='PNG dimensions are the actual browser capture sizes listed per file; scrollbar and viewport capture may reduce these relative to CSS target sizes.',
                  sources_sha256={name:sha(ROOT/name) for name in ['web/scouting/index.html','web/scouting/styles.css','web/scouting/app.js']},
                  screens=items)
    (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    cards={key:[] for key in ['desktop','mobile']}
    for i,item in enumerate(items,1):
        e=lambda s:html.escape(str(s),quote=True)
        cards[item['layout']].append(f'''<figure><a class="screen" href="{e(item['file'])}" target="_blank" rel="noopener"><img src="{e(item['file'])}" width="{item['width_px']}" height="{item['height_px']}" loading="lazy" alt="{e(item['title'])}"></a><figcaption><span class="number">{i:02d}</span><div><h3>{e(item['title'])}</h3><p>{e(item['description'])}</p><small>{item['width_px']} × {item['height_px']} px · PNG</small><div class="links"><a href="{e(item['file'])}" download>Скачать PNG ↓</a><a href="{e(item['source_url'])}" target="_blank" rel="noopener">Открыть раздел ↗</a></div></div></figcaption></figure>''')
    swatches=''.join(f'<span><i style="background:{html.escape(v)}"></i>{html.escape(k)} <b>{html.escape(v)}</b></span>' for k,v in palette.items() if k in ['bg','ink','green','lime','accent'])
    page='''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Onlyfarms — экраны интерфейса для дизайна</title><style>
*{box-sizing:border-box}body{margin:0;background:#f2f4ec;color:#223c30;font-family:Segoe UI,Arial,sans-serif}header,main,footer{max-width:1440px;margin:auto;padding:28px 4vw}header{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #dce3d3}.brand{font-size:25px;font-weight:700;letter-spacing:-1px}a{color:inherit;text-decoration:none}a:hover{text-decoration:underline}.hero{padding:30px 0 20px}.eyebrow{font-size:10px;letter-spacing:2px;color:#738366;font-weight:600}h1{font-family:Georgia,serif;font-size:clamp(32px,4vw,55px);font-weight:400;letter-spacing:-1.5px;margin:16px 0}p{font-size:14px;line-height:1.7;color:#69796d;max-width:740px}.pills,.palette{display:flex;gap:10px;flex-wrap:wrap}.pills a,.pills span{padding:9px 13px;background:#e8efda;border-radius:24px;font-size:12px}.palette{margin:25px 0 34px;gap:20px}.palette span{display:flex;align-items:center;gap:8px;font-size:11px;color:#718067}.palette b{font-size:10px;font-weight:400}.palette i{width:20px;height:20px;border-radius:6px;border:1px solid #223c3011}.section-head{display:flex;justify-content:space-between;align-items:center;margin:35px 0 18px;border-top:1px solid #dce3d3;padding-top:27px}.section-head h2{font-size:23px;font-weight:600;margin:0}.section-head span{font-size:11px;color:#718067}.grid{display:grid;grid-template-columns:1fr 1fr;gap:24px}.mobile{grid-template-columns:repeat(3,1fr)}figure{margin:0;padding:12px;border:1px solid #dfe5d7;border-radius:19px;background:#fff;box-shadow:0 5px 25px #223c3004}.screen{display:block;border:1px solid #e6eadf;border-radius:10px;overflow:hidden;background:#f4f6ef}.screen img{display:block;width:100%;height:auto}.mobile .screen{max-width:300px;margin:auto}figcaption{display:flex;gap:13px;padding:19px 7px 9px}.number{font-size:10px;color:#849572;padding-top:5px}h3{font-size:16px;font-weight:600;margin:0 0 6px}figcaption p{font-size:12px;line-height:1.6;margin:0 0 9px}small{font-size:10px;color:#89947e}.links{display:flex;gap:17px;margin-top:13px;font-size:11px;color:#396440}.note{border-left:3px solid #b8ce90;background:#eaf1de;padding:16px 20px;border-radius:0 10px 10px 0;font-size:12px;line-height:1.8;color:#536849}.notes{margin-top:35px}footer{font-size:11px;color:#78876c;border-top:1px solid #dce3d3;display:flex;gap:24px}a:focus-visible{outline:3px solid #8da95d;outline-offset:4px}@media(max-width:850px){.grid{grid-template-columns:1fr}.mobile{grid-template-columns:1fr 1fr}.hero{padding-top:10px}.palette{gap:13px}}@media(max-width:500px){header,main,footer{padding-inline:18px}.mobile{grid-template-columns:1fr}.section-head span{display:none}h1{font-size:37px}.brand{font-size:23px}}
</style></head><body><header><a class="brand" href="#">onlyfarms</a><a href="http://127.0.0.1:8770/scouting">Рабочий прототип ↗</a></header><main><div class="hero"><div class="eyebrow">UX V2 / ЭКРАНЫ ДЛЯ ДИЗАЙНА</div><h1>От снимка к осмотру.</h1><p>13 настоящих скриншотов работающего интерфейса: карта хозяйства, изменение индексов, задача и история места. Их можно использовать как референсы и импортировать в макет редактора дизайна.</p><div class="pills"><a href="#desktop">7 экранов компьютера</a><a href="#mobile">6 экранов телефона</a><span>Архив Sentinel-2 · R11</span></div><div class="palette">PALETTE</div><div class="note">Посещения не выдуманы: журнал пуст. Формы показаны без фиктивных авторов, заметок или фото. На экранах просмотра результата сохранение отключено. Границы площадей приблизительные; точность агрономических прогнозов здесь не заявляется.</div></div><section id="desktop"><div class="section-head"><h2>На компьютере</h2><span>Карта → снимки → задача → история</span></div><div class="grid">DESKTOP</div></section><section id="mobile"><div class="section-head"><h2>В поле, с телефона</h2><span>Закреплённое действие и компактный контекст</span></div><div class="grid mobile">MOBILE</div></section><div class="notes"><p>PNG сохранены непосредственно из браузера, без изменения пикселей. Размеры каждого исходника указаны под изображением. Точные размеры отличаются от CSS viewport из-за особенностей захвата и полос прокрутки. Мобильные экраны задачи и карты частично прокручены; экран 13 показывает нижнюю часть той же формы просмотра.</p><p>«Открыть раздел» восстанавливает площадь, период, индекс и этап. Приближение карты, прокрутка и открытие формы просмотра выбираются отдельно в интерфейсе.</p></div></main><footer><a href="manifest.json">Данные набора и контрольные суммы</a><a href="design-tokens.json">Цвета и типографика</a><a href="README.txt">Что изменилось в UX</a></footer></body></html>'''
    page=page.replace('PALETTE',swatches).replace('DESKTOP',''.join(cards['desktop'])).replace('MOBILE',''.join(cards['mobile']))
    (OUT/'index.html').write_text(page,encoding='utf-8')
    (OUT/'README.txt').write_text('''Onlyfarms — набор экранов UX v2

Откройте index.html в браузере. Оригинальные PNG находятся в screens/.
Каждый файл можно импортировать в Figma или другой редактор как растровый референс.
Это реальные снимки рабочего интерфейса, не редактируемые компоненты Figma.

Изменения UX:
— компактные заголовки, единая типографика, спокойная палитра;
— выбранная площадь видна в списке, на карте и в строке контекста;
— кнопка приближения к выбранной площади, включая телефон;
— поиск площадей доступен на телефоне;
— основной переход закреплён снизу на телефоне и невысоком экране компьютера;
— на телефоне рабочие этапы открываются без большого повторного заголовка;
— основание задачи раскрывается отдельно от формы;
— технические подробности расчёта находятся в раскрываемом блоке;
— ссылка сохраняет площадь, период, индекс и раздел; работают обновление и Назад;
— незаписанный черновик сохраняется при переходах между этапами этой вкладки;
— режим просмотра формы результата не позволяет сохранить посещение.

Данные:
R11 — условная обводка на карте, не подтверждённое поле №11.
Карты индексов относятся к 21.09.2026 и 14.10.2026; это архивное изменение,
а не текущая тревога. Индексы не устанавливают причину изменения поверхности.
Задач и фактических посещений для этих скриншотов не создавалось.
Макеты пустого состояния и форм нельзя представлять как выполненные осмотры.

Размеры:
Исходные размеры PNG записаны в manifest.json и галерее. Они могут отличаться
от заданных CSS viewport 1440×1000 и 390×844 из-за захвата и полос прокрутки.
Пиксели файлов не масштабировались и не ретушировались после захвата.
В manifest.json также находятся SHA256 файлов и ссылки на разделы прототипа.

Прототип: http://127.0.0.1:8770/scouting
Для галереи подключение к сервису не требуется; нужны index.html и папка screens/.
''',encoding='utf-8')
    target=OUT.with_suffix('.zip')
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for path in sorted(OUT.rglob('*')):
            if path.is_file():z.write(path,path.relative_to(OUT))
    with zipfile.ZipFile(target) as z:
        if z.testzip():raise ValueError('Package checksum failure')
    print(f'{len(items)} screenshots; {target}; {target.stat().st_size} bytes')


if __name__=='__main__':main()
