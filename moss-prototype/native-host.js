/* Native services only. The page, state machine and API adapter remain the shared frontend. */
window.MossNative = (() => {
  if (!window.MossHost?.postMessage) return null;
  const requests = new Map();
  let sequence = 0;
  document.documentElement.classList.add('native-host');
  window.MossHost.onmessage = event => {
    const response = JSON.parse(event.data), task = requests.get(response.id);
    if (!task) return;
    requests.delete(response.id);
    if (response.ok) task.resolve(response.value);
    else {
      const error = new Error(response.error || '手机操作失败');
      error.name = response.name || 'Error';
      task.reject(error);
    }
  };
  function call(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = String(++sequence);
      requests.set(id, {resolve, reject});
      window.MossHost.postMessage(JSON.stringify({id, method, params}));
    });
  }
  async function saveExport(bytes, name) {
    let binary = '';
    for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
    return call('saveExport', {name, base64: btoa(binary)});
  }
  // Check only once after the page's durable local state has been restored.
  window.addEventListener('moss-boot-ready', () => {
    call('checkAppUpdate', {manual:false}).catch(() => {});
  }, {once:true});
  return {call, saveExport};
})();
