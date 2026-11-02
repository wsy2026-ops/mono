/* 024 签到提醒 + 025 成就曝光
 *
 * 024：本地时间 20:00 若今日未签到，推一条浏览器通知。
 *      授权只问一次，之后用 localStorage 记住选择。
 * 025：首页顶部横向成就条，展示「快领的 / 进行中 / 已获得」。
 */
(function () {
  'use strict';
  if (!document.body || window.__monoRemind) return;
  window.__monoRemind = true;

  var LS = {
    perm: 'mono_notify_perm',      // granted / denied / dismissed
    remindAt: 'mono_remind_at',     // 上次提醒的日期戳
    quietFrom: 'mono_quiet_from',   // 免打扰起始小时
    quietTo: 'mono_quiet_to',       // 免打扰结束小时
    remindOn: 'mono_remind_on'      // 总开关
  };

  function ls(k, dft) {
    try { var v = localStorage.getItem(k); return v === null ? dft : v; } catch (e) { return dft; }
  }
  function set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }

  function todayStr() {
    var d = new Date();
    return d.getFullYear() + '-' + (d.getMonth() + 1) + '-' + d.getDate();
  }

  /* ---------------- 024 签到提醒 ---------------- */

  function supported() {
    return typeof Notification !== 'undefined';
  }

  function inQuiet(nowHour) {
    if (ls(LS.remindOn, '1') !== '1') return true;      // 关了就等于免打扰
    var f = parseInt(ls(LS.quietFrom, '0'), 10) || 0;
    var t = parseInt(ls(LS.quietTo, '0'), 10) || 0;
    if (f === t) return false;
    return f < t ? (nowHour >= f && nowHour < t)
                 : (nowHour >= f || nowHour < t);
  }

  function signedToday() {
    // 页面里已渲染了签到态就直接读，省一次请求
    var el = document.querySelector('[data-sign-done]');
    return el && el.getAttribute('data-sign-done') === '1';
  }

  function fire() {
    if (ls(LS.remindAt, '') === todayStr()) return;   // 今天提醒过了
    if (signedToday()) return;
    if (inQuiet(new Date().getHours())) { set(LS.remindAt, todayStr()); return; }
    try {
      var n = new Notification('今天还没签到', {
        body: '积分白白流失了，花 5 秒补上。',
        tag: 'mono-sign',
        icon: '/static/icon.svg',
        requireInteraction: false
      });
      n.onclick = function () { window.focus(); location.href = '/points'; n.close(); };
      set(LS.remindAt, todayStr());
    } catch (e) {}
  }

  function schedule() {
    // 每 5 分钟看一眼，到点了就提醒。不用 setTimeout 掐到 20:00 ——
    // 笔记本合盖、休眠、切标签都会让长延时不可靠，轮询最稳。
    setInterval(function () {
      var h = new Date().getHours();
      if (h === 20) fire();
    }, 5 * 60 * 1000);
    var h0 = new Date().getHours();
    if (h0 === 20) fire();
  }

  /* 请求授权：只弹一次，用户拒绝后不再打扰 */
  function askPermission() {
    if (!supported()) return;
    var st = ls(LS.perm, '');
    if (st === 'granted') { schedule(); return; }
    if (st === 'denied' || st === 'dismissed') return;

    var wrap = document.createElement('div');
    wrap.className = 'nd-wrap';
    wrap.innerHTML =
      '<div class="nd-card">'
      + '<b>要开签到提醒吗？</b>'
      + '<p>每天 20:00 如果还没签到，浏览器会弹一条提醒。不想要就关掉，随时能在设置里改。</p>'
      + '<div class="nd-acts">'
      + '<button type="button" class="nd-no">不用了</button>'
      + '<button type="button" class="nd-yes">开启</button>'
      + '</div></div>';
    document.body.appendChild(wrap);

    function close() { if (wrap.parentNode) wrap.parentNode.removeChild(wrap); }

    wrap.querySelector('.nd-no').addEventListener('click', function () {
      set(LS.perm, 'dismissed'); close();
    });
    wrap.querySelector('.nd-yes').addEventListener('click', function () {
      Notification.requestPermission().then(function (p) {
        set(LS.perm, p === 'granted' ? 'granted' : 'denied');
        if (p === 'granted') schedule();
        close();
      }).catch(function () { set(LS.perm, 'dismissed'); close(); });
    });
    wrap.querySelector('.nd-no').focus();
  }

  var st = document.createElement('style');
  st.textContent =
    '.nd-wrap{position:fixed;inset:0;z-index:1000001;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.45);}'
    + '.nd-card{background:var(--card,#fff);color:var(--fg,#111);border:1px solid var(--line,#e5e5e5);'
      'border-radius:14px;padding:20px 22px;max-width:340px;box-shadow:0 16px 48px rgba(0,0,0,.28);}'
    + '.nd-card b{font-size:15px;display:block;margin-bottom:8px;}'
    + '.nd-card p{margin:0 0 16px;font-size:13px;line-height:1.7;color:var(--muted,#888);}'
    + '.nd-acts{display:flex;gap:10px;justify-content:flex-end;}'
    + '.nd-acts button{padding:7px 16px;border-radius:9px;font-size:13px;cursor:pointer;border:1px solid var(--line,#ddd);background:transparent;color:inherit;}'
    + '.nd-yes{background:var(--fg,#111)!important;color:var(--bg,#fff)!important;border-color:var(--fg,#111)!important;}';
  document.head.appendChild(st);

  /* ---------------- 025 成就条 ---------------- */

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function mountAchBar() {
    var host = document.querySelector('[data-ach-bar]');
    if (!host) return;

    fetch('/api/achievements?limit=4', { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (!j || !j.items || !j.items.length) { host.remove(); return; }

        var pct = j.total ? Math.round(j.done * 100 / j.total) : 0;
        var html = '<a class="ach-hd" href="/points?tab=tasks">'
          + '<b>成就</b>'
          + '<span class="ach-num">' + j.done + '/' + j.total + '</span>'
          + '<span class="ach-pct">' + pct + '%</span>'
          + (j.ready ? '<span class="ach-ready">可领 ' + j.ready + '</span>' : '')
          + (j.hidden_locked ? '<span class="ach-hidden-tip">还有 ' + j.hidden_locked + ' 个隐藏成就</span>' : '')
          + '<a class="ach-lb-link" href="/leaderboard">排行榜 →</a>'
          + '</a>'
          + '<div class="ach-strip">';

        for (var i = 0; i < j.items.length; i++) {
          var a = j.items[i];
          var cls = a.claimed ? 'ok' : (a.ready ? 'ready' : 'doing');
          html += '<a class="ach-c ' + cls + '" href="/points?tab=tasks" title="'
               + esc(a.name + ' · ' + a.desc + '（' + a.progress + '/' + a.target
                     + '，奖励 ' + a.reward + ' 积分）') + '">'
               + '<span class="ach-ic">' + esc(a.icon) + '</span>'
               + '<span class="ach-nm">' + esc(a.name) + '</span>'
               + '<span class="ach-bar-wrap"><i style="width:' + a.pct + '%"></i></span>'
               + '<span class="ach-pg">' + a.progress + '/' + a.target + '</span>'
               + (a.claimed ? '<span class="ach-tag">已达成</span>'
                            : (a.ready ? '<span class="ach-tag go">可领取</span>' : ''))
               + '</a>';
        }
        html += '</div>';
        host.innerHTML = html;
        host.classList.add('on');
      })
      .catch(function () {});
  }

  /* 个人主页的完整勋章墙：limit=0 拿全部 */
  function mountAchWall() {
    var host = document.querySelector('[data-ach-wall]');
    if (!host) return;
    fetch('/api/achievements?limit=0', { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (!j || !j.all || !j.all.length) { host.remove(); return; }
        var html = '<div class="ach-wall-hd"><b>' + j.done + '</b> / ' + j.total
                 + (j.hidden_locked ? '　·　' + j.hidden_locked + ' 个隐藏未解锁' : '') + '</div><div class="ach-wall">';
        for (var i = 0; i < j.all.length; i++) {
          var a = j.all[i];
          var cls = a.claimed ? 'ok' : (a.ready ? 'ready' : 'doing');
          html += '<div class="ach-w ' + cls + '">'
               + '<div class="ach-w-ic">' + esc(a.icon) + '</div>'
               + '<div class="ach-w-b"><b>' + esc(a.name) + '</b>'
               + '<span>' + esc(a.desc) + '</span>'
               + '<span class="ach-w-p">' + a.progress + '/' + a.target + ' · +' + a.reward + '</span></div></div>';
        }
        html += '</div>';
        host.innerHTML = html;
        host.classList.add('on');
      })
      .catch(function () {});
  }

  var s2 = document.createElement('style');
  s2.textContent =
    '.ach-bar{display:none;margin:0 0 22px;border:1px solid var(--line,#e6e6e6);border-radius:14px;'
      + 'background:var(--card,#fff);overflow:hidden;}'
    + '.ach-bar.on{display:block;}'
    + '.ach-hd{display:flex;align-items:center;gap:9px;padding:11px 14px 9px;font-size:14px;'
      + 'color:inherit;text-decoration:none;flex-wrap:wrap;}'
    + '.ach-num,.ach-pct{font-size:12px;color:var(--muted,#8a8a8a);font-variant-numeric:tabular-nums;}'
    + '.ach-ready{font-size:11px;padding:1px 7px;border-radius:9px;background:var(--fg,#111);color:var(--bg,#fff);}'
    + '.ach-hidden-tip{font-size:11px;color:var(--muted,#8a8a8a);}'
    + '.ach-lb-link{font-size:11.5px;color:var(--muted,#8a8a8a);text-decoration:none;margin-left:auto;white-space:nowrap;}'
    + '.ach-lb-link:hover{color:var(--fg,#111);}'
    + '.ach-strip{display:flex;gap:10px;overflow-x:auto;padding:0 14px 13px;scrollbar-width:thin;}'
    + '.ach-c{flex:1 1 0;min-width:0;display:flex;flex-direction:column;align-items:center;gap:4px;'
      + 'padding:11px 6px;border:1px solid var(--line,#e6e6e6);border-radius:11px;'
      + 'text-decoration:none;color:inherit;transition:border-color .15s;}'
    + '.ach-c:hover{border-color:var(--fg,#111);}'
    + '.ach-c .ach-ic{font-size:19px;line-height:1;filter:grayscale(1);opacity:.5;}'
    + '.ach-c .ach-nm{font-size:12px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%;}'
    + '.ach-bar-wrap{display:block;width:100%;max-width:70px;height:3px;border-radius:2px;background:var(--soft,#f0f0f0);overflow:hidden;}'
    + '.ach-bar-wrap i{display:block;height:100%;background:var(--fg,#111);}'
    + '.ach-c .ach-pg{font-size:10.5px;color:var(--muted,#8a8a8a);font-variant-numeric:tabular-nums;}'
    + '.ach-c .ach-tag{font-size:10px;padding:1px 5px;border-radius:5px;background:var(--soft,#f5f5f5);color:var(--muted,#8a8a8a);}'
    + '.ach-c.ready{border-color:var(--fg,#111);border-width:2px;padding:10px 5px;}'
    + '.ach-c.ready .ach-ic{filter:none;opacity:1;}'
    + '.ach-c.ready .ach-tag.go{background:var(--fg,#111);color:var(--bg,#fff);}'
    + '.ach-c.ok{background:var(--soft,#f5f5f5);}'
    + '.ach-c.ok .ach-ic{filter:none;opacity:1;}'
    + '.ach-c.ok .ach-tag{background:var(--fg,#111);color:var(--bg,#fff);}'
    + '.ach-wall{display:none;margin:0 0 20px;}'
    + '.ach-wall.on{display:block;}'
    + '.ach-wall-hd{font-size:13px;color:var(--muted,#8a8a8a);margin-bottom:10px;font-variant-numeric:tabular-nums;}'
    + '.ach-wall-hd b{color:var(--fg,#111);font-size:16px;}'
    + '.ach-wall{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:9px;}'
    + '.ach-w{display:flex;gap:10px;align-items:flex-start;padding:10px 12px;'
      + 'border:1px solid var(--line,#e6e6e6);border-radius:11px;}'
    + '.ach-w.ready{border-color:var(--fg,#111);border-width:2px;padding:9px 11px;}'
    + '.ach-w.ok{background:var(--soft,#f5f5f5);}'
    + '.ach-w-ic{font-size:20px;line-height:1.2;filter:grayscale(1);opacity:.5;}'
    + '.ach-w.ready .ach-w-ic,.ach-w.ok .ach-w-ic{filter:none;opacity:1;}'
    + '.ach-w-b{display:flex;flex-direction:column;gap:1px;min-width:0;}'
    + '.ach-w-b b{font-size:13px;}'
    + '.ach-w-b span{font-size:11.5px;color:var(--muted,#8a8a8a);line-height:1.5;}'
    + '.ach-w-b .ach-w-p{font-variant-numeric:tabular-nums;}';
  document.head.appendChild(s2);

  /* ---------------- 启动 ---------------- */

  if (document.querySelector('[data-ach-bar]')) mountAchBar();
  if (document.querySelector('[data-ach-wall]')) mountAchWall();

  if (document.body.getAttribute('data-signed') !== null) {
    // 积分页当天签到状态由服务端渲染
    setTimeout(askPermission, 1500);
  } else {
    schedule();
  }

  /* 设置页改开关 */
  var btn = document.getElementById('remindToggle');
  if (btn) {
    btn.addEventListener('click', function () {
      var on = ls(LS.remindOn, '1') === '1';
      set(LS.remindOn, on ? '0' : '1');
      btn.textContent = on ? '签到提醒：关' : '签到提醒：开';
      if (!on) askPermission();
    });
    btn.textContent = ls(LS.remindOn, '1') === '1' ? '签到提醒：开' : '签到提醒：关';
  }
})();