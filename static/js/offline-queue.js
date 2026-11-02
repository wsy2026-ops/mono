(function () {
  var KEY = 'mono_pending_scores';
  var MAX = 200;

  function load() {
    try { return JSON.parse(localStorage.getItem(KEY)) || []; } catch (e) { return []; }
  }

  function save(list) {
    try { localStorage.setItem(KEY, JSON.stringify(list.slice(-MAX))); } catch (e) {}
    paint();
  }

  function count() { return load().length; }

  var badge = null;

  function paint() {
    var n = count();
    if (!badge) {
      badge = document.getElementById('monoOfflineBadge');
      if (!badge) return;
    }
    badge.textContent = n ? ('待上传成绩 ' + n + ' 条 · 点这里重试') : '';
    badge.style.display = n ? 'block' : 'none';
  }

  function toast(msg) {
    try {
      if (window.MONO && window.MONO.toast) { window.MONO.toast(msg); return; }
    } catch (e) {}
    var t = document.createElement('div');
    t.textContent = msg;
    t.style.cssText = 'position:fixed;left:50%;top:74px;transform:translateX(-50%);z-index:19999;' +
      'padding:10px 20px;border-radius:11px;font-size:13px;font-weight:700;' +
      'background:rgba(42,38,32,.93);color:#FFF6E0';
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, 2200);
  }

  function queue(url, body) {
    var list = load();
    list.push({ url: url, body: body || '{}', t: Date.now() });
    save(list);
    toast('已离线记录，联网后自动上传');
  }

  var flushing = false;

  function flush() {
    if (flushing) return;
    var list = load();
    if (!list.length) { paint(); return; }
    flushing = true;
    var rest = [];
    var done = 0;
    list.forEach(function (item, i) {
      fetch(item.url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: item.body
      }).then(function (r) {
        if (!r || !r.ok) rest.push(item);
      }).catch(function () {
        rest.push(item);
      }).then(function () {
        done++;
        if (done === list.length) {
          save(rest);
          flushing = false;
          if (!rest.length) toast('离线成绩已全部上传');
        }
      });
    });
  }

  var origFetch = window.fetch;

  window.fetch = function (input, init) {
    var url = '';
    try { url = (typeof input === 'string') ? input : ((input && input.url) || ''); } catch (e) {}
    var p = origFetch.apply(this, arguments);
    if (!/\/api\/submit_\w+_score/.test(url)) return p;
    return p.catch(function (err) {
      var body = '';
      try { body = (init && typeof init.body === 'string') ? init.body : ''; } catch (e) {}
      if (!body) throw err;
      queue(url, body);
      return new Response(JSON.stringify({ ok: true, queued: true, msg: '已离线记录' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      });
    });
  };

  function mount() {
    if (document.getElementById('monoOfflineBadge')) return;
    badge = document.createElement('div');
    badge.id = 'monoOfflineBadge';
    badge.style.cssText = 'position:fixed;left:12px;bottom:74px;z-index:9998;display:none;' +
      'padding:6px 13px;border-radius:99px;font-size:12px;font-weight:700;cursor:pointer;' +
      'background:#FFF4D6;color:#7A5B00;border:1px solid #F0E0B0;box-shadow:0 2px 10px rgba(0,0,0,.12)';
    badge.onclick = function () { toast('正在上传…'); flush(); };
    document.body.appendChild(badge);
    paint();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }

  // 在线时把可离线游戏预热进缓存（6 小时最多一次，SW 内部会跳过已有的）
  function warm() {
    try {
      if (!navigator.onLine) return;
      if (!navigator.serviceWorker || !navigator.serviceWorker.controller) return;
      var last = +(localStorage.getItem('mono_warm_at') || 0);
      if (Date.now() - last < 6 * 3600 * 1000) return;
      navigator.serviceWorker.controller.postMessage({ type: 'warm_games' });
      localStorage.setItem('mono_warm_at', String(Date.now()));
    } catch (e) {}
  }

  window.addEventListener('online', flush);
  window.addEventListener('online', warm);
  setTimeout(warm, 4000);
  setTimeout(flush, 1500);

  window.MonoOffline = { queue: queue, flush: flush, count: count, paint: paint };
})();
