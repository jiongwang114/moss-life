/* Independent startup diagnostics; no account data or credentials are shown. */
(function () {
  'use strict';
  var completed = false, timer;
  document.documentElement.classList.add('native-host', 'real-mode');
  function show(message) {
    if (completed) return;
    var app = document.getElementById('app');
    if (!app) return;
    app.textContent = '';
    var panel = document.createElement('div');
    panel.className = 'empty';
    var title = document.createElement('h2');
    title.textContent = 'MOSS 暂时无法打开';
    var text = document.createElement('p');
    text.textContent = message;
    var retry = document.createElement('button');
    retry.className = 'btn primary';
    retry.textContent = '重新打开';
    retry.onclick = function () { location.reload(); };
    panel.appendChild(title); panel.appendChild(text); panel.appendChild(retry); app.appendChild(panel);
  }
  window.addEventListener('error', function (event) {
    if (event.target && event.target.tagName === 'SCRIPT' || event.error || event.message) {
      clearTimeout(timer);
      show('界面程序未能启动。请重试；如果仍无法打开，请更新手机的 Android System WebView 后再试。');
    }
  }, true);
  window.addEventListener('moss-boot-ready', function () { completed = true; clearTimeout(timer); });
  window.addEventListener('moss-boot-failed', function () {
    clearTimeout(timer);
    show('手机存储或界面初始化失败。请重新打开；如果仍失败，请将此情况反馈给开发者。');
  });
  timer = setTimeout(function () {
    show('启动等待时间较长。请重新打开；如果仍无法打开，请检查网络并更新 Android System WebView。');
  }, 20000);
})();
