/* 快捷键总览：全局注册表 + ? 唤出浮层
 *
 * 站点里有十几款游戏，键盘操作原先各写各的（大部分干脆没有）。
 * 这里做一张按路由匹配的注册表，问号键随时能查。
 */
(function () {
  'use strict';
  if (!document.body || window.__monoShortcuts) return;
  window.__monoShortcuts = true;

  /* 通用键（全站都生效） */
  var GLOBAL = [
    { k: '?', d: '打开这个快捷键面板' },
    { k: 'Esc', d: '关闭浮层 / 取消选择' },
    { k: 'G 然后 H', d: '回到首页' },
    { k: 'G 然后 F', d: '去论坛' },
    { k: 'G 然后 C', d: '去聊天室' },
    { k: 'G 然后 M', d: '去消息中心' },
    { k: 'G 然后 S', d: '去设置' },
    { k: '/', d: '聚焦搜索框' }
  ];

  /* 按 pathname 匹配。p 是正则片段，k 是键名，d 是说明 */
  var PAGES = [
    { p: '/game/sudoku', name: '数独', keys: [
      { k: '↑↓←→', d: '移动选格' },
      { k: '1-9', d: '填入数字' }
    ]},
    { p: '/game/jump', name: '跳一跳', keys: [
      { k: '空格（按住）', d: '蓄力，按越久跳越远' }
    ]},
    { p: '/game/hanoi', name: '汉诺塔', keys: [
      { k: '1 / 2 / 3', d: '依次选择三根柱子' }
    ]},
    { p: '/game/color-blind', name: '色觉训练', keys: [
      { k: '空格 / Enter', d: '确认当前选择' }
    ]},
    { p: '/game/mine', name: '扫雷', keys: [
      { k: '↑↓←→', d: '移动光标' },
      { k: '空格', d: '翻开格子' },
      { k: 'F', d: '插旗标记' },
      { k: 'R', d: '重新开始' }
    ]},
    { p: '/game/klotski', name: '华容道', keys: [
      { k: '↑↓←→', d: '推动空格' },
      { k: 'R', d: '重新开始' }
    ]},
    { p: '/game/xianyi', name: '闲弈', keys: [
      { k: '↑↓←→', d: '移动光标' },
      { k: 'Enter', d: '落子' },
      { k: 'Esc', d: '取消选择' }
    ]},
    { p: '/post/', name: '帖子详情', keys: [
      { k: 'Esc', d: '离开评论框' }
    ]},
    { p: '/chat', name: '聊天室', keys: [
      { k: 'Enter', d: '发送消息' }
    ]}
  ];

  function matchPage() {
    var path = location.pathname;
    for (var i = 0; i < PAGES.length; i++) {
      if (path.indexOf(PAGES[i].p) >= 0) return PAGES[i];
    }
    return null;
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /* ---------- 浮层 ---------- */
  var box = null;
  function build() {
    var page = matchPage();
    var html = '<div class="sc-mask"></div><div class="sc-panel" role="dialog" aria-label="键盘快捷键">'
             + '<div class="sc-hd"><h3>键盘快捷键</h3><button type="button" class="sc-x" aria-label="关闭">×</button></div>';

    if (page) {
      html += '<div class="sc-sec"><div class="sc-sec-t">' + esc(page.name) + '</div><table>';
      for (var i = 0; i < page.keys.length; i++) {
        html += '<tr><td><kbd>' + esc(page.keys[i].k) + '</kbd></td><td>' + esc(page.keys[i].d) + '</td></tr>';
      }
      html += '</table></div>';
    } else {
      html += '<div class="sc-none">当前页面没有专属快捷键</div>';
    }

    html += '<div class="sc-sec"><div class="sc-sec-t">通用</div><table>';
    for (var j = 0; j < GLOBAL.length; j++) {
      html += '<tr><td><kbd>' + esc(GLOBAL[j].k) + '</kbd></td><td>' + esc(GLOBAL[j].d) + '</td></tr>';
    }
    html += '</table></div></div>';

    box = document.createElement('div');
    box.className = 'sc-wrap';
    box.innerHTML = html;
    document.body.appendChild(box);

    box.querySelector('.sc-x').addEventListener('click', hide);
    box.querySelector('.sc-mask').addEventListener('click', hide);
    box.addEventListener('click', function (e) { e.stopPropagation(); });
  }

  function show() {
    if (box) return;
    build();
    requestAnimationFrame(function () { box.classList.add('on'); });
  }

  function hide() {
    if (!box) return;
    var b = box;
    box = null;
    b.classList.remove('on');
    setTimeout(function () { if (b.parentNode) b.parentNode.removeChild(b); }, 180);
  }

  /* ---------- 输入态判断 ----------
   * 在输入框/可编辑区里绝不抢按键，否则用户打不出「?」 */
  function typing(el) {
    if (!el) return false;
    var tag = (el.tagName || '').toLowerCase();
    if (tag === 'input') {
      var t = (el.type || 'text').toLowerCase();
      return ['text', 'search', 'url', 'tel', 'password', 'email', 'number', ''].indexOf(t) >= 0;
    }
    return tag === 'textarea' || tag === 'select' || el.isContentEditable === true;
  }

  /* ---------- G 前缀序列 ---------- */
  var gArmed = 0;
  var seqActions = {
    h: function () { location.href = '/'; },
    f: function () { location.href = '/forum'; },
    c: function () { location.href = '/chat'; },
    m: function () { location.href = '/inbox'; },
    s: function () { location.href = '/settings'; }
  };

  /* ---------- 游戏内空格临时呼出 ---------- */
  // 棋盘/游戏页里空格本来可能被游戏占用，这里用 Shift+? 这个组合，
  // 免得和游戏逻辑打架（文档里写的「空格呼出」在联机对局里会误触悔棋）。

  document.addEventListener('keydown', function (e) {
    if (typing(e.target)) {
      if (e.key === 'Escape' && box) { hide(); }
      return;
    }

    // 问号（含 Shift+/ 与中文输入法下的 ？）唤出面板
    if (e.key === '?' || e.key === '？') {
      e.preventDefault();
      if (box) hide(); else show();
      return;
    }

    // Esc 关面板
    if (e.key === 'Escape' && box) {
      e.preventDefault();
      hide();
      return;
    }

    // G 序列
    var k = (e.key || '').toLowerCase();
    if (gArmed && seqActions[k]) {
      e.preventDefault();
      gArmed = 0;
      seqActions[k]();
      return;
    }
    if (k === 'g' && !e.ctrlKey && !e.metaKey && !e.altKey) {
      gArmed = setTimeout(function () { gArmed = 0; }, 900);
      return;
    }
    if (gArmed && k !== 'g') { gArmed = 0; }

    // 斜杠聚焦搜索
    if (k === '/' && !e.ctrlKey && !e.metaKey) {
      var s = document.querySelector('input[name="q"], input[type="search"], #searchInput, .nav-search input');
      if (s) {
        e.preventDefault();
        s.focus();
        s.select && s.select();
      }
    }
  }, true);

  /* 提供给别的脚本调 */
  window.monoShortcuts = { show: show, hide: hide, registry: { global: GLOBAL, pages: PAGES } };

  /* ---------- 样式 ---------- */
  var st = document.createElement('style');
  st.textContent =
    '.sc-wrap{position:fixed;inset:0;z-index:1000000;display:flex;align-items:center;' +
      'justify-content:center;opacity:0;transition:opacity .18s ease;}' +
    '.sc-wrap.on{opacity:1;}' +
    '.sc-mask{position:absolute;inset:0;background:rgba(0,0,0,.45);}' +
    '.sc-panel{position:relative;background:var(--card,#fff);color:var(--fg,#111);' +
      'border:1px solid var(--line,#e5e5e5);border-radius:16px;padding:0;' +
      'max-width:440px;width:calc(100vw - 32px);max-height:78vh;overflow:auto;' +
      'box-shadow:0 20px 60px rgba(0,0,0,.3);}' +
    '.sc-hd{display:flex;align-items:center;justify-content:space-between;' +
      'padding:16px 18px;border-bottom:1px solid var(--line,#e5e5e5);position:sticky;top:0;' +
      'background:var(--card,#fff);}' +
    '.sc-hd h3{margin:0;font-size:16px;font-weight:700;}' +
    '.sc-x{border:none;background:transparent;font-size:22px;line-height:1;cursor:pointer;' +
      'color:var(--muted,#888);padding:0 4px;}' +
    '.sc-x:hover{color:var(--fg,#111);}' +
    '.sc-sec{padding:14px 18px 6px;}' +
    '.sc-sec-t{font-size:12px;font-weight:700;color:var(--muted,#888);' +
      'letter-spacing:.06em;margin-bottom:8px;}' +
    '.sc-sec table{width:100%;border-collapse:collapse;font-size:13px;}' +
    '.sc-sec td{padding:5px 0;vertical-align:top;}' +
    '.sc-sec td:first-child{width:112px;padding-right:12px;}' +
    '.sc-sec kbd{display:inline-block;padding:2px 7px;border:1px solid var(--line,#ddd);' +
      'border-bottom-width:2px;border-radius:6px;background:var(--soft,#f5f5f5);' +
      'font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;' +
      'color:var(--fg,#111);white-space:nowrap;}' +
    '.sc-none{padding:20px 18px;color:var(--muted,#888);font-size:13px;text-align:center;}';
    '.foot-subs a,.foot-subs .foot-keys{margin-left:6px;}' +
    '.foot-subs .foot-keys{border:none;background:transparent;cursor:pointer;' +
      'color:inherit;font-size:inherit;padding:0 2px;line-height:1;opacity:.75;}' +
    '.foot-subs .foot-keys:hover{opacity:1;text-decoration:underline;}'
  document.head.appendChild(st);
})();
