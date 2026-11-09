'use strict';
(function(root){
  const finite=v=>typeof v==='number'&&Number.isFinite(v);
  const n=v=>finite(v)?(v>0?'+':'')+v.toFixed(3).replace('.',','):'нет данных';
  const date=v=>new Date(v).toLocaleDateString('ru-RU',{timeZone:'Europe/Moscow'});
  const utcdate=v=>new Date(v).toLocaleDateString('ru-RU',{timeZone:'UTC'});
  const age=v=>finite(v)?`${Math.floor(v)} сут.`:'неизвестно';
  function view(data){
    if(!data?.observation)return {status:'Сравнение недоступно',lines:[],question:'',forecast:'Нет проверенного прогноза'};
    const o=data.observation,w=o.window,latest=data.latest_usable_place_now;
    const lines=[['Окно 60 × 60 м',data.status==='window_evidence_available'?`ΔNDVI ${n(w.ndvi)} · ΔNDMI ${n(w.ndmi)}`:'Недостаточно общей пригодной части'],
      ['Общие ячейки окна',`${w.paired_cells}/${w.total_cells} · ${Math.round(w.coverage*100)}%`],
      ['Поздний снимок пары',`${date(o.after.at)} · ${age(o.pair_age_days)} назад`],
      ['Последний пригодный снимок места в архиве',latest?`${date(latest.at)} · ${age(latest.age_days)} назад`:'Нет пригодного наблюдения']];
    if(data.archive_end_exclusive_utc)lines.push(['Конец периода архива (UTC, не включая)',utcdate(data.archive_end_exclusive_utc)]);
    const b=o.boundary_sensitivity.ndmi;
    const boundary=b.complete?`${n(b.min)}…${n(b.max)} · ${b.direction==='decrease'?'снижение во всех сценариях':b.direction==='increase'?'повышение во всех сценариях':'нет единого ненулевого знака'}`:'Не все сценарии имеют пригодные данные';
    const f=data.forecast;let prediction='Нет сохранённого будущего выпуска для просмотра.';
    if(f?.status==='unavailable')prediction=f.reason;
    else if(f?.issued_at_utc){
      const dates=`Выпуск ${date(f.issued_at_utc)} · входная съёмка ${f.input_acquisition_date||'неизвестна'} · цели ${utcdate(f.target_window.start_utc)}–${utcdate(new Date(Date.parse(f.target_window.end_exclusive_utc)-1).toISOString())} UTC. `;
      prediction=dates+(!f.input_available?'Площадь не вошла во входной пул: это не отрицательный прогноз.':
        f.selected_candidate&&f.selected_baseline?'В коротком списке обеих схем.':f.selected_candidate?'В коротком списке кандидата.':f.selected_baseline?'В коротком списке только базы.':'Вне коротких списков при фиксированном бюджете; это не отсутствие будущего снижения.');
      prediction+=' '+(!f.input_available?'Прогноз для этой площади не выпускался.':f.status==='finalised'?(typeof f.outcome?.outcome!=='boolean'?'Целевой исход неизвестен.':f.outcome.outcome===true?'Зарегистрировано спутниковое снижение.':'Порогового спутникового снижения не зарегистрировано.'):
        f.status==='awaiting_evaluation_data'?'Ожидается проверка полноты целевых данных.':'Исход ещё не оценён.');
    }
    return {status:data.status==='window_evidence_available'?'Есть наблюдаемое основание для вопроса':'Изменение площади не подтверждает изменение этого окна',lines,boundary,
      question:data.question,forecast:prediction};
  }
  function render(parent,data,{confirmed=false,onQuestion}={}){
    const v=view(data);parent.replaceChildren();
    const el=(tag,text,container=parent,cls)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;container.appendChild(node);return node;};
    el('h4','Перед осмотром');el('p',v.status,parent,'review-status');
    const dl=el('dl');for(const[label,value]of v.lines){const row=el('div',undefined,dl);el('dt',label,row);el('dd',value,row);}
    const details=el('details');el('summary','Обводка и отдельный прогноз',details);
    el('p',`NDMI по всей обводке: ${v.boundary||'нет данных'}. Это чувствительность к границам, не интервал точности окна.`,details);
    el('strong','Проверка прогноза на будущих снимках',details);
    el('p',v.forecast,details);el('p','Прогноз относится к ядру обводки, а не окну 60 м. Полезность осмотра и причина изменения не установлены.',details,'review-footnote');
    el('p',v.question,parent,'review-question');
    if(data.as_of_utc)el('p','Карточка рассчитана '+new Date(data.as_of_utc).toLocaleString('ru-RU',{timeZone:'Europe/Moscow',dateStyle:'short',timeStyle:'short'})+' МСК.',parent,'review-footnote');
    const button=el('button','Добавить вопрос в задачу',parent,'secondary');button.type='button';button.disabled=!confirmed||!v.question;
    button.addEventListener('click',()=>onQuestion?.(v.question));
    if(!confirmed)el('p','Сначала выберите место или подтвердите опорную точку.',parent,'review-footnote');
  }
  const api={view,render};if(typeof module==='object'&&module.exports)module.exports=api;else root.ScoutingReviewCard=api;
})(typeof window==='undefined'?globalThis:window);
