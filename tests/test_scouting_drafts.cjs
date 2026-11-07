'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const drafts=require('../web/scouting/drafts.js');

function memoryStorage(){
  const entries=new Map();
  return {
    getItem(id){return entries.has(id)?entries.get(id):null;},
    setItem(id,value){entries.set(id,String(value));},
    removeItem(id){entries.delete(id);},
    entries
  };
}

test('canonical IDs produce stable framed keys and separate scopes',()=>{
  const parts=['R11','recent2026','scene-before','scene-after','version-sha','R11-123-456','ndmi'];
  assert.equal(drafts.key('task',parts),drafts.key('task',[...parts]));
  assert.equal(drafts.key('plan','single-id'),drafts.key('plan',['single-id']));
  assert.notEqual(drafts.key('task',parts),drafts.key('plan',parts));
  assert.notEqual(drafts.key('task',['a|b','c']),drafts.key('task',['a','b|c']));
  assert.notEqual(drafts.key('task',['R11','ndvi']),drafts.key('task',['R11','ndmi']));
  assert.notEqual(drafts.key('task',['R11','v1']),drafts.key('task',['R11','v2']));
  assert.notEqual(drafts.key('task',['a','b']),drafts.key('task',['b','a']));
});

test('key rejects non-canonical or empty inputs',()=>{
  for(const [scope,parts]of [[null,['R11']],['', ['R11']],['  ',['R11']],['task',[]],['task',[undefined]],['task',{id:'R11'}],['task',['R11',1]],['task','']]){
    assert.throws(()=>drafts.key(scope,parts),TypeError);
  }
});

test('form snapshot roundtrips as a detached JSON value',()=>{
  const storage=memoryStorage(),store=drafts.create(storage),id=drafts.key('task',['R11','cell-id']);
  const value={actor:'ТЕСТ — не агроном',date:'',objective:'Проверить вопрос',selected:['task-1'],optional:null};
  assert.equal(store.load(id),null);
  assert.equal(store.save(id,value),true);
  value.objective='unsaved edit';
  const loaded=store.load(id);
  assert.equal(loaded.objective,'Проверить вопрос');
  loaded.selected.push('not saved');
  assert.deepEqual(store.load(id).selected,['task-1']);
  assert.equal(storage.entries.size,1);
});

test('remove removes only its own key',()=>{
  const storage=memoryStorage(),store=drafts.create(storage),a=drafts.key('task',['R11']),b=drafts.key('plan',['R11']);
  store.save(a,{objective:'A'});store.save(b,{title:'B'});storage.setItem('unrelated','keep');
  assert.equal(store.remove(a),true);assert.equal(store.load(a),null);
  assert.deepEqual(store.load(b),{title:'B'});
  assert.equal(store.remove('unrelated'),false);assert.equal(storage.getItem('unrelated'),'keep');
});

test('missing, blocked, partial and quota-limited storage never throw',()=>{
  const id=drafts.key('task',['R11']);
  for(const storage of [null,undefined,{}, {getItem(){return null;}},
    {getItem(){throw Error('denied');},setItem(){throw Error('quota');},removeItem(){throw Error('denied');}}]){
    const store=drafts.create(storage);
    assert.equal(store.load(id),null);assert.equal(store.save(id,{objective:'test'}),false);assert.equal(store.remove(id),false);
  }
  const storage=memoryStorage();storage.setItem=()=>{throw Error('quota');};
  const store=drafts.create(storage);
  assert.equal(store.save(id,{objective:'test'}),false);assert.equal(store.load(id),null);
});

test('corrupt and incompatible JSON falls back to null',()=>{
  const storage=memoryStorage(),store=drafts.create(storage),id=drafts.key('task',['R11']);
  for(const raw of ['{bad', 'null', '[]', '{}','{"schema":2,"value":{}}','{"schema":1}',
    '{"schema":1,"value":{},"unexpected":true}','{"schema":1,"value":{"photos":[]}}']){
    storage.setItem(id,raw);assert.equal(store.load(id),null,raw);
  }
});

test('credentials, visit/photo records and lossy payloads are not persisted',()=>{
  const storage=memoryStorage(),store=drafts.create(storage),id=drafts.key('task',['R11']);
  const circular={};circular.self=circular;
  for(const value of [{token:'secret'},{nested:{csrfToken:'secret'}},{visits:[]},{photos:[]},
    {nested:[{photo:'base64'}]},{authorization:'secret'},undefined,{x:undefined},{x:NaN},{x:Infinity},{x:()=>1},circular]){
    assert.equal(store.save(id,value),false);
    assert.equal(storage.getItem(id),null);
  }
  assert.equal(store.save(id,{objective:'Слово photos в вопросе не является фотографией'}),true);
});

test('unrelated or invalid keys do not read or write storage',()=>{
  const storage=memoryStorage(),store=drafts.create(storage);
  for(const id of [null,undefined,'','task','another-app:key',12]){
    assert.equal(store.load(id),null);assert.equal(store.save(id,{title:'A'}),false);assert.equal(store.remove(id),false);
  }
  assert.equal(storage.entries.size,0);
});
