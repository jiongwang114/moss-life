/* Personal account settings; real operations are unavailable in Mock mode. */
window.MossAccounts=(()=>{
  let ctx,section='home',busy=false,error='',success='',form={},authMode='login',returnPage='write';
  let referralData=null,referralEpoch=0;
  const referralErrors={REFERRAL_UNAVAILABLE:'推荐码暂时无法读取，请稍后重试。'};
  const errors={AUTH_INVALID_CREDENTIALS:'账号或当前密码不正确。',AUTH_REQUIRED:'登录已失效，请重新登录。',AUTH_RATE_LIMITED:'操作太频繁，请稍等一分钟再试。',USERNAME_INVALID:'账号需为 3—40 位小写英文字母、数字、下划线或短横线。',USERNAME_TAKEN:'这个账号已被使用。',PASSWORD_INVALID:'密码需为 10—128 个字符。',NICKNAME_INVALID:'昵称需为 1—40 个字符。',INVITE_INVALID:'邀请码无效、已过期或已用完。',CONFIRMATION_REQUIRED:'请输入“注销”确认。'};
  const esc=v=>Moss.escape(String(v??'')),message=e=>errors[e.detail?.code]||referralErrors[e.detail?.code]||e.message;
  const field=(id,label,type='text',extra='')=>`<label for="${id}">${label}</label><input id="${id}" name="${id}" type="${type}" value="${esc(form[id]||'')}" ${extra} required>`;
  const submit=label=>`<button class="btn primary" type="submit" ${busy?'disabled':''}>${busy?'正在处理…':label}</button>`;
  const notices=()=>`${error?`<p class="status-note" role="alert">${esc(error)}</p>`:''}${success?`<p class="status-note" role="status">${esc(success)}</p>`:''}`;
  const row=(action,title,detail='',danger=false)=>`<button class="settings-row ${danger?'danger-text':''}" data-account="${action}"><span><b>${title}</b>${detail?`<small>${esc(detail)}</small>`:''}</span><span aria-hidden="true">›</span></button>`;
  const render=()=>ctx?.render(),clearForms=()=>{form={};error='';success=''};
  function open(){if(ctx.state.page!=='settings')returnPage=ctx.state.page;section='home';clearForms();ctx.state.page='settings';render();if(Moss.real&&Moss.user)Moss.refreshProfile().then(render).catch(e=>{error=message(e);render()})}
  function login(){
    if(!Moss.real)return null;
    const reg=authMode==='register';
    return `<section class="login account-form"><div class="eyebrow">YOUR PRIVATE GARDEN</div><h2>${reg?'创建你的 MOSS 账号':'欢迎回来，<br>继续记录你的生活。'}</h2><p>${reg?'使用邀请码创建自己的账号。':'登录后，继续记录和回看自己的生活。'}</p><form id="account-${authMode}">${field('account-username','登录账号','text','autocomplete="username" autocapitalize="none" spellcheck="false" minlength="3" maxlength="40"')}${reg?field('account-nickname','昵称','text','autocomplete="nickname" maxlength="40"'):''}${field('account-password','密码','password',`autocomplete="${reg?'new-password':'current-password'}" maxlength="128"`)}${reg?field('account-password-confirm','再次输入密码','password','autocomplete="new-password" minlength="10" maxlength="128"')+field('account-invite','邀请码','text','autocomplete="off" maxlength="128"')+'<p class="read-time">账号为 3—40 位小写英文、数字、下划线或短横线，注册后固定；密码为 10—128 个字符。</p>':''}${notices()}${ctx.state.authError?`<p role="alert">${esc(ctx.state.authError)}</p>`:''}${submit(reg?'注册账号':'进入花园')}</form><button class="btn ghost" data-account="auth-toggle" ${busy?'disabled':''}>${reg?'已有账号，去登录':'有邀请码？注册账号'}</button></section>`;
  }
  function page(){
    const u=Moss.user,back=`<button class="clear settings-back" data-account="${section==='home'?'close':'home'}">‹ ${section==='home'?(returnPage==='records'?'返回看记录':'返回写记录'):'账号与设置'}</button>`,head=`<section class="settings-page account-form">${back}`;
    if(!Moss.real)return head+'<h2>账号与设置</h2><p>当前为独立模拟模式；账号操作请使用真实服务。</p></section>';
    if(section==='home')return head+`<div class="eyebrow">YOUR ACCOUNT</div><h2>账号与设置</h2><div class="account-summary"><b>${esc(u?.nickname||'我的账号')}</b><small>登录账号：${esc(u?.username||'正在读取…')}</small></div><div class="settings-group">${row('nickname','修改昵称')}${row('password','修改密码')}${row('referral','我的推荐码','邀请一位朋友加入 MOSS')}${row('export','导出我的记录','导出已完成记录，保存为 ZIP 文件')}</div><div class="settings-group">${row('privacy','数据与隐私说明')}${row('about','关于 MOSS')}</div><div class="settings-group">${row('switch','切换账号')}${row('logout','退出当前账号')}${row('logout-all','退出所有设备')}</div><div class="settings-group">${row('delete','注销账号','删除账号及相关数据，不提供恢复',true)}</div>${notices()}</section>`;
    if(section==='referral'){
      const r=referralData,active=r?.status==='active';
      return head+`<h2>我的推荐码</h2><p>每次只能有一个有效推荐码，从生成时起 7 天内有效，仅供一人注册成功后失效。使用或过期后，可以重新生成。</p>${r?`<p role="status">${{not_generated:'还没有生成推荐码。',active:'推荐码可用，请仅分享给你想邀请的朋友。',used:'上一个推荐码已使用，可以重新生成。',expired:'上一个推荐码已过期，可以重新生成。'}[r.status]||'请刷新推荐码状态。'}</p>${active?`<label for="referral-code">推荐码（注册页填写“邀请码”）</label><textarea class="referral-code" id="referral-code" readonly rows="2" spellcheck="false">${esc(r.code)}</textarea><p>到期时间：${esc(new Date(r.expires_at).toLocaleString())}</p><button class="btn primary" data-account="referral-copy" ${busy?'disabled':''}>复制推荐码</button>`:`<button class="btn primary" data-account="referral-generate" ${busy?'disabled':''}>${busy?'正在处理…':r.status==='not_generated'?'生成推荐码':'重新生成推荐码'}</button>`}`:busy?'<p role="status">正在读取推荐码…</p>':''}<button class="btn ghost" data-account="referral-refresh" ${busy?'disabled':''}>刷新状态</button>${notices()}</section>`;
    }
    if(section==='nickname')return head+`<h2>修改昵称</h2><p>登录账号 ${esc(u?.username)} 保持固定。</p><form id="account-nickname-form">${field('new-nickname','昵称','text','maxlength="40" autocomplete="nickname"')}${notices()}${submit('保存昵称')}</form></section>`;
    if(section==='password')return head+`<h2>修改密码</h2><p>修改成功后，所有设备都会退出，请使用新密码重新登录。</p><form id="account-password-form">${field('current-password','当前密码','password','autocomplete="current-password" maxlength="128"')}${field('new-password','新密码','password','autocomplete="new-password" minlength="10" maxlength="128"')}${field('confirm-password','再次输入新密码','password','autocomplete="new-password" minlength="10" maxlength="128"')}${notices()}${submit('修改密码并退出')}</form></section>`;
    if(section==='delete')return head+`<h2>注销账号</h2><div class="status-note danger-text">账号、记录、搜索索引和服务器临时导出文件将被删除，不提供恢复。当前设备的账号数据也会清理。</div><p>已经自行下载的文件、其他离线设备上的内容，需要你自行删除；服务器历史备份按实际保留策略处理，不承诺同步清除。</p><p>如需留存记录，请先返回设置导出。</p><form id="account-delete-form">${field('delete-password','当前密码','password','autocomplete="current-password" maxlength="128"')}${field('delete-confirmation','请输入“注销”确认','text','autocomplete="off"')}<label class="delete-ack"><input id="delete-ack" type="checkbox" ${form['delete-ack']?'checked':''} required> 我理解删除后无法恢复</label>${notices()}${submit('永久注销并删除数据')}</form></section>`;
    if(section==='privacy')return head+`<h2>数据与隐私说明</h2><h3>账号与记录</h3><p>账号和记录保存在服务器中，按登录用户检查访问权限。密码保存为不可直接还原的校验值。手机保存登录凭证和未完成提交，登录凭证使用 Android 安全存储加密。</p><h3>AI 处理</h3><p>启用在线 AI 时，处理所需的记录正文会发送到服务器配置的 AI 服务，用于清理正文、生成标题和匹配标签。提供方及其数据处理政策以运营方实际配置为准。</p><h3>智能搜索</h3><p>服务器保存记录的搜索索引，用于按含义检索；返回结果前会检查账号归属。</p><h3>退出与注销</h3><p>退出不会删除云端记录。有草稿或未发送内容时，需要先处理完才能主动退出。注销清理在线账号数据；自行保存的文件、离线设备和服务器历史备份不在当前设备可直接删除的范围内。</p></section>`;
    const v=window.MOSS_RELEASE||{version:'未知',notes:[]};
    return head+`<div class="eyebrow">ABOUT MOSS</div><h2>关于 MOSS</h2><p>MOSS · 个人生活记录</p><p>留住当下，用 AI 协助整理，再通过时间、分类和搜索回看自己的记录。</p><p>当前${window.MossNative?'应用':'网页'}版本：<strong id="moss-version">${esc(v.version)}</strong></p><h3>当前版本更新</h3><ul>${v.notes.map(n=>`<li>${esc(n)}</li>`).join('')}</ul></section>`;
  }
  function blockLeaving(){
    const s=ctx.state;
    if(s.draft.trim()||s.reviseDraft.trim()||Moss.local&&Moss.local.delivery_state!=='accepted'||Object.values(s.editDrafts).some(v=>v?.trim()))throw Error('还有草稿、未发送内容或未保存的编辑，请先返回处理完。');
    if(s.busy.size||s.export&&!['succeeded','failed','cancelled','handed_off'].includes(s.export.state))throw Error('还有操作或导出正在进行，请先完成或取消。');
  }
  function resetUI(){referralEpoch++;referralData=null;const s=ctx.state;Object.assign(s,{auth:false,cache:[],draft:'',expanded:null,edit:null,tagEdit:null,editDrafts:{},revise:false,reviseDraft:'',filters:[],tree:{},topicOpen:{},busy:new Set(),modal:null,export:null,page:'write',mode:'time',read:'idle',scroll:{write:0,records:0},toast:''});window.MossSearch?.clearAccount();window.MossUI?.clearAccount();clearForms();section='home'}
  async function loadReferral(create=false){
    const epoch=referralEpoch,owner=Moss.user?.id;busy=true;error='';success='';render();
    try{
      const result=await Moss.request('/account/referral',create?{method:'POST',body:'{}'}:{});
      if(epoch===referralEpoch&&owner===Moss.user?.id)referralData=result;
    }catch(e){if(epoch===referralEpoch)error=message(e)}
    finally{busy=false;render()}
  }
  function back(){
    if(busy){ctx.toast('正在处理账号操作，请稍候。');return true}
    if(!ctx.state.auth&&authMode==='register'){authMode='login';clearForms();ctx.state.authError='';render();return true}
    if(ctx.state.page!=='settings')return false;
    if(section!=='home'){section='home';clearForms()}else ctx.state.page=returnPage;
    render();return true;
  }
  async function action(name){
    if(busy)return;
    if(name==='close'){back();return}
    if(name==='auth-toggle'){authMode=authMode==='login'?'register':'login';clearForms();ctx.state.authError='';render();return}
    if(name==='home'){back();return}
    if(name==='referral'){section='referral';referralData=null;clearForms();await loadReferral();return}
    if(name==='referral-refresh'||name==='referral-generate'){await loadReferral(name==='referral-generate');return}
    if(name==='referral-copy'){
      if(!referralData?.code)return;
      if(Date.now()>=Date.parse(referralData.expires_at)){await loadReferral();return}
      try{await navigator.clipboard.writeText(referralData.code);success='推荐码已复制。';error=''}
      catch{error='请长按推荐码并选择复制。';success='';render();const input=document.getElementById('referral-code');input?.focus();input?.select();return}
      render();return;
    }
    if(['nickname','password','privacy','about','delete'].includes(name)){
      if(name==='password')blockLeaving();section=name;clearForms();if(name==='nickname')form['new-nickname']=Moss.user?.nickname||'';
      if(name==='about'&&window.MossNative)try{const info=await MossNative.call('appInfo');window.MOSS_RELEASE={...window.MOSS_RELEASE,version:info.version}}catch{}
      render();return;
    }
    if(name==='export'){await ctx.startExport();return}
    if(['switch','logout','logout-all'].includes(name)){blockLeaving();busy=true;render();try{await Moss.logout(name==='logout-all');authMode='login';success=name==='logout-all'?'所有设备已退出。':'已退出，请登录账号。'}finally{busy=false;render()}}
  }
  async function handleForm(id){
    if(busy)return;error='';success='';ctx.state.authError='';const v={...form};busy=true;render();
    try{
      if(id==='account-register'){
        if(v['account-password']!==v['account-password-confirm'])throw Error('两次输入的密码不一致。');
        await Moss.request('/auth/register',{method:'POST',body:JSON.stringify({username:v['account-username']?.trim(),password:v['account-password'],nickname:v['account-nickname'],invite_code:v['account-invite']?.trim()})});authMode='login';clearForms();form['account-username']=v['account-username'];success='注册成功，请使用新账号登录。';
      }else if(id==='account-login'){
        await Moss.login(v['account-username']?.trim(),v['account-password']);ctx.state.auth=true;clearForms();ctx.sync();
      }else if(id==='account-nickname-form'){
        await Moss.updateNickname(v['new-nickname']);section='home';clearForms();success='昵称已更新。';
      }else if(id==='account-password-form'){
        blockLeaving();if(v['new-password']!==v['confirm-password'])throw Error('两次输入的新密码不一致。');await Moss.changePassword(v['current-password'],v['new-password']);success='密码已修改，所有设备已退出，请重新登录。';authMode='login';
      }else if(id==='account-delete-form'){
        const r=await Moss.deleteAccount(v['delete-password'],v['delete-confirmation']);authMode='login';success=r.status==='deleted'?'账号及相关在线数据已删除。':'账号已停用，数据清理正在重试；尚未确认全部删除完成。';
      }
    }catch(e){error=message(e)}finally{busy=false;render()}
  }
  document.addEventListener('input',e=>{if(e.target.closest('.account-form'))form[e.target.id]=e.target.type==='checkbox'?e.target.checked:e.target.value});
  document.addEventListener('submit',e=>{if(e.target.id.startsWith('account-')){e.preventDefault();e.stopImmediatePropagation();handleForm(e.target.id)}});
  document.addEventListener('click',e=>{const b=e.target.closest('[data-account]');if(!b)return;e.preventDefault();e.stopImmediatePropagation();action(b.dataset.account).catch(e=>{error=message(e);render()})});
  window.addEventListener('moss-account-cleared',()=>{if(ctx){resetUI();render()}});
  window.addEventListener('moss-account-ready',e=>{ctx.state.draft=e.detail.draft;render()});
  return {bind:value=>ctx=value,open,page,login,resetUI,back};
})();
