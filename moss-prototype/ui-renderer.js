/* Preserve live DOM nodes so background snapshots never restart interaction animations. */
window.MossUI = (() => {
  const ease = 'cubic-bezier(.22,.8,.25,1)';
  let route = null, ghost = null;
  const reduced = () => document.body.classList.contains('static') || matchMedia('(prefers-reduced-motion: reduce)').matches;
  function key(node) {
    if (node.nodeType !== Node.ELEMENT_NODE) return null;
    if (node.id) return 'id:' + node.id;
    if (node.dataset.record) return 'record:' + node.dataset.record;
    if (node.dataset.tagId) return 'tag:' + node.dataset.tagId;
    if (node.dataset.tree) return 'tree:' + node.dataset.tree;
    if (node.dataset.topic) return 'topic:' + node.dataset.topic;
    if (node.dataset.motionKey) return 'part:' + node.dataset.motionKey;
    if (node.dataset.action) return ['action',node.dataset.action,node.classList.contains('record-title')?'title':node.classList.contains('open-line')?'toggle':'',node.dataset.id||'',node.dataset.tag||'',node.dataset.key||''].join(':');
    for (const name of ['app-head','scroll','composer','bottom-nav','export-island','overlay','toast','record-expansion','record-preview','markdown','editor']) {
      if (node.classList.contains(name)) return 'part:' + name;
    }
    return null;
  }
  function compatible(a,b) { return a.nodeType === b.nodeType && (a.nodeType !== Node.ELEMENT_NODE || a.tagName === b.tagName); }
  function attributes(current, next) {
    for (const attr of [...current.attributes]) if (!next.hasAttribute(attr.name) && !(current.mossClosing && attr.name==='style')) current.removeAttribute(attr.name);
    for (const attr of [...next.attributes]) if (current.getAttribute(attr.name) !== attr.value) current.setAttribute(attr.name,attr.value);
  }
  function morph(current, next) {
    const field = current instanceof HTMLInputElement || current instanceof HTMLTextAreaElement;
    if (current.isEqualNode(next) && (!field || current.value === next.value)) return;
    if (current.nodeType !== Node.ELEMENT_NODE) { current.nodeValue = next.nodeValue; return; }
    attributes(current,next);
    if (field) {
      // Only assign a changed value; ordinary status updates preserve selection and IME input.
      if (!current.mossComposing && current.value !== next.value) current.value = next.value;
      if (current instanceof HTMLInputElement) current.checked = next.checked;
      return;
    }
    if (current.classList.contains('collapse-region')) {
      clearTimeout(current.collapseTimer);
      if (!current.classList.contains('is-open') && current.firstElementChild?.childNodes.length && !next.firstElementChild?.childNodes.length && !reduced()) {
        current.collapseTimer = setTimeout(() => {
          if (!current.classList.contains('is-open')) current.firstElementChild?.replaceChildren();
        },280);
        return;
      }
    }
    const changedTitle=current.classList.contains('record-title') && current.textContent!==next.textContent;
    children(current,next);
    if(changedTitle && !reduced())current.animate([{opacity:.4},{opacity:1}],{duration:180,easing:ease});
  }
  function children(current,next) {
    const old = [...current.childNodes], used = new Set();
    const keyed = new Map(old.map(node => [key(node),node]).filter(([id])=>id));
    let cursor = current.firstChild;
    for (const child of [...next.childNodes]) {
      const id = key(child);
      let existing = id ? keyed.get(id) : null;
      if (!id) existing = old.find(node => !used.has(node) && !key(node) && compatible(node,child));
      if (existing && compatible(existing,child)) {
        used.add(existing);
        if (existing !== cursor) current.insertBefore(existing,cursor);
        morph(existing,child);
        cursor = existing.nextSibling;
      } else {
        const added = child.cloneNode(true);
        current.insertBefore(added,cursor);
        if (!reduced() && added.nodeType === Node.ELEMENT_NODE) {
          if (added.matches('.export-island,.pending-banner,.status-note')) {
            added.animate([{opacity:0,transform:'translateY(6px)'},{opacity:1,transform:'translateY(0)'}],{duration:220,easing:ease});
          }
        }
      }
    }
    for (const node of old) if (!used.has(node)) node.remove();
  }
  function render(html, nextRoute) {
    const root = document.querySelector('#app'), oldScroll = root.querySelector('.scroll');
    const switched = route !== null && route !== nextRoute;
    if(switched){ghost?.remove();ghost=null;}
    if (switched && oldScroll && !reduced()) {
      const box=oldScroll.getBoundingClientRect();
      ghost=oldScroll.cloneNode(true);
      ghost.querySelectorAll('[id]').forEach(node=>node.removeAttribute('id'));
      ghost.removeAttribute('id');
      ghost.setAttribute('aria-hidden','true'); ghost.inert=true;
      ghost.classList.add('route-ghost');
      Object.assign(ghost.style,{position:'fixed',left:box.left+'px',top:box.top+'px',width:box.width+'px',height:box.height+'px',zIndex:35,pointerEvents:'none',background:'var(--paper)'});
      document.body.append(ghost); ghost.scrollTop=oldScroll.scrollTop;
    }
    const template=document.createElement('template');template.innerHTML=html;
    children(root,template.content);
    route=nextRoute;
    if (switched && !reduced()) {
      const leaving=ghost;
      leaving?.animate([{opacity:1},{opacity:0}],{duration:160,easing:ease}).finished.finally(()=>{leaving.remove();if(ghost===leaving)ghost=null});
      root.querySelector('.scroll')?.animate([{opacity:.25,transform:'translateY(5px)'},{opacity:1,transform:'translateY(0)'}],{duration:240,easing:ease});
    }
  }
  async function close(element) {
    if (!element || reduced()) return;
    const box=element.getBoundingClientRect(), style=getComputedStyle(element);
    const collapse=element.matches('.export-island');
    const from={opacity:1,transform:'translateY(0)'},to={opacity:0,transform:'translateY(8px)'};
    if (collapse) {
      Object.assign(from,{height:box.height+'px',paddingTop:style.paddingTop,paddingBottom:style.paddingBottom,marginBottom:style.marginBottom});
      Object.assign(to,{height:'0px',paddingTop:'0px',paddingBottom:'0px',marginBottom:'0px'});
    }
    element.mossClosing=true;
    element.style.overflow='hidden';
    if(element.matches('.overlay')) {
      // Keep the scrim intercepting touches until its exit finishes; do not click through it.
      element.addEventListener('click',event=>{event.preventDefault();event.stopImmediatePropagation()},{capture:true});
    } else element.style.pointerEvents='none';
    try { await element.animate([from,to],{duration:220,easing:ease,fill:'forwards'}).finished; } catch {}
  }
  document.addEventListener('compositionstart',event=>{event.target.mossComposing=true});
  document.addEventListener('compositionend',event=>{event.target.mossComposing=false});
  function clearAccount(){ghost?.remove();ghost=null;route=null;document.querySelector('#app')?.replaceChildren()}
  return {render,close,reduced,clearAccount};
})();
