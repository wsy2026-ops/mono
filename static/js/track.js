/* track.js — 站点功能规划第 046 项 · 全站行为埋点 SDK
 *
 * 原则：**用户无感、不阻塞页面**。
 *   - 优先 navigator.sendBeacon（关页面也能送达），降级 fetch(keepalive)
 *   - 只采集：页面停留时长 / JS 错误 / 来源渠道 / data-track 标记的关键点击
 *   - 不采集输入内容、不做全局点击监听、不碰任何隐私字段
 *   - 登录用户带 uid（服务端从 session 认），未登录匿名也记（行为分布要有分母）
 *
 * 后台看板：/admin/metrics
 */
(function () {
  'use strict';
  if (window.__monoTrackOn) return;
  window.__monoTrackOn = 1;

  var enter = Date.now();
  var maxScroll = 0;
  var events = [];
  var flushed = false;

  function post(payload) {
    try {
      var body = JSON.stringify(payload);
      if (navigator.sendBeacon) {
        navigator.sendBeacon('/api/track', new Blob([body], {type: 'application/json'}));
      } else {
        fetch('/api/track', {method: 'POST', body: body, keepalive: true,
          headers: {'Content-Type': 'application/json'}}).catch(function () {});
      }
    } catch (e) { /* 埋点永远不能影响用户 */ }
  }

  function push(action, extra) {
    events.push(Object.assign({action: action, ts: Date.now()}, extra || {}));
    if (events.length >= 10) flush();
  }

  function flush() {
    if (flushed || !events.length) return;
    flushed = true;
    var dur = Math.round((Date.now() - enter) / 1000);
    post({
      events: events,
      dur: dur,                                   // 页面停留秒数
      scroll: maxScroll,                          // 最大滚动深度 %
      ref: document.referrer || '',               // 来源渠道
      path: location.pathname
    });
    events = [];
  }

  // 停留时长：页面隐藏/关闭时结算（关页面 sendBeacon 是唯一可靠通道）
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden') flush();
  });
  window.addEventListener('pagehide', flush);

  // 滚动深度（采样，防抖）
  window.addEventListener('scroll', function () {
    var h = document.documentElement;
    var pct = Math.round((h.scrollTop + window.innerHeight) / (h.scrollHeight || 1) * 100);
    if (pct > maxScroll) maxScroll = Math.min(100, pct);
  }, {passive: true});

  // JS 错误 → 错误率
  window.addEventListener('error', function (e) {
    push('js_error', {msg: String(e.message || '').slice(0, 120),
                      src: String(e.filename || '').slice(0, 120)});
  }, true);
  window.addEventListener('unhandledrejection', function () {
    push('js_error', {msg: 'unhandledrejection'});
  });

  // 关键点击：任何带 data-track 属性的元素（后台配，不全局监听）
  document.addEventListener('click', function (e) {
    var el = e.target && e.target.closest ? e.target.closest('[data-track]') : null;
    if (el) push('click', {key: el.getAttribute('data-track')});
  }, true);

  // 来源渠道首跳也记一条（无 uid 的匿名访问也有分母价值）
  push('visit', {ref: document.referrer || ''});
})();
