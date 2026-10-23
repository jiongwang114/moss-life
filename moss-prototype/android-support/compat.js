/* Loaded before all application code. This file must stay parseable by Chrome 61. */
(function () {
  'use strict';
  function define(target, key, value) {
    Object.defineProperty(target, key, {configurable: true, writable: true, value: value});
  }
  if (!window.queueMicrotask) define(window, 'queueMicrotask', function (callback) {
    Promise.resolve().then(callback).catch(function (error) {setTimeout(function () {throw error;}, 0);});
  });
  if (!crypto.randomUUID) define(crypto, 'randomUUID', function () {
    // Record/operation identifiers still use secure randomness on old WebViews.
    var bytes = crypto.getRandomValues(new Uint8Array(16)), result = '';
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    for (var i = 0; i < bytes.length; i++) {
      if (i === 4 || i === 6 || i === 8 || i === 10) result += '-';
      result += ('0' + bytes[i].toString(16)).slice(-2);
    }
    return result;
  });
  if (!window.structuredClone) {
    // MOSS copies JSON record snapshots. Keep undefined and repeated references,
    // which a JSON stringify/parse round trip would lose.
    define(window, 'structuredClone', function (value) {
      var seen = new Map();
      function copy(item) {
        if (item === null || typeof item !== 'object') return item;
        if (seen.has(item)) return seen.get(item);
        if (item instanceof Date) return new Date(item.getTime());
        if (item instanceof ArrayBuffer) return item.slice(0);
        if (ArrayBuffer.isView(item)) return new item.constructor(item);
        var result = Array.isArray(item) ? [] : {};
        seen.set(item, result);
        Object.keys(item).forEach(function (key) {
          Object.defineProperty(result, key, {value: copy(item[key]), enumerable: true, writable: true, configurable: true});
        });
        return result;
      }
      return copy(value);
    });
  }
  if (!Object.groupBy) define(Object, 'groupBy', function (items, selector) {
    var groups = Object.create(null), index = 0;
    Array.from(items).forEach(function (item) {
      var key = selector(item, index++);
      if (!Object.prototype.hasOwnProperty.call(groups, key)) groups[key] = [];
      groups[key].push(item);
    });
    return groups;
  });
  if (!Array.prototype.at) define(Array.prototype, 'at', function (index) {
    index = Math.trunc(Number(index) || 0);
    return this[index < 0 ? this.length + index : index];
  });
  if (!Array.prototype.flat) define(Array.prototype, 'flat', function (depth) {
    var result = [], limit = depth === undefined ? 1 : Math.max(0, Math.trunc(depth));
    function append(items, remaining) {
      items.forEach(function (item) {
        if (Array.isArray(item) && remaining > 0) append(item, remaining - 1);
        else result.push(item);
      });
    }
    append(this, limit);
    return result;
  });
  if (!String.prototype.replaceAll) define(String.prototype, 'replaceAll', function (search, replacement) {
    if (search instanceof RegExp) {
      if (!search.global) throw new TypeError('replaceAll requires a global RegExp');
      return this.replace(search, replacement);
    }
    var pattern = String(search).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return this.replace(new RegExp(pattern, 'g'), replacement);
  });
  if (!Promise.prototype.finally) define(Promise.prototype, 'finally', function (callback) {
    var C = this.constructor;
    if (typeof callback !== 'function') return this.then(callback, callback);
    return this.then(function (value) {
      return C.resolve(callback()).then(function () { return value; });
    }, function (error) {
      return C.resolve(callback()).then(function () { throw error; });
    });
  });
  if (!Element.prototype.replaceChildren) define(Element.prototype, 'replaceChildren', function () {
    var fragment = document.createDocumentFragment();
    for (var i = 0; i < arguments.length; i++) {
      fragment.appendChild(arguments[i] instanceof Node ? arguments[i] : document.createTextNode(String(arguments[i])));
    }
    while (this.firstChild) this.removeChild(this.firstChild);
    this.appendChild(fragment);
  });
  // Chrome 61 has animate(), but not Animation.finished. Keep real UI logic and
  // disable decorative motion rather than inventing animation completion.
  if (!window.Animation || !('finished' in Animation.prototype)) {
    document.body.classList.add('static');
    window.matchMedia = (function (original) {
      return function (query) {
        if (query === '(prefers-reduced-motion: reduce)') return {matches: true};
        return original.call(window, query);
      };
    })(window.matchMedia);
  }
})();
