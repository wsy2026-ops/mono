/* MONO 设备指纹 —— 同一台机器算出来一样，换机器/换浏览器就变。
   匹配防自配用：同一台设备开两个页面各排一次，就是自己跟自己下。 */
(function (g) {
  'use strict';

  var _cache = null;

  function hash32(str) {
    var h = 0;
    for (var i = 0; i < str.length; i++) h = (h * 33 + str.charCodeAt(i)) >>> 0;
    return h.toString(36);
  }

  /* canvas 指纹：字体渲染差异同浏览器稳定、跨浏览器不同。
     不追求唯一，只求「同一台机器每次算出来一样」。 */
  function canvasMark() {
    try {
      var c = document.createElement('canvas');
      c.width = 220; c.height = 44;
      var x = c.getContext('2d');
      x.textBaseline = 'top';
      x.font = '14px "Arial"';
      x.fillStyle = '#f60'; x.fillRect(10, 5, 80, 20);
      x.fillStyle = '#069'; x.font = '16px "Times New Roman"';
      x.fillText('mono-fp-中文', 12, 8);
      x.fillStyle = 'rgba(120,180,60,0.7)';
      x.fillText('abcdefghijklmnop', 4, 22);
      return hash32(c.toDataURL());
    } catch (e) {
      return 'nocanvas';
    }
  }

  function screenMark() {
    try {
      return screen.width + 'x' + screen.height + '@' + screen.colorDepth;
    } catch (e) {
      return 'noscreen';
    }
  }

  /* 硬件/并发数。虚拟机和小号往往这些值一样或很规整。 */
  function hwMark() {
    try {
      return (navigator.hardwareConcurrency || 0) + '-' + (navigator.maxTouchPoints || 0)
        + '-' + (navigator.deviceMemory || 0) + '-' + new Date().getTimezoneOffset();
    } catch (e) {
      return 'nohw';
    }
  }

  g.monoFp = function () {
    if (_cache) return _cache;
    _cache = canvasMark() + '.' + screenMark() + '.' + hash32(hwMark());
    return _cache;
  };

  /* fetch 的同款封装：自动带上设备指纹头。 */
  g.monoFetch = function (url, opts) {
    opts = opts || {};
    var h = opts.headers || {};
    try { h['X-FP-Marks'] = g.monoFp(); } catch (e) {}
    opts.headers = h;
    opts.credentials = opts.credentials || 'same-origin';
    return fetch(url, opts);
  };
})(window);
