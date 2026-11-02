(function () {
  var KEY = 'mono_fx';
  var CATS = ['click', 'page', 'ko'];
  var defaults = { click: true, page: true, ko: true };
  var state = null;

  function load() {
    var s = {};
    try {
      var raw = localStorage.getItem(KEY);
      if (raw) {
        var d = JSON.parse(raw);
        for (var i = 0; i < CATS.length; i++) {
          var k = CATS[i];
          s[k] = (typeof d[k] === 'boolean') ? d[k] : defaults[k];
        }
      } else {
        s = JSON.parse(JSON.stringify(defaults));
      }
    } catch (e) {
      s = JSON.parse(JSON.stringify(defaults));
    }
    state = s;
    return s;
  }

  function save() {
    try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {}
  }

  function apply() {
    if (!state) load();
    var b = document.body;
    if (!b) return;
    var all = state.click && state.page && state.ko;
    b.classList.toggle('fx-off', all === false && state.click === false && state.page === false && state.ko === false);
    b.classList.toggle('fx-no-click', !state.click);
    b.classList.toggle('fx-no-page', !state.page);
    b.classList.toggle('fx-no-ko', !state.ko);
  }

  function reduced() {
    try {
      return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    } catch (e) { return false; }
  }

  function vibrate(pat) {
    if (reduced()) return;
    try { if (navigator.vibrate) navigator.vibrate(pat); } catch (e) {}
  }

  function ripple(el, ev) {
    if (!state || !state.click || reduced()) return;
    if (!el || !el.classList) return;
    try {
      var r = el.getBoundingClientRect();
      var size = Math.max(r.width, r.height);
      var x = (ev && ev.clientX ? ev.clientX - r.left : r.width / 2);
      var y = (ev && ev.clientY ? ev.clientY - r.top : r.height / 2);
      if (!el.classList.contains('fx-ripple-host')) el.classList.add('fx-ripple-host');
      var s = document.createElement('span');
      s.className = 'fx-ripple';
      s.style.width = s.style.height = size + 'px';
      s.style.left = (x - size / 2) + 'px';
      s.style.top = (y - size / 2) + 'px';
      el.appendChild(s);
      setTimeout(function () { if (s.parentNode) s.parentNode.removeChild(s); }, 600);
    } catch (e) {}
  }

  function particles(host, cx, cy, count) {
    if (reduced()) return;
    count = count || 26;
    for (var i = 0; i < count; i++) {
      (function (i) {
        var p = document.createElement('span');
        p.className = 'fx-ko-particle';
        var ang = (Math.PI * 2 * i) / count + (Math.random() - 0.5) * 0.5;
        var dist = 90 + Math.random() * 190;
        p.style.left = cx + 'px';
        p.style.top = cy + 'px';
        p.style.setProperty('--dx', (Math.cos(ang) * dist).toFixed(1) + 'px');
        p.style.setProperty('--dy', (Math.sin(ang) * dist).toFixed(1) + 'px');
        var dur = (0.7 + Math.random() * 0.6).toFixed(2);
        p.style.animation = 'fxKoP ' + dur + 's cubic-bezier(.2,.7,.4,1) forwards';
        p.style.animationDelay = (Math.random() * 0.12).toFixed(2) + 's';
        if (i % 3 === 0) p.style.background = '#fff3d4';
        host.appendChild(p);
        setTimeout(function () { if (p.parentNode) p.parentNode.removeChild(p); }, 1600);
      })(i);
    }
  }

  function checkmate(opts) {
    opts = opts || {};
    if (!state) load();
    if (!state.ko || reduced()) {
      vibrate(60);
      return;
    }
    var board = opts.board || document.querySelector('.xq-board-wrap') || document.querySelector('canvas');
    var cell = opts.cell;
    var word = opts.word || '绝杀';

    if (board) board.classList.add('xq-ko');
    var target = document.body;
    target.classList.add('xq-ko-shake');
    setTimeout(function () { target.classList.remove('xq-ko-shake'); }, 700);

    var flash = document.createElement('div');
    flash.className = 'fx-ko-flash';
    document.body.appendChild(flash);
    setTimeout(function () { if (flash.parentNode) flash.parentNode.removeChild(flash); }, 900);

    var vig = document.createElement('div');
    vig.className = 'fx-ko-vignette';
    document.body.appendChild(vig);
    setTimeout(function () { if (vig.parentNode) vig.parentNode.removeChild(vig); }, 2300);

    var host = document.body;
    var cx = window.innerWidth / 2;
    var cy = window.innerHeight / 2;
    if (cell && cell.getBoundingClientRect) {
      var r = cell.getBoundingClientRect();
      cx = r.left + r.width / 2;
      cy = r.top + r.height / 2;
      cell.classList.add('fx-ko-target');
      setTimeout(function () { cell.classList.remove('fx-ko-target'); }, 3200);
    }

    var ringHost = document.createElement('div');
    ringHost.style.position = 'fixed';
    ringHost.style.left = '0';
    ringHost.style.top = '0';
    ringHost.style.width = '100%';
    ringHost.style.height = '100%';
    ringHost.style.pointerEvents = 'none';
    ringHost.style.zIndex = '60';
    document.body.appendChild(ringHost);

    [0, 1].forEach(function (i) {
      var ring = document.createElement('div');
      ring.className = 'fx-ko-ring' + (i ? ' d2' : '');
      ring.style.left = (cx - 30) + 'px';
      ring.style.top = (cy - 30) + 'px';
      ring.style.width = '60px';
      ring.style.height = '60px';
      ringHost.appendChild(ring);
    });

    particles(host, cx, cy, 30);

    var w = document.createElement('div');
    w.className = 'fx-ko-word';
    w.textContent = word;
    try { w.setAttribute('data-n', String(word.length)); } catch (e) {}
    document.body.appendChild(w);
    setTimeout(function () { if (w.parentNode) w.parentNode.removeChild(w); }, 1900);

    setTimeout(function () {
      if (board) board.classList.remove('xq-ko');
      if (ringHost.parentNode) ringHost.parentNode.removeChild(ringHost);
    }, 2400);

    vibrate([0, 40, 60, 120, 60, 220]);
  }

  function check(el) {
    if (!state) load();
    vibrate(45);
    if (!state.ko || reduced() || !el || !el.classList) return;
    el.classList.add('fx-check-flash');
    setTimeout(function () { el.classList.remove('fx-check-flash'); }, 1100);
  }

  function count(el, to, dur) {
    if (!el) return;
    if (!state) load();
    if (!state.click || reduced()) { el.textContent = String(to); return; }
    var from = parseInt(String(el.textContent).replace(/[^\d-]/g, ''), 10);
    if (isNaN(from)) from = 0;
    dur = dur || 600;
    var t0 = performance.now();
    function step(t) {
      var k = Math.min(1, (t - t0) / dur);
      var e = 1 - Math.pow(1 - k, 3);
      el.textContent = String(Math.round(from + (to - from) * e));
      if (k < 1) requestAnimationFrame(step);
    }
    el.classList.add('fx-count');
    requestAnimationFrame(step);
  }

  function toast(el) {
    if (!el || !el.classList) return;
    el.classList.add('fx-toast');
  }

  function page(sel) {
    if (!state) load();
    if (!state.page || reduced()) return;
    var els = document.querySelectorAll(sel || '.fx-page');
    for (var i = 0; i < els.length; i++) els[i].classList.add('fx-page');
  }

  load();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', apply);
  } else {
    apply();
  }

  document.addEventListener('pointerdown', function (e) {
    if (!state) load();
    if (!state.click) return;
    var t = e.target;
    if (!t || !t.closest) return;
    var b = t.closest('.btn, button.btn, .fx-press, .fx-ripple');
    if (b) ripple(b, e);
  }, true);

  window.MonoFX = {
    CATS: CATS,
    state: function () { if (!state) load(); return state; },
    get: function (k) { if (!state) load(); return !!state[k]; },
    set: function (k, v) {
      if (!state) load();
      if (CATS.indexOf(k) < 0) return;
      state[k] = !!v;
      save(); apply();
      try {
        window.dispatchEvent(new CustomEvent('monofx:change', { detail: state }));
      } catch (e) {}
    },
    setAll: function (v) {
      if (!state) load();
      for (var i = 0; i < CATS.length; i++) state[CATS[i]] = !!v;
      save(); apply();
      try {
        window.dispatchEvent(new CustomEvent('monofx:change', { detail: state }));
      } catch (e) {}
    },
    apply: apply,
    ripple: ripple,
    checkmate: checkmate,
    check: check,
    count: count,
    toast: toast,
    page: page,
    vibrate: vibrate,
    reduced: reduced
  };
})();
