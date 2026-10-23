/* Real adapter. Mock code supplies rendering helpers only; its init is never called. */
if(new URLSearchParams(location.search).get('api')!=='mock') window.Moss=(()=>{
  const helpers=window.Moss, base=window.API_BASE_URL||'/api/v1', native=window.MossNative;
  document.documentElement.classList.add('real-mode');
  let db,local=null,session=null,ready=false,refreshing=null,listeners=[],records={},pendingServer=null,pendingKnown=false,readError=null;
  let owner=null,generation=0;
  const assertEpoch=epoch=>{if(epoch!==generation)throw new DOMException('账号已经切换','AbortError')};
  const inflight=new Set();
  const cfg={online:navigator.onLine,scenario:'real',fault:'none',max_input_chars:20000},streams=new Map();
  const emit=()=>listeners.forEach(f=>f());
  const scoped=key=>key==='submission'||key==='operations'||key==='draft'||key.startsWith('operation:')||key.startsWith('export-');
  const storageKey=key=>scoped(key)?(owner?'user:'+owner+':'+key:null):key;
  function rawPut(key,value){if(native)return native.call('put',{key,value});return new Promise((resolve,reject)=>{const t=db.transaction('kv','readwrite');t.objectStore('kv').put(value,key);t.oncomplete=resolve;t.onerror=()=>reject(t.error);t.onabort=()=>reject(t.error)})}
  function rawGet(key){if(native)return native.call('get',{key});return new Promise((resolve,reject)=>{const q=db.transaction('kv').objectStore('kv').get(key);q.onsuccess=()=>resolve(q.result);q.onerror=()=>reject(q.error)})}
  function transaction(key,value){const stored=storageKey(key);return stored?rawPut(stored,value):Promise.resolve()}
  function get(key){if(key==='auth')return Promise.resolve(!!session);const stored=storageKey(key);return stored?rawGet(stored):Promise.resolve(null)}
  async function quarantineLegacy(){
    if(await rawGet('legacy-account-migrated')===true)return;
    const held=await rawGet('legacy-unclaimed')||{};
    for(const key of ['submission','operations','export-id','export-intent','export-session']){
      const value=await rawGet(key);if(value!=null)held[key]=value;
    }
    if(Object.keys(held).length){await rawPut('legacy-unclaimed',held);readError='旧版暂存的账号归属尚未核实，已单独保留，不会归给新登录的账号。'}
    for(const key of ['submission','operations','export-id','export-intent','export-session'])await rawPut(key,null);
    await rawPut('legacy-account-migrated',true);
  }
  async function bindOwner(){
    if(!session?.user){const profile=await request('/account');session.user=profile;await rawPut('session',session)}
    owner=session.user.id;
    // Move legacy singleton data only to the account that owned the pre-migration session.
    if(await rawGet('legacy-account-migrated')!==true){
      for(const key of ['submission','operations','export-id','export-intent','export-session']){
        const value=await rawGet(key);if(value!=null&&await get(key)==null)await transaction(key,value);
        await rawPut(key,null);
      }
      await rawPut('legacy-account-migrated',true);
    }
    local=await get('submission');
  }
  function resetMemory(){generation++;for(const c of inflight)c.abort();inflight.clear();for(const c of streams.values())c.abort();streams.clear();records={};pendingServer=null;pendingKnown=false;readError=null;local=null;window.dispatchEvent(new Event('moss-account-cleared'))}
  async function forgetSession(){resetMemory();session=null;await rawPut('session',null);emit()}
  async function request(path,options={},retry=true){
    const epoch=generation,controller=new AbortController();inflight.add(controller);
    if(options.signal){if(options.signal.aborted)controller.abort();else options.signal.addEventListener('abort',()=>controller.abort(),{once:true})}
    const headers={'Content-Type':'application/json',...(session?{Authorization:'Bearer '+session.access_token}:{}),...options.headers};
    let response;
    const timeout=options.timeout?setTimeout(()=>controller.abort(),options.timeout):null;
    try{response=await fetch(base+path,{...options,headers,signal:controller.signal})}catch(e){if(e.name!=='AbortError')cfg.online=false;throw e}finally{if(timeout)clearTimeout(timeout);inflight.delete(controller)}
    if(epoch!==generation)throw new DOMException('账号已经切换','AbortError');
    cfg.online=true;
    if(response.status===401&&retry&&session&&path!=='/auth/refresh'){
      refreshing ||= (async()=>{try{const s=await request('/auth/refresh',{method:'POST',body:JSON.stringify({refresh_token:session.refresh_token})},false);session=s;await transaction('session',s)}catch(e){if(e.status===401){await forgetSession();window.dispatchEvent(new Event('moss-auth-required'))}throw e}finally{refreshing=null}})();
      await refreshing;return request(path,options,false);
    }
    if(!response.ok){const data=await response.json().catch(()=>null);assertEpoch(epoch);const messages={INPUT_TOO_LARGE:`每条最多 ${cfg.max_input_chars.toLocaleString()} 字，请调整后发送。`,REQUEST_TOO_LARGE:'提交内容过大，请减少内容后重试。',TITLE_TOO_LARGE:'标题最多 500 字，请缩短后保存。',USAGE_RATE_LIMITED:'操作较频繁，请稍后重试。',EXPORT_CLEANUP_PENDING:'云端临时文件清理暂未完成，会自动重试。'};const err=Error(messages[data?.error?.code]||data?.error?.message||`服务请求失败 (${response.status})`);err.detail=data?.error;err.status=response.status;throw err}
    if(options.raw)return response;
    if(response.status===204)return null;
    const result=await response.json();if(epoch!==generation)throw new DOMException('账号已经切换','AbortError');return result;
  }
  async function store(value){const epoch=generation;await transaction('submission',value);assertEpoch(epoch);local=value}
  async function apply(r){
    const old=records[r.id];if(old&&old.revision>r.revision)return;
    // Polling/SSE can return the same revision. Do not repaint an unchanged record.
    if(old?.revision===r.revision && (!('body_markdown' in r)||r.body_markdown===old.body_markdown) && local?.record_id!==r.id)return;
    if(old?.revision===r.revision&&!('body_markdown' in r)&&'body_markdown' in old)r={...r,body_markdown:old.body_markdown};
    records[r.id]=r;
    if(local?.record_id===r.id){local.last_known_revision=r.revision;local.delivery_state='accepted';await store(local);if(r.is_complete)await store(null)}
    if(r.is_complete&&pendingServer?.id===r.id)pendingServer=null;
    else if(!r.is_complete)pendingServer=r;
    emit();
  }
  async function detail(id){const r=await request('/records/'+id);await apply(r);watch(id);return r}
  function watch(id){
    if(!cfg.online||streams.has(id)||records[id]?.is_complete)return;
    const epoch=generation,controller=new AbortController();streams.set(id,controller);
    (async()=>{while(!controller.signal.aborted){try{
      const response=await request('/records/'+id+'/events',{raw:true,signal:controller.signal});const reader=response.body.getReader();const decoder=new TextDecoder();let buffer='';
      // Old WebView's AbortController polyfill cannot close an existing fetch
      // stream. Cancel the reader explicitly and reject old-account snapshots.
      const stopReader=()=>{reader.cancel().catch(()=>{})};
      controller.signal.addEventListener('abort',stopReader,{once:true});
      try{while(!controller.signal.aborted){const {done,value}=await reader.read();assertEpoch(epoch);if(done||controller.signal.aborted)break;buffer+=decoder.decode(value,{stream:true});let i;
        while((i=buffer.indexOf('\n\n'))>=0){const event=buffer.slice(0,i);buffer=buffer.slice(i+2);const data=event.split('\n').find(l=>l.startsWith('data: '));if(data){const snapshot=JSON.parse(data.slice(6));await apply(snapshot);if(snapshot.is_complete){controller.abort();break}}}
      }}finally{controller.signal.removeEventListener('abort',stopReader);stopReader()}
    }catch(e){if(epoch!==generation||controller.signal.aborted)break;if(e.status===410||e.status===404){delete records[id];emit();break}}
      if(controller.signal.aborted||!cfg.online)break;await helpers.wait(1500);try{await apply(await request('/records/'+id))}catch(e){if([404,410].includes(e.status))break}
    }if(streams.get(id)===controller)streams.delete(id)})().catch(console.error);
  }
  async function load(view='time',tags=[]){
    const epoch=generation;
    if(view==='category'&&!tags.length)return;
    const query=new URLSearchParams({view});if(tags.length)query.set('tag_ids',[...tags].sort().join(','));
    const index=await request('/records/date-index?'+query);assertEpoch(epoch);let found=[];
    for(const year of index.years)for(const month of year.months)for(const day of month.days){
      let cursor=null;do{const q=new URLSearchParams(query);q.set('record_date',day.record_date);q.set('limit','100');if(cursor)q.set('cursor',cursor);
        assertEpoch(epoch);const page=await request('/records?'+q);assertEpoch(epoch);found.push(...page.items);cursor=page.next_cursor;
      }while(cursor)
    }
    if(view==='time'){const ids=new Set(found.map(r=>r.id));for(const id of Object.keys(records))if(!ids.has(id)&&id!==local?.record_id)delete records[id]}
    for(const r of found){assertEpoch(epoch);await apply(r)}assertEpoch(epoch);emit();return index;
  }
  async function acceptSearchResults(items){const epoch=generation;for(const record of items){assertEpoch(epoch);await apply(record)}}
  async function check(){
    if(!local)return;
    const id=local.record_id;await store({...local,delivery_state:'checking'});emit();
    try{await detail(id)}catch(e){if(local?.record_id!==id)return;if(e.detail?.code==='RECORD_NOT_FOUND')await store({...local,delivery_state:'not_accepted'});else if(e.detail?.code==='RECORD_DELETED')await store(null);else await store({...local,delivery_state:'unknown'});emit();if(e.status!==404)throw e}
  }
  async function send(){
    if(!cfg.online)throw Error('请先恢复网络，再手动重新发送。');
    if(!local||!['local_only','not_accepted'].includes(local.delivery_state)||local.modifiable_rejection)return;
    await store({...local,delivery_state:'sending'});emit();
    const p={id:local.record_id,raw_input:local.raw_input,first_sent_at:local.first_sent_at,sent_utc_offset_minutes:local.sent_utc_offset_minutes,record_date:local.record_date};
    try{const result=await request('/records',{method:'POST',body:JSON.stringify(p)});await apply(result.record);watch(p.id)}catch(e){
      const d=e.detail;if(local?.record_id!==p.id)throw e;await store({...local,delivery_state:d?.record_acceptance==='not_accepted'?'not_accepted':d?.record_acceptance==='accepted'?'accepted':'unknown',modifiable_rejection:d?.record_acceptance==='not_accepted'&&d?.modifiable_rejection===true});emit();
      if(d?.code==='PENDING_RECORD_EXISTS'){pendingServer=(await request('/records/pending')).record;if(pendingServer){await apply(pendingServer);watch(pendingServer.id)}}
      throw e;
    }
  }
  async function submit(raw,{replace=false}={}){
    if(cfg.online&&!pendingKnown)throw Error('服务端未完成任务尚未核对成功，请重新读取后再发。');
    if(!raw.trim())throw Error('请输入非空文字');
    if(Array.from(raw).length>cfg.max_input_chars)throw Error(`每条最多 ${cfg.max_input_chars.toLocaleString()} 字，内容仍保留，请调整后发送。`);
    if(pending()&&!replace)throw Error('上一条尚未完成');
    if(replace&&!(local?.delivery_state==='not_accepted'&&local.modifiable_rejection))throw Error('不能修改此次提交');
    const stamp=new Date(),offset=-stamp.getTimezoneOffset(),id=helpers.uid();
    await store({record_id:id,raw_input:raw,first_sent_at:stamp.toISOString(),sent_utc_offset_minutes:offset,record_date:new Date(stamp.getTime()+offset*60000).toISOString().slice(0,10),delivery_state:'local_only',modifiable_rejection:false,last_known_revision:null});emit();
    if(cfg.online)send().catch(e=>window.dispatchEvent(new CustomEvent('moss-error',{detail:e.message})));return id;
  }
  function pending(){return local&&!records[local.record_id]?.is_complete?local:pendingServer}
  function all(){return Object.values(records).sort((a,b)=>b.record_date.localeCompare(a.record_date)||Date.parse(b.first_sent_at)-Date.parse(a.first_sent_at)||b.id.localeCompare(a.id))}
  function current(){return local?(records[local.record_id]||{...helpers.record(local.raw_input,local.first_sent_at,local.record_id),record_date:local.record_date,sent_utc_offset_minutes:local.sent_utc_offset_minutes}):pendingServer||all()[0]}
  async function handleOperation(result){
    if(result.record)await apply(result.record);
    if(result.operation.deleted_record_id){delete records[result.operation.deleted_record_id];streams.get(result.operation.deleted_record_id)?.abort();emit()}
    if(result.operation.status==='failed'){const e=Error(result.operation.error?.message||'操作失败');e.terminal=true;throw e;}
    return result;
  }
  async function settle(meta){
    const epoch=generation;
    let result;
    try{result=await request('/operations/'+meta.id)}catch(e){if(e.detail?.code!=='OPERATION_NOT_FOUND')throw e;result=await request(meta.path,{method:meta.method,body:JSON.stringify(meta.body)})}
    assertEpoch(epoch);
    if(meta.kind==='export_create'){await transaction('export-id',result.operation?.export_id||result.id);assertEpoch(epoch);await transaction('operation:'+meta.id,null);return result}
    await handleOperation(result);
    while(['queued','running'].includes(result.operation.status)){await helpers.wait(500);assertEpoch(epoch);result=await request('/operations/'+meta.id);await handleOperation(result)}
    await transaction('operation:'+meta.id,null);return result;
  }
  async function mutate(id,kind,value){
    const epoch=generation;
    const existing=(await get('operations')||[]).find(x=>x.record_id===id&&x.kind===kind&&x.value===value&&x.pending);
    assertEpoch(epoch);
    if(existing){try{const result=await settle(existing);await transaction('operations',(await get('operations')).filter(x=>x.id!==existing.id));return result}catch(e){if(e.detail||e.terminal)await transaction('operations',(await get('operations')).filter(x=>x.id!==existing.id));throw e}}
    const oid=helpers.uid(),r=records[id],body={operation_id:oid};let path='/records/'+id,method='POST';
    if(kind==='title_edit'){path+='/title';method='PATCH';body.title=value;body.expected_title_revision=r.title_revision}
    if(kind==='title_regenerate'){path+='/title/regenerate';body.expected_title_revision=r.title_revision}
    if(kind==='tag_delete'){path+='/tags/'+value;method='DELETE'}
    if(kind==='record_delete')method='DELETE';if(kind==='continue')path+='/continue';
    const meta={id:oid,record_id:id,kind,value,path,method,body,pending:true};let queue=await get('operations')||[];assertEpoch(epoch);queue.push(meta);await transaction('operations',queue);assertEpoch(epoch);
    try{const result=await settle(meta);queue=(await get('operations')).filter(x=>x.id!==oid);await transaction('operations',queue);return result}catch(e){
      if(e.detail||e.terminal){queue=(await get('operations')).filter(x=>x.id!==oid);await transaction('operations',queue)}throw e;
    }
  }
  async function restore(){
    const epoch=generation;
    await flushExportCleanup();assertEpoch(epoch);
    for(const meta of await get('operations')||[])try{await settle(meta);await transaction('operations',(await get('operations')).filter(x=>x.id!==meta.id))}catch(e){if(e.terminal||e.detail)await transaction('operations',(await get('operations')).filter(x=>x.id!==meta.id));window.dispatchEvent(new CustomEvent('moss-error',{detail:e.message}))}
    assertEpoch(epoch);const config=await request('/config');cfg.max_input_chars=config.limits.max_input_chars||20000;const dictionary=await request('/tags');assertEpoch(epoch);helpers.catalog.splice(0,helpers.catalog.length,...dictionary.items);
    const pending=await request('/records/pending');assertEpoch(epoch);pendingServer=pending.record;pendingKnown=true;await load();assertEpoch(epoch);readError=null;
    if(pendingServer){await apply(pendingServer);watch(pendingServer.id)}
    if(local&&['unknown','sending','checking','accepted'].includes(local.delivery_state))await check();
  }
  async function login(username,password){
    const next=await request('/auth/login',{method:'POST',body:JSON.stringify({username,password})},false);
    if(!owner)await quarantineLegacy();
    resetMemory();session=next;await rawPut('session',session);await bindOwner();
    window.dispatchEvent(new CustomEvent('moss-account-ready',{detail:{draft:await get('draft')||''}}));
    try{await restore()}catch(e){readError=e.message;window.dispatchEvent(new CustomEvent('moss-error',{detail:e.message}))}emit();
  }
  async function logout(all=false){
    if(local&&!['accepted'].includes(local.delivery_state))throw Error('还有未发送或接收情况未确认的内容，请先处理完。');
    await request(all?'/auth/logout-all':'/auth/logout',{method:'POST',body:JSON.stringify(all?{}:{refresh_token:session.refresh_token})});
    await forgetSession();
  }
  async function refreshProfile(){if(!session)return null;session.user=await request('/account');await rawPut('session',session);return session.user}
  async function updateNickname(nickname){session.user=await request('/account/nickname',{method:'PATCH',body:JSON.stringify({nickname})});await rawPut('session',session);emit();return session.user}
  async function changePassword(current_password,new_password){await request('/account/password',{method:'POST',body:JSON.stringify({current_password,new_password})});await forgetSession()}
  async function purgeLocalAccount(identifier){
    if(native)await native.call('purgeAccount',{user_id:identifier});
    else await new Promise((resolve,reject)=>{const t=db.transaction('kv','readwrite'),store=t.objectStore('kv'),cursor=store.openCursor();cursor.onsuccess=()=>{const item=cursor.result;if(item){if(String(item.key).startsWith('user:'+identifier+':'))item.delete();item.continue()}};t.oncomplete=resolve;t.onerror=()=>reject(t.error);t.onabort=()=>reject(t.error)});
    await rawPut('account-cleanup',null);
  }
  async function deleteAccount(current_password,confirmation){
    const result=await request('/account',{method:'DELETE',body:JSON.stringify({current_password,confirmation})});
    await rawPut('account-cleanup',owner);
    try{await purgeLocalAccount(owner)}catch{throw Error('云端账号已停用，但本机数据清理未完成；重新打开应用后会重试。')}finally{await forgetSession();owner=null}
    return result;
  }
  let exportCleanup=null,cleanupWrite=Promise.resolve();
  function updateCleanup(change){
    const epoch=generation;
    const work=cleanupWrite.catch(()=>{}).then(async()=>{assertEpoch(epoch);const queue=await get('export-cleanup')||[];assertEpoch(epoch);await transaction('export-cleanup',change(queue))});
    cleanupWrite=work;return work;
  }
  async function flushExportCleanup(){
    if(!session||!owner||!cfg.online)return false;
    if(exportCleanup?.epoch===generation){if(!await exportCleanup.work)return false;return flushExportCleanup()}
    const epoch=generation;
    const work=(async()=>{
      while(true){
        const item=(await get('export-cleanup')||[])[0];assertEpoch(epoch);if(!item)return true;
        try{
          let id=item.id;
          if(!id){const op=await request('/operations/'+item.operation_id,{timeout:5000});id=op.operation.export_id}
          if(id)await request('/exports/'+id,{method:'DELETE',timeout:5000});
        }catch(e){if(![404,410].includes(e.status))return false}
        assertEpoch(epoch);
        await updateCleanup(queue=>queue.filter(x=>x.key!==item.key));
      }
    })();
    exportCleanup={epoch,work};
    try{return await work}finally{if(exportCleanup?.work===work)exportCleanup=null}
  }
  async function releaseExport(id=null,{background=false}={}){
    const epoch=generation,intent=await get('export-intent');id ||= await get('export-id');assertEpoch(epoch);
    if(id||intent){
      const key=id||intent.operation_id;
      await updateCleanup(queue=>queue.some(x=>x.key===key)?queue:[...queue,id?{key,id}:{key,operation_id:intent.operation_id}]);assertEpoch(epoch);
      if(!id||await get('export-id')===id)await transaction('export-id',null);
      await transaction('export-intent',null);
    }
    const cleanup=flushExportCleanup();
    if(background){cleanup.catch(()=>{});return true}
    return cleanup;
  }
  async function init(){
    if(!native)db=await new Promise((resolve,reject)=>{const q=indexedDB.open('moss-real-v1',1);q.onupgradeneeded=()=>q.result.createObjectStore('kv');q.onsuccess=()=>resolve(q.result);q.onerror=()=>reject(q.error)});
    const cleanup=await rawGet('account-cleanup');if(cleanup){await rawPut('session',null);await purgeLocalAccount(cleanup)}
    session=await get('session');if(!session)await quarantineLegacy();if(session)try{await bindOwner()}catch(e){if(e.status===401){await quarantineLegacy();await forgetSession()}else readError=e.message}
    if(local&&['sending','checking'].includes(local.delivery_state))await store({...local,delivery_state:'unknown'});
    ready=true;
    if(session&&cfg.online)try{await restore()}catch(e){readError=e.message;window.dispatchEvent(new CustomEvent('moss-error',{detail:e.message}))}emit();
  }
  async function online(value){cfg.online=value;emit();if(value&&session){try{await restore();readError=null}catch(e){readError=e.message}emit()}else if(!value){for(const stream of streams.values())stream.abort();streams.clear()}}
  window.addEventListener('online',()=>online(true));window.addEventListener('offline',()=>online(false));
  return {...helpers,real:true,cfg,init,get,transaction,submit,send,check,mutate,login,logout,refreshProfile,updateNickname,changePassword,deleteAccount,releaseExport,detail,load,restore,request,all,current,pending,online,acceptSearchResults,continueRecord:id=>mutate(id,'continue'),subscribe:f=>listeners.push(f),get user(){return session?.user},get owner(){return owner},get readError(){return readError},get local(){return local},get ready(){return ready},get backend(){return {records}}};
})();
