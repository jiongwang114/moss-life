/* Native WebMessagePort fallback for WebViews predating WEB_MESSAGE_LISTENER. */
(function () {
  'use strict';
  if (window.MossHost) return;
  var port = null, pending = [];
  window.MossHost = {
    onmessage: null,
    postMessage: function (data) {
      if (port) port.postMessage(data);
      else pending.push(data);
    }
  };
  window.addEventListener('message', function (event) {
    if (event.data !== 'moss-native-channel' || event.ports.length !== 1 || port) return;
    port = event.ports[0];
    port.onmessage = function (message) {
      if (window.MossHost.onmessage) window.MossHost.onmessage(message);
    };
    while (pending.length) port.postMessage(pending.shift());
  });
})();
