/* Tab-local form snapshots only. Never a server journal or observation store. */
(function(root,factory){
  'use strict';
  const api=factory(root);
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.ScoutingDrafts=api;
})(globalThis,function(root){
  'use strict';
  const PREFIX='agropulse:draft:v1:';
  const MAX_CHARS=32768;

  function key(scope,parts){
    if(typeof scope!=='string'||!scope.trim())throw new TypeError('Draft scope must be a non-empty string');
    const ids=typeof parts==='string'?[parts]:parts;
    if(!Array.isArray(ids)||!ids.length||ids.some(id=>typeof id!=='string'||!id.length))throw new TypeError('Draft parts must be canonical ID strings');
    // JSON framing distinguishes ["a|b","c"] from ["a","b|c"].
    return PREFIX+JSON.stringify([scope,ids]);
  }

  function ownKey(value){return typeof value==='string'&&value.startsWith(PREFIX)&&value.length>PREFIX.length;}
  function forbidden(name){
    const normalized=name.toLowerCase().replace(/[-_]/g,'');
    return ['visit','visits','photo','photos','authorization'].includes(normalized)||normalized.endsWith('token');
  }
  function safeJSON(value){
    return JSON.stringify(value,(name,v)=>{
      if(forbidden(name)||['undefined','function','symbol','bigint'].includes(typeof v)||typeof v==='number'&&!Number.isFinite(v))throw new TypeError('Unsupported draft payload');
      return v;
    });
  }

  function create(storage){
    if(arguments.length===0){try{storage=root.sessionStorage;}catch{storage=null;}}
    const usable=()=>{
      try{return !!storage&&typeof storage.getItem==='function'&&typeof storage.setItem==='function'&&typeof storage.removeItem==='function';}
      catch{return false;}
    };
    return Object.freeze({
      load(id){
        if(!ownKey(id)||!usable())return null;
        try{
          const raw=storage.getItem(id);
          if(typeof raw!=='string'||raw.length>MAX_CHARS)return null;
          const entry=JSON.parse(raw);
          if(!entry||Array.isArray(entry)||entry.schema!==1||!Object.prototype.hasOwnProperty.call(entry,'value')||Object.keys(entry).length!==2)return null;
          // Check tampered/old payloads as well as newly saved snapshots.
          safeJSON(entry.value);
          return entry.value;
        }catch{return null;}
      },
      save(id,value){
        if(!ownKey(id)||!usable())return false;
        try{
          const raw=safeJSON({schema:1,value});
          if(raw.length>MAX_CHARS)return false;
          storage.setItem(id,raw);
          return true;
        }catch{return false;}
      },
      remove(id){
        if(!ownKey(id)||!usable())return false;
        try{storage.removeItem(id);return true;}catch{return false;}
      }
    });
  }

  const defaults=create();
  return Object.freeze({key,create,load:defaults.load,save:defaults.save,remove:defaults.remove});
});
