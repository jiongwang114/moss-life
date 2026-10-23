const M=Moss,E=M.escape,$=s=>document.querySelector(s);
const icons={write:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 5l5 5M5 19l4-1L20 7a2.8 2.8 0 0 0-4-4L5 14z"/></svg>',records:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="4" y="4" width="6" height="6" rx="2"/><rect x="14" y="4" width="6" height="6" rx="2"/><rect x="4" y="14" width="6" height="6" rx="2"/><rect x="14" y="14" width="6" height="6" rx="2"/></svg>'};
const state={page:'write',mode:'time',draft:'',expanded:null,edit:null,tagEdit:null,editDrafts:{},revise:false,reviseDraft:'',filters:[],dimension:'scene',tree:{},topicOpen:{},scroll:{write:0,records:0},modal:null,toast:'',auth:false,authBusy:false,authError:'',username:'',read:'loading',cache:[],busy:new Set(),static:false,large:false,export:null,loginReturn:'write',hiddenExport:false};
const names={blocked:'等待正文',queued:'等待执行',running:'正在生成',retry_wait:'等待自动重试',save_pending:'正在保存',awaiting_manual:'待手动继续',succeeded:'已保存'};
const deliveryNames={local_only:'仅保存在手机，尚未发送',sending:'正在发送',checking:'正在核对接收情况',unknown:'接收情况未确认',not_accepted:'已确认未接收',accepted:M.real?'后台已接收':'后台已接收（模拟）'};
const scenarioNames={normal:['顺畅生长','正文先保存 · 标题标签并行'],offline:['离线收纳','仅存手机 · 联网后手动重发'],manual:['慢一点，也没关系','标题慢 · 标签三次失败待继续'],rejection:['明确拒收','独立修改 · 保留下一段草稿'],unknown:['接收未明','原编号核对 · 不重复建立'],garden:['翻阅花园','跨年时间树 · 多维条件交集']};
const time=r=>new Date(Date.parse(r.first_sent_at)+r.sent_utc_offset_minutes*60000).toISOString().slice(11,16);
function toast(s){state.toast=s;render();clearTimeout(toast.timer);toast.timer=setTimeout(()=>{state.toast='';render()},4500)}
function sync(){if(M.real&&M.readError)state.read='error';if(M.cfg.online){state.cache=M.copy(M.all());if(!M.readError)state.read=state.cache.length?'ready':'empty'}let r=M.current();if(r&&M.local&&!state.cache.some(x=>x.id===r.id))state.cache.unshift(M.copy(r));else if(M.local&&r){let i=state.cache.findIndex(x=>x.id===r.id);if(i>=0)state.cache[i]=M.copy(r)}render()}
function visibleRecords(){const p=M.pending();let rows=[...state.cache];if(p){const id=p.record_id||p.id;if(!rows.some(r=>r.id===id))rows.unshift(p.record_id?{...M.record(p.raw_input,p.first_sent_at,id),record_date:p.record_date,sent_utc_offset_minutes:p.sent_utc_offset_minutes}:p)};return rows.sort((a,b)=>b.record_date.localeCompare(a.record_date)||Date.parse(b.first_sent_at)-Date.parse(a.first_sent_at))}
function tags(r,removable=false){
  if(r.tags===null)return '<div class="empty-tags">标签尚未保存</div>';
  if(!r.tags.length)return '<div class="empty-tags">已完成匹配 · 无适用标签</div>';
  const chips=r.tags.map(id=>{
   const t=M.tag(id),name=E(t.name),busy=state.busy.has(r.id);
   if(!removable)return `<span class="tag ${t.dimension}" data-tag-id="${E(id)}">${name}</span>`;
   const editing=state.tagEdit?.id===r.id&&state.tagEdit.tag===id;
   return `<span class="tag tag-editable ${editing?'is-tag-removing':''} ${t.dimension}" data-tag-id="${E(id)}" ${editing?'':`role="button" tabindex="0" aria-label="${name}，长按标签删除" aria-expanded="false"`} title="长按标签删除"><span class="tag-name">${name}</span>${editing?`<button type="button" class="tag-remove" data-action="tag-delete" data-id="${r.id}" data-tag="${E(id)}" aria-label="删除标签 ${name}" title="删除标签 ${name}" ${busy?'disabled':''}><span aria-hidden="true">×</span></button>`:''}</span>`;
  }).join('');
  return `<div class="tags ${removable?'tags-editable':''}">${chips}</div>${removable?'<p class="tag-edit-hint" role="note">长按标签删除</p>':''}`;
}
function steps(r){return `<div class="steps">${Object.entries(r.steps).map(([k,v],i)=>`<div class="step ${v.status}" title="${E(v.status)}"><i>${v.status==='succeeded'?'✓':v.status==='awaiting_manual'?'!':String(i+1).padStart(2,'0')}</i><b>${{body:'正文清理',title:'标题生成',tags:'标签匹配'}[k]}</b><small>${names[v.status]}</small>${v.automatic_attempts?`<small>自动 ${v.automatic_attempts}/3${v.manual_attempts?` · 手动 ${v.manual_attempts}`:''}</small>`:''}</div>`).join('')}</div>`}
function recovery(r){const l=M.local?.record_id===r.id?M.local:null;let html='';if(l&&l.delivery_state!=='accepted'){html+=`<div class="status-note">${deliveryNames[l.delivery_state]}${l.modifiable_rejection?' · 未建立任务，请在独立修改框中调整后再发。':''}</div>`;if(!r.body_available)html+=`<p class="preview">${E(l.raw_input)}</p>`;html+='<div class="actions">';if(['local_only','not_accepted'].includes(l.delivery_state)&&!l.modifiable_rejection)html+=`<button class="btn primary" data-action="resend" ${!M.cfg.online?'disabled':''}>重新发送</button>`;if(l.delivery_state==='unknown')html+='<button class="btn primary" data-action="check">核对接收情况</button>';if(l.modifiable_rejection)html+='<button class="btn primary" data-action="revise">修改后再发</button>';html+='</div>';if(state.revise&&l.modifiable_rejection)html+=`<div class="editor"><label class="read-time">修改这次拒收内容 · 不影响上方草稿</label><textarea id="revise" aria-label="拒收记录修改框">${E(state.reviseDraft)}</textarea><div class="actions"><button class="btn primary" data-action="revise-send" ${state.busy.has('submit')?'disabled':''}>作为新提交发送</button><button class="btn" data-action="revise-cancel">取消</button></div></div>`}else if(!r.is_complete){html+=steps(r);if(Object.values(r.steps).some(s=>s.status==='awaiting_manual'))html+=`<div class="status-note">${M.real?'处理暂未完成':'模拟服务暂时不可用'}。已保存结果保留；仅继续待手动项。</div><div class="actions"><button class="btn primary" data-action="continue" data-id="${r.id}">继续处理</button></div>`}if(r.is_complete&&M.local?.record_id===r.id)html+='<div class="status-note">记录已完整保存；手机原文暂存清理失败。不会重复提交。<button class="btn" data-action="cleanup">补清暂存</button></div>';return html}
function card(r,recent=false){
 const expanded=state.expanded===r.id, canEdit=r.is_complete;
 const titleBusy=state.busy.has('title:'+r.id)||['queued','running'].includes(r.last_title_operation?.status);
 const details=expanded?`<p class="read-time">首次发送 · ${r.record_date} ${time(r)} · UTC+08:00</p>
  <div class="collapse-region title-editor ${state.edit===r.id?'is-open':''}" data-motion-key="title-editor" aria-hidden="${state.edit!==r.id}" ${state.edit===r.id?'':'inert'}><div class="collapse-inner">${state.edit===r.id?`<div class="editor"><input id="title-edit" aria-label="编辑标题" value="${E(state.editDrafts[r.id]??r.title)}"><div class="actions"><button class="btn primary" data-action="title-save" data-id="${r.id}" ${titleBusy?'disabled':''}>${titleBusy?'保存中…':'保存标题'}</button><button class="btn" data-action="title-cancel" data-id="${r.id}">取消</button></div></div>`:''}</div></div>
  ${r.body_available?`<div class="markdown">${readableMarkdown(r)}</div>`:'<div class="empty">正文尚未保存；原提交内容仍在手机暂存。</div>'}
  ${canEdit?`<div class="actions"><button class="btn ghost" data-action="title-edit" data-id="${r.id}" ${titleBusy?'disabled':''}>改标题</button><button class="btn ghost title-regenerate ${titleBusy?'is-busy':''}" data-action="title-regen" data-id="${r.id}" aria-label="${titleBusy?'正在生成标题':'重新生成标题'}" aria-busy="${titleBusy}" ${titleBusy?'disabled':''}><span class="button-label" aria-hidden="true">重新生成标题</span><span class="button-progress" aria-hidden="true">正在生成…</span></button><button class="btn danger" data-action="delete" data-id="${r.id}" ${state.busy.has(r.id)?'disabled':''}>删除记录</button></div>`:''}
  ${r.last_title_operation?.status==='failed'?'<p class="status-note">标题操作失败，旧标题仍保留；可重新操作。</p>':''}`:'';
 return `<article class="record ${expanded?'expanded':''}" data-record="${r.id}">
  <div class="record-top"><span class="${r.is_complete?'saved-dot':''}">${r.is_complete?'已收纳':M.local?.record_id===r.id?deliveryNames[M.local.delivery_state]:'处理中'}</span><span>${time(r)} <span class="mini-orbit">✳</span></span></div>
  <button class="record-title" data-action="expand" data-id="${r.id}" aria-expanded="${expanded}">${E(r.title||'标题尚未保存')}</button>
  <div class="record-preview collapse-region ${expanded?'':'is-open'}" aria-hidden="${expanded}"><div class="collapse-inner"><p class="preview">${E(r.body_preview||'正文尚未保存，完成后即可阅读。')}</p></div></div>
  ${tags(r,expanded&&canEdit)}${recovery(r)}
  <div class="record-expansion collapse-region ${expanded?'is-open':''}" aria-hidden="${!expanded}" ${expanded?'':'inert'}><div class="expanded-content collapse-inner">${details}</div></div>
  <button class="open-line" data-action="expand" data-id="${r.id}">${expanded?'收起全文':recent?'回看这一刻':'原地阅读全文'}<span>${expanded?'↑':'↗'}</span></button>
 </article>`;
}
const markdownCache=new Map();
function readableMarkdown(record){
 const cached=markdownCache.get(record.id);
 if(cached?.body===record.body_markdown)return cached.html;
 const html=M.markdown(record.body_markdown);markdownCache.set(record.id,{body:record.body_markdown,html});return html;
}

function writePage(){let r=M.real?M.current():(M.local?M.current():state.cache[0]);return `<section class="hero"><div class="eyebrow">A LITTLE SPACE FOR YOURSELF</div><h2>此刻，<br><span>有什么想留下？</span></h2><div class="little-leaf"></div><p>一件小事、一缕情绪，或一个还没成形的念头。</p></section><section class="composer"><textarea id="draft" aria-label="写下记录" placeholder="让想法落在这里…\n不用整理，也不用想好标题。">${E(state.draft)}</textarea><div class="compose-foot"><div class="shortcut"><span>纯文字 / Markdown</span><span id="count">${Array.from(state.draft).length} 字</span></div><button class="send" data-action="send" aria-label="发送记录" ${!M.ready||!state.draft.trim()||M.pending()||state.busy.has('submit')?'disabled':''}>↗</button></div></section><p class="hint">${M.pending()?'上一条尚未完整完成。你可以先写下一个念头。':'写下，然后交给时间。Enter 换行，点击箭头发送。'}</p><div class="section-label">${M.pending()?'正在收纳':'最近留下的'}<span>${M.pending()?'GROWING':'LATEST MOMENT'}</span></div>${r?card(r,true):'<div class="empty"><span class="seed">✳</span>你的花园，从一个念头开始。<br>尚无最近记录。离线时不加载历史副本。</div>'}<div class="mock-badge">${M.real?(window.MossNative?'':'真实后端 · 网页联调端（非 Android）'):'本地交互原型 · AI 与后台状态为模拟'}</div>`}
function tree(rows,level=0,prefix=''){
 if(!rows.length)return '';
 const groups=Object.groupBy(rows,r=>r.record_date.split('-')[level]);
 return Object.keys(groups).sort().reverse().map((part,i)=>{
  const key=prefix+part,open=state.tree[key]??(i===0),suffix=['年','月','日'][level];
  return `<section class="tree-${['year','month','day'][level]}" data-tree="${key}"><button class="tree-button ${open?'':'closed'}" data-action="tree" data-key="${key}" data-open="${open}" aria-expanded="${open}"><span class="chev">▼</span>${part}${suffix}<em>${groups[part].length} 条</em></button><div class="tree-content collapse-region ${open?'is-open':''}" aria-hidden="${!open}" ${open?'':'inert'}><div class="collapse-inner">${open?(level===2?groups[part].map(r=>card(r)).join(''):tree(groups[part],level+1,key+'-')):''}</div></div></section>`;
 }).join('');
}

function choices(){const choose=t=>`<button class="choice ${state.filters.includes(t.id)?'active':''}" data-action="filter" data-id="${t.id}" aria-pressed="${state.filters.includes(t.id)}">${E(t.name)}${state.filters.includes(t.id)?' ✓':''}</button>`;let list=M.catalog.filter(t=>t.dimension===state.dimension);return `<div class="dimension">${[['scene','个人场景'],['topic','内容主题'],['record_type','记录类型']].map(([id,n])=>`<button class="${state.dimension===id?'active':''}" data-action="dimension" data-id="${id}">${n}</button>`).join('')}</div><div class="choices">${state.dimension==='topic'?list.filter(t=>!t.parent_id).map(t=>`<details class="topic-group" data-topic="${t.id}" ${state.topicOpen[t.id]?'open':''}><summary>${E(t.name)}</summary><div class="choices">${choose(t)}${list.filter(c=>c.parent_id===t.id).map(choose).join('')}</div></details>`).join(''):list.map(choose).join('')}</div><div class="selected"><div><span>已选条件 · 全部同时满足</span><button class="clear" data-action="clear-filter">清空</button></div><div class="choices">${state.filters.length?state.filters.map(id=>`<button class="choice active" data-action="filter" data-id="${id}" aria-label="取消筛选 ${E(M.tag(id).name)}">${E(M.tag(id).name)} ×</button>`).join(''):'<span class="read-time">尚未选择分类条件</span>'}</div></div>`}
function recordsPage(){const searchPanel=M.real?MossSearch.panel({mode:state.mode,filters:[...state.filters],changed:render}):'';const searchResults=M.real?MossSearch.results(card):null;let rows=visibleRecords();if(state.mode==='category')rows=state.filters.length?rows.filter(r=>M.matches(r,state.filters)):[];return `<div class="eyebrow">YOUR GROWING COLLECTION</div><div class="browse-head"><h2>回看，自己的风景</h2></div>${M.pending()?'<button class="pending-banner" data-action="pending">✳ 一条记录尚未完整完成 · 查看进度 ↗</button>':''}<div class="seg"><button class="${state.mode==='time'?'active':''}" data-action="mode" data-id="time">按时间</button><button class="${state.mode==='category'?'active':''}" data-action="mode" data-id="category">按分类</button></div>${!M.cfg.online?'<p class="status-note">当前离线 · 仅保留本次已加载内容，不能读取新历史。</p>':''}${state.mode==='category'?choices():''}${searchPanel}${state.read==='loading'?'<div class="empty">正在读取记录…</div>':''}${state.read==='error'?'<div class="status-note">读取失败；已加载内容保留。<button class="btn" data-action="read-retry">重新读取</button></div>':''}${searchResults!==null?searchResults:rows.length?tree(rows):state.read!=='loading'&&state.read!=='error'?`<div class="empty"><span class="seed">✳</span>${state.mode==='category'?(state.filters.length?'没有同时符合这些条件的记录<br>试着取消一个条件，让视野宽一点。':'选择一个标签，重新遇见你的念头。'):'这里还没有记录。'}</div>`:''}`}
function loginPage(){if(M.real)return MossAccounts.login();return `<section class="login"><div class="eyebrow">YOUR PRIVATE GARDEN</div><h2>欢迎回来，<br>继续记录你的生活。</h2><p>使用预建测试账号进入个人花园。<br>${M.real?'连接真实账号服务。':'此处为登录模拟，不连接账号服务。'}</p><label for="username">账号</label><input id="username" autocomplete="username" value="${E(state.username)}" placeholder="demo-user"><label for="password">密码</label><input id="password" type="password" autocomplete="off" placeholder="${M.real?'请输入密码':'演示密码：moss'}"><button class="btn ghost" data-action="password">显示 / 隐藏密码</button><button class="btn primary" data-action="login" ${state.authBusy?'disabled':''}>${state.authBusy?(M.real?'登录中…':'模拟登录中…'):'进入花园'}</button>${state.authError?`<p role="alert">${E(state.authError)}</p>`:''}<p>${M.real?'请使用本人测试账号登录。':'模拟账号：demo-user / moss'}</p></section>`}
function exportPanel(){const x=state.export;if(!x)return '';const labels={preparing:'01 · 正在准备打包',downloading:'02 · 正在读取 ZIP 字节流',awaiting_location:'03 · 选择保存位置（模拟）',saving:'03 · 正在写入位置（模拟）',succeeded:'04 · 已保存（模拟结果）',failed:'导出失败',cancelled:'已取消保存'};if(M.real){labels.awaiting_location='03 · ZIP 已校验，等待保存';labels.saving='03 · 正在写入文件';labels.succeeded='04 · 已写入所选文件';labels.handed_off='已交给浏览器下载，保存结果未确认'}let detail=x.state==='preparing'?`${x.processed}/${x.total} 条已整理`:x.state==='downloading'?`${x.received_bytes.toLocaleString()} / ${x.total_bytes.toLocaleString()} 字节`:x.state==='failed'?x.error:x.state==='succeeded'?(M.real?(x.local_uri||x.file_name):`文件 / Downloads / ${x.file_name}`):x.file_name;return `<section class="export-island" aria-live="polite"><header><span>${labels[x.state]}</span><small>全量 ${x.total} 条</small></header><div class="bar"><i style="width:${x.state==='preparing'?x.processed/x.total*100:x.total_bytes?x.received_bytes/x.total_bytes*100:0}%"></i></div><small>${E(detail||'')}</small>${x.excluded.length?`<p>未包含：${x.excluded.map(r=>`${r.record_date} ${time(r)} ${E(r.title||'未完成记录')}`).join('；')}</p>`:''}<div>${x.state==='awaiting_location'?'<button data-action="location">'+(M.real?'保存 ZIP 文件':'选择位置（模拟）')+'</button>':''}${['failed','cancelled'].includes(x.state)?'<button data-action="export-retry">重试导出</button>':''}${['succeeded','failed','cancelled','handed_off'].includes(x.state)?(M.real||x.state!=='succeeded'?'':'<button data-action="download">另行下载真实 ZIP</button>')+'<button data-action="export-dismiss">收起结果</button>':''}${!['succeeded','failed','cancelled','handed_off'].includes(x.state)?'<button data-action="export-cancel">取消导出</button>':''}</div></section>`}
function modal(){if(!state.modal)return '';let m=state.modal;return `<div class="overlay" role="dialog" aria-modal="true" aria-label="${E(m.title)}"><div class="sheet"><div class="handle"></div><div class="eyebrow">${m.kind==='location'?'MOCK SYSTEM SHEET':'TAKE A MOMENT'}</div><h3>${E(m.title)}</h3><p>${E(m.message)}</p>${m.kind==='location'?`<p>文件 / Downloads<br><strong>${E(state.export?.file_name)}</strong></p><label>这是 Android 系统保存窗口的模拟。<br>模拟成功不代表真实文件已经写入。</label>`:''}<div class="actions"><button class="btn ghost" data-action="modal-cancel">取消</button><button class="btn ${m.kind==='delete'?'danger':'primary'}" data-action="modal-confirm" ${state.busy.has(m.id)?'disabled':''}>${m.kind==='delete'?'确认删除':m.kind==='scenario'?'切换模拟场景':'模拟确认保存'}</button></div></div></div>`}

// Damped harmonic oscillator: stiffness=210, damping=24, mass=1.
function springFrames(map){const w=Math.sqrt(210),z=24/(2*w),wd=w*Math.sqrt(1-z*z);return Array.from({length:49},(_,i)=>{const t=i/48*.75;let v=1-Math.exp(-z*w*t)*(Math.cos(wd*t)+z*w/wd*Math.sin(wd*t));if(i===48)v=1;return {...map(v),offset:i/48}})}
function spring(el,map){if(state.static||matchMedia('(prefers-reduced-motion: reduce)').matches)return;return el.animate(springFrames(map),{duration:750,easing:'linear'})}

function render(){
 if(state.tagEdit&&(state.expanded!==state.tagEdit.id||state.page!==state.tagEdit.page||!state.auth||!M.backend.records[state.tagEdit.id]?.tags?.includes(state.tagEdit.tag)))state.tagEdit=null;
 if(M.ready&&state.export)M.transaction('export-session',M.real?{...state.export,bytes:null}:state.export).catch(()=>{});
 const focused=document.activeElement,focusId=focused?.id,sel=focused?.selectionStart,end=focused?.selectionEnd;
 const previous=$('.scroll');
 if(previous)state.scroll[previous.dataset.page||state.page]=previous.scrollTop;
 const route=!M.ready?'loading':!state.auth?'login':state.page+':'+(state.page==='records'?[state.mode,state.mode==='category'?state.dimension:'',state.filters.join(',')].join(':'):'');
 const html=`<header class="app-head"><div class="brand">moss</div><div class="network ${M.cfg.online?'':'offline'}"><span class="dot"></span>${M.cfg.online?(M.real?'网络可用':'模拟在线'):'离线'}</div><button class="account-entry" data-action="account" aria-label="设置" title="设置" ${!state.auth?'disabled':''}><span class="settings-label">设置</span><span class="icon-btn" aria-hidden="true">⚙</span></button></header><div class="scroll" data-page="${state.page}">${!M.ready?'<div class="empty">正在读取账号与已提交暂存…</div>':!state.auth?loginPage():state.page==='settings'?MossAccounts.page():state.page==='write'?writePage():recordsPage()}</div>${state.auth?exportPanel()+`<nav class="bottom-nav" aria-label="主页面"><button class="${state.page==='write'?'active':''}" data-action="page" data-id="write" title="双击回到页面顶部">${icons.write}写记录</button><button class="${state.page==='records'?'active':''}" data-action="page" data-id="records" title="双击回到页面顶部">${icons.records}看记录</button></nav>`:''}${modal()}${state.toast?`<div class="toast" role="status">${E(state.toast)}</div>`:''}`;
 MossUI.render(html,route);
 if($('.scroll').scrollTop!==(state.scroll[state.page]||0))$('.scroll').scrollTop=state.scroll[state.page]||0;
 if(focusId&&$('#'+focusId)&&document.activeElement!==$('#'+focusId)){
  const el=$('#'+focusId);el.focus({preventScroll:true});if(typeof sel==='number')try{el.setSelectionRange(sel,end)}catch{}
 }
 renderConsole();bindDetails();
}
function bindDetails(){
 document.querySelectorAll('[data-topic]').forEach(d=>{
  if(d.mossToggleBound)return;
  d.mossToggleBound=true;d.addEventListener('toggle',()=>{state.topicOpen[d.dataset.topic]=d.open});
 });
}
function renderConsole(){if(M.real){$('#console').innerHTML='<h2>真实 API 模式</h2><p>FastAPI + SQLite</p><p>本地处理器为规则演示，非真实大模型。</p><a href="?api=mock">切换独立 Mock 模式</a>';return}let r=M.current();$('#console').innerHTML=`<h2>状态实验室 <span>MOCK CONSOLE</span></h2><p>每一种状态，都值得被温柔对待。<br>选择场景，亲手试试它的下一步。</p><div class="console-label">01 — JOURNEYS / 模拟场景</div>${Object.entries(scenarioNames).map(([id,[name,sub]],i)=>`<button class="scenario ${M.cfg.scenario===id?'active':''}" data-action="scenario" data-id="${id}"><span class="number">0${i+1}</span><span><b>${name}</b><small>${sub}</small></span></button>`).join('')}<div class="console-label">02 — FAULT INJECTION / 模拟故障</div><select id="fault" aria-label="模拟故障"><option value="none">无故障</option>${[['local_save','手机暂存写入失败'],['query','接收查询失败'],['manual','手动继续仍失败'],['mutation','标题 / 标签 / 删除失败'],['cleanup','原文暂存清理失败'],['export_prepare','打包失败'],['export_download','下载中断'],['export_save','保存空间不足'],['export_expired','文件已过期']].map(([id,n])=>`<option value="${id}" ${M.cfg.fault===id?'selected':''}>${n}</option>`).join('')}</select><div class="console-controls"><button data-action="network" class="${!M.cfg.online?'on':''}">${M.cfg.online?'切换离线':'恢复网络'}</button><button data-action="static" class="${state.static?'on':''}">静态模式</button><button data-action="large" class="${state.large?'on':''}">大字号</button><button data-action="width">320 / 360dp</button><button data-action="expire">模拟登录失效</button><button data-action="read-error">读取失败</button><button data-action="body-fail">正文失败</button><button data-action="both-fail">标题标签均失败</button><button data-action="empty">空记录视图</button></div><div class="inspector">delivery: ${M.local?.delivery_state||'—'}<br>body: ${r?.steps.body.status||'—'}<br>title: ${r?.steps.title.status||'—'}<br>tags: ${r?.steps.tags.status||'—'}<br>revision: ${r?.revision||'—'} · export: ${state.export?.state||'idle'}</div><div class="note">实际 IndexedDB 暂存 · 独立模拟服务端快照<br>非真实 Android / Room / AI / 网络服务<br>切换场景会重置模拟未完成项，保留完成记录。</div>`}
async function submit(replace=false){if(state.busy.has('submit'))return;state.busy.add('submit');const from=$('.composer')?.getBoundingClientRect();try{await M.submit(replace?state.reviseDraft:state.draft,{replace});if(replace){state.revise=false;state.reviseDraft=''}else {state.draft='';if(M.real)await M.transaction('draft','')}state.expanded=null;render();if(!state.static&&from&&!matchMedia('(prefers-reduced-motion: reduce)').matches){let target=$('.record')?.getBoundingClientRect();if(target){let el=document.createElement('div');el.className='flying';Object.assign(el.style,{left:from.left+'px',top:from.top+'px',width:from.width+'px',height:from.height+'px'});document.body.append(el);let a=el.animate([{transform:'translate(0,0) scale(1)',opacity:.5},{transform:`translate(${target.left-from.left}px,${target.top-from.top}px) scale(.84,.45)`,opacity:0}],{duration:750,easing:'cubic-bezier(.22,.8,.25,1)'});a.onfinish=()=>el.remove()}}}catch(e){toast(e.message)}finally{state.busy.delete('submit');render()}}
async function scenario(id){await M.resetScenario(id);state.revise=false;state.expanded=null;state.page=id==='garden'?'records':'write';state.read='ready';sync();if(id!=='garden'){await M.submit(id==='normal'?'傍晚走过河边，风吹得很舒服。忽然觉得，不必把每一刻都填满。':id==='rejection'?'这一段模拟原文被明确拒收。修改后使用新的编号与首次时间提交。':'今天想把一个还没想清楚的念头留下来。也许慢下来，会更容易听见自己的声音。');sync()}else{state.mode='time';state.tree={};render()}}
let exportToken=0;
async function startExport(){if(M.real)return startRealExport();if(state.export&&!['succeeded','failed','cancelled'].includes(state.export.state)){toast('已有导出正在进行。');return}if(!M.cfg.online){toast('离线不能开始全量导出。');return}let rows=M.copy(M.all().filter(r=>r.is_complete));let excluded=visibleRecords().filter(r=>!r.is_complete);if(!rows.length){toast('没有已完成的可导出内容。'+(excluded.length?`未包含：${excluded.map(r=>r.record_date+' '+time(r)+' 未完成记录').join('；')}`:''));return}const token=++exportToken;const x=state.export={export_id:M.uid(),state:'preparing',processed:0,total:rows.length,received_bytes:0,total_bytes:null,local_uri:null,error:null,file_name:`moss-records-${new Date().toISOString().replace(/[-:]/g,'').slice(0,15)}.zip`,excluded,bytes:null};render();try{if(M.cfg.fault==='export_prepare')throw Error('准备打包失败；请重试。');const bytes=await M.zip(rows,(n,total)=>{if(token!==exportToken)return;x.processed=n;render()});if(token!==exportToken)return;x.bytes=bytes;x.sha256=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(n=>n.toString(16).padStart(2,'0')).join('');x.total_bytes=bytes.length;x.state='downloading';render();let reader=new Blob([bytes]).stream().getReader();while(true){let{value,done}=await reader.read();if(token!==exportToken){reader.cancel();return}if(done)break;if(M.cfg.fault==='export_download')throw Error('ZIP 字节流读取中断；请重试下载。');x.received_bytes+=value.byteLength;render();await M.wait(300)}if(x.received_bytes!==x.total_bytes)throw Error('文件长度核对失败');x.state='awaiting_location';render();}catch(e){if(token!==exportToken)return;x.state='failed';x.error=e.message;render()}}
async function saveMock(){let x=state.export;if(!x)return;state.modal=null;x.state='saving';render();await M.wait(1000);if(x.state!=='saving')return;if(M.cfg.fault==='export_save'||M.cfg.fault==='export_expired'){x.state='failed';x.error=M.cfg.fault==='export_save'?'模拟保存失败：空间不足；未保存文件。':'模拟文件已过期：请重新建立导出。'}else{x.state='succeeded';x.local_uri='模拟：文件/Downloads/'+x.file_name}render()}
document.addEventListener('input',e=>{if(e.target.id==='draft'){state.draft=e.target.value;if(M.real)M.transaction('draft',state.draft).catch(()=>{});$('#count').textContent=Array.from(state.draft).length+' 字';$('.send').disabled=!state.draft.trim()||!!M.pending()||state.busy.has('submit')}if(e.target.id==='revise')state.reviseDraft=e.target.value;if(e.target.id==='title-edit')state.editDrafts[state.edit]=e.target.value;if(e.target.id==='username')state.username=e.target.value});
document.addEventListener('change',e=>{if(e.target.id==='fault'){M.cfg.fault=e.target.value;toast('模拟故障已设置；仅作用于下一次对应操作。')}});
let tagPressTimer=null,tagPressStart=null,suppressTagClick=null;
function cancelTagPress(){clearTimeout(tagPressTimer);tagPressTimer=null;tagPressStart=null}
function revealTagDelete(tag){
 const id=tag.closest('[data-record]')?.dataset.record,tid=tag.dataset.tagId;
 if(!id||state.expanded!==id||!M.backend.records[id]?.is_complete||state.busy.has(id))return;
 state.tagEdit={id,tag:tid,page:state.page};render();
 $('#app').querySelector(`[data-record="${CSS.escape(id)}"] [data-tag-id="${CSS.escape(tid)}"] .tag-remove`)?.focus({preventScroll:true});
}
document.addEventListener('pointerdown',e=>{
 suppressTagClick=null;
 cancelTagPress();
 const tag=e.target.closest('.tag-editable');
 if(!tag||e.target.closest('.tag-remove')||e.isPrimary===false||e.button!==0)return;
 tagPressStart={x:e.clientX,y:e.clientY,pointerId:e.pointerId,tag,activated:false};
 tagPressTimer=setTimeout(()=>{
  if(!tag.isConnected)return;
  tagPressStart.activated=true;
  suppressTagClick=tag;
  revealTagDelete(tag);
 },450);
});
document.addEventListener('pointermove',e=>{if(tagPressStart&&(e.pointerId!==tagPressStart.pointerId||Math.hypot(e.clientX-tagPressStart.x,e.clientY-tagPressStart.y)>10))cancelTagPress()},{passive:true});
document.addEventListener('pointerup',()=>{const tag=tagPressStart?.activated?tagPressStart.tag:null;cancelTagPress();if(tag)requestAnimationFrame(()=>{if(tag.isConnected)tag.querySelector('.tag-remove')?.focus({preventScroll:true})})});
document.addEventListener('pointercancel',cancelTagPress);
document.addEventListener('scroll',cancelTagPress,{capture:true,passive:true});
window.addEventListener('blur',cancelTagPress);
document.addEventListener('contextmenu',e=>{if(e.target.closest('.tag-editable')&&!e.target.closest('.tag-remove'))e.preventDefault()});
document.addEventListener('click',e=>{
 if(suppressTagClick?.contains(e.target)){suppressTagClick=null;e.preventDefault();e.stopImmediatePropagation();return}
 if(state.tagEdit&&!e.target.closest('.is-tag-removing')&&!state.busy.has(state.tagEdit.id)){state.tagEdit=null;render()}
},true);
document.addEventListener('keydown',e=>{if(['Enter',' '].includes(e.key)&&e.target.matches('.tag-editable'))e.preventDefault()});
document.addEventListener('keyup',e=>{if(['Enter',' '].includes(e.key)&&e.target.matches('.tag-editable')){e.preventDefault();revealTagDelete(e.target)}});
document.addEventListener('keydown',e=>{if(e.key==='Escape'){if(state.tagEdit){e.preventDefault();state.tagEdit=null;render()}else if(state.modal){e.preventDefault();state.modal=null;render()}}if(e.key==='Tab'&&state.modal){const els=[...document.querySelectorAll('.sheet button:not(:disabled)')];if(e.shiftKey&&document.activeElement===els[0]){els.at(-1).focus();e.preventDefault()}else if(!e.shiftKey&&document.activeElement===els.at(-1)){els[0].focus();e.preventDefault()}}});
let lastPageTap={id:null,at:0};
function navigatePage(id){
 if(!['write','records'].includes(id))return;
 const now=performance.now(),twice=lastPageTap.id===id&&now-lastPageTap.at<=350;
 lastPageTap=twice?{id:null,at:0}:{id,at:now};
 const changed=state.page!==id;
 if(changed){
  state.page=id;render();
  if(M.real&&M.cfg.online)M.restore().then(()=>{state.read='ready';sync()}).catch(e=>toast(e.message));
 }
 if(twice){
  state.scroll[id]=0;
  $('.scroll')?.scrollTo({top:0,behavior:state.static||matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});
 }
}

document.addEventListener('click',async e=>{const b=e.target.closest('[data-action]');if(!b||b.disabled)return;const a=b.dataset.action,id=b.dataset.id;try{switch(a){case 'console':document.body.classList.toggle('console-open');b.setAttribute('aria-expanded',String(document.body.classList.contains('console-open')));break;case 'send':await submit();break;case 'page':navigatePage(id);break;case 'account':MossAccounts.open();break;case 'pending':state.page='write';render();break;case 'expand':{if(M.real&&state.expanded!==id)await M.detail(id);state.expanded=state.expanded===id?null:id;state.edit=null;render();break}case 'mode':state.mode=id;render();break;case 'tree':state.tree[b.dataset.key]=b.dataset.open!=='true';render();break;case 'dimension':state.dimension=id;render();break;case 'filter':state.filters=state.filters.includes(id)?state.filters.filter(x=>x!==id):[...state.filters,id];render();break;case 'clear-filter':state.filters=[];render();break;case 'resend':await M.send();break;case 'check':await M.check();break;case 'continue':await M.continueRecord(id);break;case 'cleanup':if(M.cfg.fault==='cleanup')throw Error('模拟清理故障仍开启');await M.check();break;case 'revise':state.revise=true;state.reviseDraft ||= M.local.raw_input;render();break;case 'revise-cancel':state.revise=false;state.reviseDraft='';render();break;case 'revise-send':M.cfg.scenario='normal';await submit(true);break;case 'title-edit':state.edit=id;state.editDrafts[id]??=M.backend.records[id].title;render();$('#title-edit')?.focus();break;case 'title-cancel':state.edit=null;delete state.editDrafts[id];render();break;case 'title-save':if(!state.editDrafts[id]?.trim())throw Error('标题不能为空');await M.mutate(id,'title_edit',state.editDrafts[id]);if(state.edit===id)state.edit=null;delete state.editDrafts[id];sync();break;case 'title-regen':if(state.busy.has('title:'+id))break;state.busy.add('title:'+id);render();try{await M.mutate(id,'title_regenerate')}finally{state.busy.delete('title:'+id);render()}break;case 'tag-delete':if(state.tagEdit?.id!==id||state.tagEdit.tag!==b.dataset.tag||state.busy.has(id))break;state.busy.add(id);render();try{await M.mutate(id,'tag_delete',b.dataset.tag);state.tagEdit=null;if(state.filters.length&&!M.matches(M.backend.records[id],state.filters))toast('标签已删除；记录已移出当前分类，仍保留在时间列表中。')}finally{state.busy.delete(id);render()}break;case 'delete':state.modal={kind:'delete',id,title:'让这条记录离开？',message:`「${M.backend.records[id].title}」将被永久删除，无法恢复。`};render();break;case 'modal-cancel':await MossUI.close($('.overlay'));if(state.modal.kind==='location'){state.export.state='cancelled'}state.modal=null;render();break;case 'modal-confirm':{let m=state.modal;if(m.kind==='delete'){state.busy.add(m.id);render();try{await M.mutate(m.id,'record_delete');state.cache=state.cache.filter(r=>r.id!==m.id);state.modal=null;state.expanded=null;toast('记录已删除。')}finally{state.busy.delete(m.id);render()}}else if(m.kind==='scenario'){state.modal=null;await scenario(m.id)}else await saveMock();break}case 'scenario':case 'body-fail':case 'both-fail':{const target=a==='body-fail'?'body_fail':a==='both-fail'?'both_fail':id;if(M.pending()){state.modal={kind:'scenario',id:target,title:'切换模拟场景？',message:'仅重置这条模拟未完成记录及其暂存。已完成记录与主输入草稿保持不变。此控制不是正式产品的删除入口。'};render()}else await scenario(target);break}case 'network':await M.online(!M.cfg.online);toast(M.cfg.online?'网络已恢复；仅存手机的记录请手动重新发送。':'已切换模拟离线；本次已加载内容保留。');break;case 'static':state.static=!state.static;document.body.classList.toggle('static',state.static);render();break;case 'large':state.large=!state.large;$('.device').classList.toggle('large',state.large);render();break;case 'width':$('.device').style.width=$('.device').style.width==='336px'?'376px':'336px';break;case 'expire':state.auth=false;state.authError='模拟会话已失效，请重新登录。本次输入和浏览状态已保留。';render();break;case 'password':$('#password').type=$('#password').type==='password'?'text':'password';break;case 'login':{let pw=$('#password').value;if(M.real){state.authBusy=true;state.authError='';render();try{await M.login(state.username,pw);state.auth=true;sync()}catch(e){state.authError=e.message}finally{state.authBusy=false;render()}break;}state.authBusy=true;state.authError='';render();await M.wait(700);state.authBusy=false;if(!M.cfg.online)state.authError='当前离线，首次登录需要联网。';else if(state.username==='demo-user'&&pw==='moss'){state.auth=true;await M.transaction('auth',true);sync()}else state.authError='模拟账号或密码不正确，请重试。';render();break}case 'read-error':state.read='error';state.page='records';render();break;case 'read-retry':if(M.real){try{await M.restore();state.read='ready';sync()}catch(e){state.read='error';render();throw e}break;}state.read='loading';render();await M.wait(600);if(!M.cfg.online){state.read='error';render()}else{state.read='ready';sync()}break;case 'empty':state.cache=[];state.read='empty';state.page='records';state.mode='time';render();toast('仅模拟空读取视图；点击重新读取恢复。');break;case 'export':await startExport();break;case 'export-retry':await startExport();break;case 'export-cancel':if(M.real){await cancelExport();break;}exportToken++;state.export.state='cancelled';state.modal=null;render();break;case 'export-dismiss':await dismissExport();break;case 'location':if(M.real){await saveReal();break;}state.modal={kind:'location',title:'将 ZIP 保存到文件',message:'选择你的保存位置。取消不会被当作成功。'};render();break;case 'download':{const x=state.export;let url=URL.createObjectURL(new Blob([x.bytes],{type:'application/zip'}));const a=document.createElement('a');a.href=url;a.download=x.file_name;a.click();setTimeout(()=>URL.revokeObjectURL(url),60000);toast('真实 ZIP 已交给浏览器下载；本原型不能确认浏览器最终保存位置。');break}}}catch(err){toast(err.message)}});
let backgroundPending=false;
function handleSystemBack(){
 if(state.modal){
  const dialog=state.modal;
  if(state.busy.has(dialog.id)){toast('正在处理，请稍候。');return true}
  if(dialog.kind==='location'&&state.export)state.export.state='cancelled';
  state.modal=null;render();return true;
 }
 if(state.export){
  const close=['succeeded','failed','cancelled','handed_off'].includes(state.export.state)?dismissExport:cancelExport;
  close().catch(e=>toast(e.message));return true;
 }
 if(MossAccounts.back())return true;
 if(state.tagEdit){state.tagEdit=null;render();return true}
 if(state.edit){state.edit=null;render();return true}
 if(state.revise){state.revise=false;render();return true}
 if(state.expanded){state.expanded=null;render();return true}
 if(state.page==='records'){
  if(M.real&&MossSearch.back())return true;
  state.page='write';render();return true;
 }
 if(window.MossNative&&M.real&&M.ready&&state.auth){
  if(backgroundPending)return true;
  backgroundPending=true;
  M.transaction('draft',state.draft).then(()=>MossNative.call('backgroundApp')).catch(e=>toast('草稿尚未可靠保存，请稍后再退出：'+e.message)).finally(()=>backgroundPending=false);
  return true;
 }
 return false;
}
window.mossHandleBack=handleSystemBack;
window.mossPrepareAppUpdate=async()=>{
 if(!M.real||!M.ready)throw Error('页面尚未就绪，请稍后重试。');
 if(state.busy.size||state.export&&!['succeeded','failed','cancelled','handed_off'].includes(state.export.state))throw Error('请先完成或取消当前操作和导出，再安装更新。');
 if(state.revise||state.edit||Object.values(state.editDrafts).some(v=>v?.trim()))throw Error('请先保存或取消当前编辑，再安装更新。');
 MossAccounts.prepareAppUpdate();
 if(state.auth)await M.transaction('draft',state.draft);
};

MossAccounts.bind({state,render,toast,sync,startExport});
M.subscribe(sync);render();M.init().then(async()=>{
 state.auth=!!(await M.get('auth'));
 if(M.real)state.draft=await M.get('draft')||'';
 state.export=(await M.get('export-session'))||null;
 if(state.export&&['preparing','downloading','saving'].includes(state.export.state)){
  state.export.state='failed';state.export.error='进程已中断；未确认保存成功，请重试。';
 }
 if(M.cfg.online){state.cache=M.copy(M.all());state.read=M.readError?'error':'ready'}else{state.cache=[];state.read='idle'}
 render();window.dispatchEvent(new Event('moss-boot-ready'));
}).catch(e=>{
 state.authError='本地存储不可用：'+e.message;
 $('#app').innerHTML='<div class="empty">手机本地存储初始化失败。请重新打开，或将此情况反馈给开发者。</div>';
 window.dispatchEvent(new Event('moss-boot-failed'));
});
window.mossTest={M,state,scenario,startExport,render,sync};


window.addEventListener('moss-auth-required',()=>{state.auth=false;state.authError='会话失效，请重新登录；未完成内容仅在原账号恢复。';render()});
window.addEventListener('moss-error',e=>toast(e.detail));
async function startRealExport(){
 if(exportClosing)await exportClosing;
 if(state.export&&!['succeeded','failed','cancelled','handed_off'].includes(state.export.state))return;
 const token=++exportToken;
 const x=state.export={state:'preparing',processed:0,total:0,received_bytes:0,total_bytes:null,excluded:[],file_name:null,error:null,bytes:null};render();
 try{
  let eid=await M.get('export-id'),task;
  if(eid)try{task=await M.request('/exports/'+eid);if(task.status==='failed')task=null}catch(e){if(![404,410].includes(e.status))throw e}
  if(!task){let intent=await M.get('export-intent');intent ||= {operation_id:M.uid(),format:'zip_markdown',scope:'all_completed'};await M.transaction('export-intent',intent);task=await M.request('/exports',{method:'POST',body:JSON.stringify(intent)});await M.transaction('export-id',task.id);await M.transaction('export-intent',null)}
  x.export_id=task.id;
  if(token!==exportToken){await M.releaseExport(task.id);return;}
  while(token===exportToken){
   x.processed=task.processed_records;x.total=task.total_records||0;
   x.excluded=task.excluded_incomplete_records?.map(r=>({...r,record_date:new Date(r.first_sent_at).toISOString().slice(0,10),sent_utc_offset_minutes:0}))||[];
   if(M.local&&!x.excluded.some(r=>r.id===M.local.record_id))x.excluded.push({...M.current(),title:'未送达的本地记录'});
   render();if(task.status==='ready')break;if(task.status==='failed')throw Error(task.error.message);
   await M.wait(500);task=await M.request('/exports/'+task.id);
  }
  if(token!==exportToken){await M.releaseExport(task.id);return;}
  x.file_name=task.file_name;x.total_bytes=task.file_bytes;x.state='downloading';render();
  const response=await M.request('/exports/'+task.id+'/download',{raw:true});const reader=response.body.getReader(),chunks=[];
  while(true){const {done,value}=await reader.read();if(token!==exportToken){await reader.cancel();await M.releaseExport(task.id);return}if(done)break;chunks.push(value);x.received_bytes+=value.length;render()}
  const bytes=new Uint8Array(x.received_bytes);let pos=0;for(const chunk of chunks){bytes.set(chunk,pos);pos+=chunk.length}
  const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(n=>n.toString(16).padStart(2,'0')).join('');
  if(bytes.length!==task.file_bytes||hash!==task.sha256)throw Error('下载长度或 SHA-256 校验失败');
  if(token!==exportToken){await M.releaseExport(task.id);return;}
  x.bytes=bytes;x.state='awaiting_location';render();
 }catch(e){if(token!==exportToken)return;x.state='failed';x.error=e.message;render()}
}
async function saveReal(){
 const x=state.export;if(!x?.bytes||x.state!=='awaiting_location')return;
 try{
  if(window.MossNative){x.state='saving';render();const result=await window.MossNative.saveExport(x.bytes,x.file_name);x.state='succeeded';x.local_uri=result.name;toast('文件已保存并完成 SHA-256 核对。')}
  else if(window.showSaveFilePicker){const handle=await showSaveFilePicker({suggestedName:x.file_name,types:[{description:'ZIP',accept:{'application/zip':['.zip']}}]});x.state='saving';render();const stream=await handle.createWritable();try{await stream.write(x.bytes);await stream.close()}catch(e){await stream.abort().catch(()=>{});throw e}x.state='succeeded';x.local_uri=handle.name}
  else{const a=document.createElement('a'),url=URL.createObjectURL(new Blob([x.bytes],{type:'application/zip'}));a.href=url;a.download=x.file_name;a.click();setTimeout(()=>URL.revokeObjectURL(url),60000);x.state='handed_off';toast('ZIP 已交给浏览器下载，无法确认最终保存结果。')}
  x.bytes=null;render();
 }catch(e){if(e.name==='AbortError'){x.bytes=null;await cancelExport();return;}x.state='failed';x.error=e.message;render();return}
 try{if(!await M.releaseExport(x.export_id))toast('文件已交付；云端临时文件清理将在联网后重试。')}
 catch{toast('文件已交付；云端临时文件将由到期清理处理。')}
}

let exportClosing=null;
async function dismissExport(message=''){
 if(exportClosing)return exportClosing;
 exportToken++;
 const closing=state.export;
 exportClosing=(async()=>{
  await MossUI.close($('.export-island'));
  if(closing)closing.bytes=null;
  if(M.real)await M.releaseExport(closing?.export_id,{background:true});
  await M.transaction('export-session',null);
  if(state.export===closing)state.export=null;
  if(message)toast(message);else render();
 })();
 try{await exportClosing}finally{exportClosing=null}
}
async function cancelExport(){await dismissExport('已取消导出。')}
