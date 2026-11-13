/* @ds-bundle: {"format":4,"namespace":"AgroPulse","components":[{"name":"Button"},{"name":"StatusChip"},{"name":"Tag"},{"name":"SummaryTile"},{"name":"FieldRow"},{"name":"IndexStat"},{"name":"Segmented"},{"name":"TextField"},{"name":"InspectionMark"},{"name":"Notice"},{"name":"NavRail"},{"name":"TopBar"},{"name":"TodayScreen"}]} */
(function () {
  var React = window.React;
  var h = React.createElement;
  var useState = React.useState;

  function cx() {
    var out = [];
    for (var i = 0; i < arguments.length; i++) if (arguments[i]) out.push(arguments[i]);
    return out.join(' ');
  }
  function rest(props, omit) {
    var o = {};
    for (var k in props) if (Object.prototype.hasOwnProperty.call(props, k) && omit.indexOf(k) < 0) o[k] = props[k];
    return o;
  }
  function svg(paths, viewBox) {
    return h('svg', { viewBox: viewBox || '0 0 24 24', 'aria-hidden': 'true' }, paths.map(function (p, i) {
      if (p.c) return h('circle', { key: i, cx: p.c[0], cy: p.c[1], r: p.c[2] });
      if (p.r) return h('rect', { key: i, x: p.r[0], y: p.r[1], width: p.r[2], height: p.r[3], rx: p.r[4] });
      return h('path', { key: i, d: p });
    }));
  }

  /* Icons — copied from the AgroPulse navigation (web/scouting/index.html) */
  var ICONS = {
    map: ['m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3Z', 'M9 3v15M15 6v15'],
    today: ['M6 16V11a6 6 0 0 1 12 0v5l2 2H4Z', 'M10 21h4'],
    trips: [{ c: [6, 5, 2] }, { c: [18, 19, 2] }, 'M8 5h8a4 4 0 0 1 0 8H8a3 3 0 0 0 0 6h8'],
    analysis: [{ r: [3, 6, 15, 15, 1] }, 'M7 3h14v14M3 17l5-5 4 4 3-3 3 3', { c: [8, 10, 1] }],
    task: ['M8 4H4v17h16V4h-4M8 2h8v5H8Z', 'm8 14 3 3 5-6'],
    history: ['M3 11a9 9 0 1 1 2 7M3 4v7h7', 'M12 7v6l4 2'],
    check: ['m5 12 5 5 9-10'],
    spot: [{ c: [12, 12, 7] }, { c: [12, 12, 2] }],
    info: [{ c: [12, 12, 9] }, 'M12 11v6M12 7.5v.01'],
    alert: ['M12 3 2 20h20Z', 'M12 10v4M12 17.5v.01'],
    arrow: ['M5 12h14M13 6l6 6-6 6']
  };
  var SPROUTS = ['M7 25V15M16 25V10M25 25V16', 'M7 15C2 14 3 7 3 7s7 0 7 5c0 2-3 3-3 3ZM16 10c-1-6 6-8 6-8s3 7-3 9M25 16c-4-3-2-8-2-8s7 2 6 6c-1 2-4 2-4 2Z', 'M4 28h24'];
  function Icon(props) { return svg(ICONS[props.name] || ICONS.info); }

  var STATUS_LABEL = { risk: 'Риск', check: 'Проверить', normal: 'Норма', none: 'Нет правил' };

  function Button(props) {
    var variant = props.variant || 'primary';
    var size = props.size || 'md';
    var p = rest(props, ['variant', 'size', 'wide', 'icon', 'className', 'children']);
    if (!p.type) p.type = 'button';
    p.className = cx('ap-btn', 'ap-btn-' + variant, size !== 'md' && 'ap-btn-' + size, props.wide && 'ap-btn-wide', props.className);
    return h('button', p, props.icon ? h(Icon, { name: props.icon }) : null, props.children);
  }

  function StatusChip(props) {
    var s = props.status || 'none';
    return h('span', { className: cx('ap-chip', 'ap-chip-' + s, props.className) }, props.children || STATUS_LABEL[s]);
  }

  function Tag(props) {
    return h('span', { className: cx('ap-tag', props.tone === 'neutral' && 'ap-tag-neutral', props.className) }, props.children);
  }

  function SummaryTile(props) {
    var s = props.status;
    return h('div', { className: cx('ap-tile', s && 'ap-tile-' + s, props.className) },
      h('span', null, props.label),
      h('strong', null, props.value),
      props.hint ? h('small', null, props.hint) : null);
  }

  var MARK_LABEL = { focus: 'Осмотрено · нашёл очаг', clean: 'Осмотрено · чисто', none: 'Осмотра ещё не было' };

  function FieldRow(props) {
    var s = props.status || 'none';
    var reasons = props.reasons || [];
    return h('article', { className: cx('ap-field', 'ap-field-' + s, props.className) },
      h('div', { className: 'ap-field-head' },
        h('div', { className: 'ap-field-id' }, h('strong', null, props.id), h(StatusChip, { status: s })),
        h('div', { className: 'ap-field-meta' }, h('b', null, props.crop), props.detail ? h('span', null, props.detail) : null),
        props.due ? h('div', { className: 'ap-due' }, h('span', null, props.dueLabel || 'Осмотреть до'), h('strong', null, props.due)) : null),
      reasons.length ? h('ul', { className: 'ap-reasons' }, reasons.map(function (r, i) { return h('li', { key: i }, r); })) : null,
      h('div', { className: 'ap-field-foot' },
        props.source ? h('span', { className: cx('ap-source', props.sensor && 'ap-source-sensor') }, props.source) : null,
        props.mark ? h('span', { className: cx('ap-mark', 'ap-mark-' + props.mark) }, MARK_LABEL[props.mark]) : null,
        props.actions ? h('div', { className: 'ap-field-actions' }, props.actions) : null),
      props.children || null);
  }

  function IndexStat(props) {
    var t = props.trend;
    return h('div', { className: cx('ap-index', props.className) },
      h('span', null, props.label),
      h('strong', null, props.value),
      props.change ? h('small', { className: t === 'down' ? 'ap-down' : t === 'up' ? 'ap-up' : null }, (t === 'down' ? '↓ ' : t === 'up' ? '↑ ' : '') + props.change) : null);
  }

  function Segmented(props) {
    var opts = props.options || [];
    var internal = useState(props.defaultValue != null ? props.defaultValue : (opts[0] && opts[0].value));
    var value = props.value != null ? props.value : internal[0];
    return h('div', { className: cx('ap-seg', props.wide && 'ap-seg-wide', props.className), role: 'group', 'aria-label': props.label },
      opts.map(function (o) {
        return h('button', {
          key: o.value, type: 'button', 'aria-pressed': String(o.value === value),
          onClick: function () { internal[1](o.value); if (props.onChange) props.onChange(o.value); }
        }, o.label);
      }));
  }

  var fieldSeq = 0;
  function TextField(props) {
    var idRef = React.useRef(null);
    if (!idRef.current) idRef.current = props.id || 'ap-field-' + (++fieldSeq);
    var id = idRef.current;
    var control;
    var common = { id: id, name: props.name, placeholder: props.placeholder, defaultValue: props.defaultValue, value: props.value, onChange: props.onChange, required: props.required, 'aria-invalid': props.error ? 'true' : undefined };
    if (props.options) {
      control = h('select', common, props.options.map(function (o) { return h('option', { key: o.value, value: o.value }, o.label); }));
    } else if (props.multiline) {
      control = h('textarea', common);
    } else {
      common.type = props.type || 'text';
      control = h('input', common);
    }
    return h('label', { className: cx('ap-input', props.error && 'ap-input-error', props.className), htmlFor: id, style: props.width ? { width: props.width } : undefined },
      props.label, control, props.error || props.hint ? h('small', null, props.error || props.hint) : null);
  }

  function InspectionMark(props) {
    var internal = useState(props.defaultValue || null);
    var value = props.value !== undefined ? props.value : internal[0];
    function pick(v) { internal[1](v); if (props.onChange) props.onChange(v); }
    return h('div', { className: cx('ap-inspect', props.className), role: 'group', 'aria-label': props.label || 'Итог осмотра' },
      h('button', { type: 'button', className: 'ap-clean', 'aria-pressed': String(value === 'clean'), onClick: function () { pick('clean'); } },
        h('i', null, h(Icon, { name: 'check' })), 'Чисто', h('span', null, props.cleanHint || 'Признаков болезни нет')),
      h('button', { type: 'button', className: 'ap-focus', 'aria-pressed': String(value === 'focus'), onClick: function () { pick('focus'); } },
        h('i', null, h(Icon, { name: 'spot' })), 'Нашёл очаг', h('span', null, props.focusHint || 'Поле поднимется в «Проверить»')));
  }

  function Notice(props) {
    var tone = props.tone || 'success';
    var icon = tone === 'error' ? 'alert' : tone === 'example' ? 'info' : tone === 'neutral' ? 'info' : 'check';
    return h('div', { className: cx('ap-notice', tone !== 'success' && 'ap-notice-' + tone, props.className), role: tone === 'error' ? 'alert' : 'status' },
      h(Icon, { name: icon }),
      h('div', null, props.title ? h('strong', null, props.title, ' ') : null, props.children));
  }

  var DEFAULT_NAV = [
    { id: 'map', label: 'Карта' }, { id: 'today', label: 'Сегодня' }, { id: 'trips', label: 'Выезды' },
    { id: 'analysis', label: 'Анализ' }, { id: 'task', label: 'Задача' }, { id: 'history', label: 'История' }
  ];
  function NavRail(props) {
    var items = props.items || DEFAULT_NAV;
    var internal = useState(props.defaultActive || items[0].id);
    var active = props.active || internal[0];
    return h('nav', { className: cx('ap-rail', props.horizontal && 'ap-rail-h', props.className), 'aria-label': 'Рабочие разделы' },
      items.map(function (it) {
        return h('button', {
          key: it.id, type: 'button', 'aria-current': it.id === active ? 'page' : undefined,
          onClick: function () { internal[1](it.id); if (props.onChange) props.onChange(it.id); }
        },
        h('span', { className: 'ap-nav-icon' }, h(Icon, { name: it.icon || it.id })),
        h('span', { className: 'ap-nav-label' }, it.label),
        it.badge ? h('b', { 'aria-label': it.badge + ' в работе' }, it.badge) : null);
      }));
  }

  function TopBar(props) {
    return h('header', { className: cx('ap-topbar', props.className) },
      h('a', { className: 'ap-brand', href: '#', 'aria-label': 'AgroPulse, начало' },
        h('span', { className: 'ap-brand-symbol' }, svg(SPROUTS, '0 0 32 32')),
        h('span', { className: 'ap-wordmark' }, 'AgroPulse', h('small', null, 'ONLYFARMS'))),
      h('div', { className: 'ap-topbar-tools' },
        props.farm ? h('div', { className: 'ap-farm' }, h('strong', null, props.farm), props.farmDetail ? h('span', null, props.farmDetail) : null) : null,
        props.children));
  }

  /* Page: the «Сегодня» screen assembled from the components above (demo data from config/farm_records_example.csv, 20 May 2024) */
  function TodayScreen() {
    var filter = useState('all');
    var mark = useState(null);
    var rows = [
      { id: 'R11', status: 'risk', crop: 'Озимая пшеница · выход в трубку', detail: 'Бурая ржавчина · защиты нет 21 день', due: '21 мая', reasons: ['Влажный лист 31 ч при 12–22 °C за пять суток', 'Фаза восприимчивая с выхода в трубку', 'Последний фунгицид 29 апреля — защита истекла'], source: 'Погода: ERA5, сектор 0,25°' },
      { id: 'R07', status: 'risk', crop: 'Озимый ячмень · колошение', detail: 'Септориоз · очаг 2023 года рядом', due: '22 мая', reasons: ['Влажный лист 26 ч при 10–20 °C', 'Прошлый очаг в учёте хозяйства'], source: 'Узел на поле · 14 суток данных', sensor: true },
      { id: 'R02', status: 'check', crop: 'Озимая пшеница · кущение', detail: 'Бурая ржавчина · условия на границе', due: '24 мая', reasons: ['Влажный лист 18 ч — порог 24 ч', 'Дождь 12 мм 18 мая смыл защиту'], source: 'Погода: ERA5, сектор 0,25°', mark: 'clean' },
      { id: 'R01', status: 'normal', crop: 'Озимая пшеница · выход в трубку', detail: 'Защита действует до 27 мая', reasons: ['Фунгицид 13 мая, 14 дней защиты'], source: 'Погода: ERA5, сектор 0,25°' }
    ];
    var shown = rows.filter(function (r) { return filter[0] === 'all' || r.status === filter[0]; });
    return h('div', { className: 'ap-root ap-screen' },
      h(TopBar, { farm: 'Хозяйство · пример', farmDetail: '27 обводок · озимые' },
        h(Button, { variant: 'soft', className: 'ap-hide-sm' }, 'Архивный пример'),
        h(Button, { variant: 'secondary', size: 'sm' }, 'Возможности')),
      h('div', { className: 'ap-screen-body' },
        h(NavRail, { defaultActive: 'today', items: [
          { id: 'map', label: 'Карта' }, { id: 'today', label: 'Сегодня', badge: 2 }, { id: 'trips', label: 'Выезды' },
          { id: 'analysis', label: 'Анализ' }, { id: 'task', label: 'Задача' }, { id: 'history', label: 'История' }] }),
        h('main', { className: 'ap-main' },
          h('div', { className: 'ap-today-head' },
            h('div', null,
              h('div', { className: 'ap-eyebrow' }, 'Очередь осмотра'),
              h('h1', null, 'Что проверить сегодня'),
              h('p', null, 'Поля по черновым правилам риска: причина, срок осмотра и почасовое основание. Статус значит «осмотреть первым», это не диагноз.')),
            h('div', { className: 'ap-today-controls' },
              h(TextField, { label: 'Дата расчёта', type: 'date', defaultValue: '2024-05-20', width: 180 }),
              h(Button, { icon: 'arrow' }, 'Пересчитать'))),
          h(Notice, { tone: 'example', title: 'Пример данных.' }, 'Поля подписаны «ПРИМЕР» — отметки осмотра не сохраняются в журнал хозяйства.'),
          h('div', { className: 'ap-tiles', style: { marginTop: 16 } },
            h(SummaryTile, { status: 'risk', label: 'Риск', value: 2, hint: 'осмотреть за 1–2 дня' }),
            h(SummaryTile, { status: 'check', label: 'Проверить', value: 2, hint: 'при объезде' }),
            h(SummaryTile, { status: 'normal', label: 'Норма', value: 22 }),
            h(SummaryTile, { label: 'Нет правил', value: 1, hint: 'кукуруза' })),
          h('div', { className: 'ap-today-layout' },
            h('section', { className: 'ap-list', 'aria-label': 'Поля по риску' },
              h(Segmented, { label: 'Показать', value: filter[0], onChange: filter[1], options: [
                { value: 'all', label: 'Все' }, { value: 'risk', label: 'Риск' }, { value: 'check', label: 'Проверить' }, { value: 'normal', label: 'Норма' }] }),
              shown.map(function (r) {
                return h(FieldRow, Object.assign({ key: r.id }, r, {
                  actions: [
                    h(Button, { key: 'm', variant: 'secondary', size: 'sm' }, 'На карте'),
                    h(Button, { key: 'o', size: 'sm' }, 'Отметить осмотр')]
                }));
              })),
            h('aside', { className: 'ap-side' },
              h('div', { className: 'ap-card' },
                h('div', { className: 'ap-eyebrow' }, 'R11 · последний снимок'),
                h('h2', { className: 'ap-h2' }, 'Индексы поля'),
                h('div', { className: 'ap-index-grid' },
                  h(IndexStat, { label: 'NDVI', value: '0,62', change: '0,07 за 10 дней', trend: 'down' }),
                  h(IndexStat, { label: 'NDMI', value: '0,21', change: '0,02', trend: 'up' })),
                h('div', { style: { marginTop: 14 } }, h(Button, { variant: 'secondary', wide: true, icon: 'analysis' }, 'Сравнить снимки'))),
              h('div', { className: 'ap-card' },
                h('div', { className: 'ap-eyebrow' }, 'Отметить осмотр · R11'),
                h('h2', { className: 'ap-h2' }, 'Что на поле?'),
                h(InspectionMark, { value: mark[0], onChange: mark[1] }),
                h('div', { style: { marginTop: 12 } }, h(TextField, { label: 'Комментарий', placeholder: 'Где нашли, сколько растений', multiline: true })),
                h('div', { style: { marginTop: 12 } }, h(Button, { wide: true, size: 'lg', disabled: !mark[0] }, 'Сохранить отметку'))),
              h('div', { className: 'ap-card' },
                h('div', { className: 'ap-eyebrow' }, 'Почасовое основание · R11'),
                h('table', { className: 'ap-hourly' },
                  h('thead', null, h('tr', null, h('th', null, 'Сутки'), h('th', null, 'Мокрый лист'), h('th', null, 'Темп.'))),
                  h('tbody', null,
                    [['16 мая', '4 ч', '14 °C'], ['17 мая', '9 ч', '16 °C'], ['18 мая', '11 ч', '15 °C'], ['19 мая', '7 ч', '18 °C']].map(function (r) {
                      return h('tr', { key: r[0] }, h('td', null, r[0]), h('td', { className: 'ap-wet' }, r[1]), h('td', null, r[2]));
                    })))))))));
  }

  var api = { Button: Button, StatusChip: StatusChip, Tag: Tag, SummaryTile: SummaryTile, FieldRow: FieldRow, IndexStat: IndexStat, Segmented: Segmented, TextField: TextField, InspectionMark: InspectionMark, Notice: Notice, NavRail: NavRail, TopBar: TopBar, TodayScreen: TodayScreen, Icon: Icon };
  window.AgroPulse = Object.assign(window.AgroPulse || {}, api);
})();
