/* Shared Web/Android search surface. Queries/results are in memory only. */
window.MossSearch=(()=>{
  const state={draft:'',query:'',items:null,status:null,error:'',busy:false,token:0,context:null,statusBusy:false};
  const escape=value=>window.Moss.escape(String(value??''));
  const errorText=error=>({
    SEARCH_DISABLED:'智能搜索尚未启用。',SEARCH_UNAVAILABLE:'智能搜索暂时不可用，记录保存不受影响，请稍后重试。',
    SEARCH_QUERY_TOO_LONG:'搜索内容过长，请缩短问题后重试。',AUTH_REQUIRED:'登录已失效，请重新登录。',
  }[error.detail?.code]||'搜索未完成，请检查网络后重试。');
  function reset(){state.token++;state.items=null;state.query='';state.error='';state.busy=false;}
  let statusTimer;
  async function refreshStatus(){
    if(state.statusBusy||!window.Moss.real)return;
    state.statusBusy=true;const owner=Moss.owner;
    try{const status=await Moss.request('/search/status');if(owner===Moss.owner)state.status=status}catch{}finally{
      if(owner!==Moss.owner)return;
      state.statusBusy=false;state.context?.changed();clearTimeout(statusTimer);
      if(document.querySelector('#semantic-search-form')&&state.status?.enabled&&(state.status.pending||state.status.state!=='ready'))statusTimer=setTimeout(refreshStatus,2000);
    }
  }
  function panel(context){
    const key=JSON.stringify([context.mode,[...context.filters].sort()]);
    if(state.context?.key!==key)reset();
    if(state.status?.scope==='all')context={...context,mode:'time',filters:[]};
    state.context={...context,key};
    if(!state.status&&!state.statusBusy)queueMicrotask(refreshStatus);
    const status=state.status;
    let hint='按内容含义查找已完成记录。';
    if(context.mode==='category')hint=context.filters.length?'在当前所选分类中，按内容含义查找。':'先选择分类，或切换“按时间”搜索全部记录。';
    if(status?.enabled===false)hint='智能搜索尚未启用，现有记录浏览可正常使用。';
    else if(status?.state==='error')hint=status.failed?`有 ${status.pending} 条记录暂未完成搜索准备，当前结果可能不完整。`:'智能搜索准备失败，系统将自动重试；记录保存不受影响。';
    else if(status?.pending)hint=`${status.indexed} / ${status.total} 条记录已可搜索，其余正在准备。`;
    else if(['starting','loading_model'].includes(status?.state))hint='智能搜索正在准备，记录保存不受影响。';
    return `<section class="semantic-search" aria-label="智能搜索"><form id="semantic-search-form"><label for="semantic-query">找回相关记录</label><div class="search-controls"><input id="semantic-query" type="search" maxlength="1000" placeholder="例如：我之前为什么想换工作？" value="${escape(state.draft)}" autocomplete="off"><button class="btn primary" type="submit" ${state.busy||!Moss.cfg.online||status?.enabled===false||(context.mode==='category'&&!context.filters.length)?'disabled':''}>${state.busy?'查找中…':'搜索'}</button></div></form><p class="search-hint" role="status">${escape(hint)}</p>${state.error?`<p class="status-note" role="alert">${escape(state.error)}</p>`:''}${state.items!==null||state.busy?'<button class="clear" data-search-action="clear">返回时间 / 分类浏览</button>':''}</section>`;
  }
  function results(card){
    if(state.items===null)return null;
    const rows=state.items.filter(item=>Moss.all().some(record=>record.id===item.record.id));
    return `<section class="search-results" aria-label="相关记录"><p class="section-label">${escape(state.query)} · ${rows.length} 条候选</p>${rows.length?rows.map(item=>{
      const record=Moss.all().find(r=>r.id===item.record.id)||item.record;
      return `<div data-motion-key="search-${record.id}"><p class="search-match">${escape(item.snippet.slice(0,220))}${item.snippet.length>220?'…':''}</p>${card(record)}</div>`;
    }).join(''):'<div class="empty">暂无可显示的相关记录。<br>可以换一种说法，或稍后重试。</div>'}</section>`;
  }
  async function submit(){
    const query=state.draft.trim();if(!query||state.busy)return;
    const token=++state.token,context=state.context;
    state.busy=true;state.error='';context.changed();
    try{
      const result=await Moss.request('/search',{method:'POST',body:JSON.stringify({query,tag_ids:context.mode==='category'?context.filters:[],limit:5})});
      if(token!==state.token)return;
      await Moss.acceptSearchResults(result.items.map(item=>item.record));
      if(token!==state.token)return;
      state.items=result.items;state.query=query;state.status=result.status;
    }catch(error){if(token===state.token)state.error=errorText(error)}
    finally{if(token===state.token){state.busy=false;state.context?.changed()}}
  }
  document.addEventListener('input',event=>{if(event.target.id==='semantic-query')state.draft=event.target.value});
  document.addEventListener('submit',event=>{if(event.target.id==='semantic-search-form'){event.preventDefault();submit()}});
  function back(){if(state.items===null&&!state.busy)return false;reset();state.context?.changed();refreshStatus();return true}
  document.addEventListener('click',event=>{if(event.target.closest('[data-search-action="clear"]'))back()});
  window.addEventListener('moss-auth-required',()=>{reset();state.status=null;state.draft=''});
  function clearAccount(){reset();clearTimeout(statusTimer);state.draft='';state.status=null;state.context=null;state.statusBusy=false}
  return {panel,results,reset,refreshStatus,clearAccount,back};
})();
