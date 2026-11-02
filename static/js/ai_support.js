/* ai_support.js — 053 AI 客服悬浮窗（自注入 DOM，全站生效，零模板改动）
 *
 * 用法：在 base.html 里引一行即可。登录页/注册页自动跳过。
 * 后端：POST /api/ai/support（SSE 流式，HK 代理 LA 的 Hunyuan-0.5B）
 */
(function () {
  'use strict';

  /* 登录/注册页不显示（避免干扰注册流程） */
  var p = location.pathname;
  if (p === '/login' || p === '/register' || p.indexOf('/admin') === 0) return;
  if (window.__monoSupportInit) return;
  window.__monoSupportInit = true;

  var CSS = ''
    + '.msup-ball{position:fixed;right:22px;bottom:22px;width:56px;height:56px;border-radius:50%;'
    + 'background:linear-gradient(135deg,#5b8cff,#8b5cf6);color:#fff;border:none;cursor:pointer;'
    + 'box-shadow:0 6px 20px rgba(91,140,255,.42);z-index:9998;display:flex;align-items:center;'
    + 'justify-content:center;transition:transform .22s cubic-bezier(.34,1.56,.64,1),box-shadow .22s;}'
    + '.msup-ball:hover{transform:scale(1.08);box-shadow:0 8px 26px rgba(91,140,255,.55);}'
    + '.msup-ball:active{transform:scale(.96);}'
    + '.msup-ball svg{transition:transform .25s,opacity .2s;}'
    + '.msup-ball.open svg.i-chat{transform:rotate(90deg) scale(.6);opacity:0;}'
    + '.msup-ball svg.i-close{position:absolute;transform:rotate(-90deg) scale(.6);opacity:0;}'
    + '.msup-ball.open svg.i-close{transform:none;opacity:1;}'
    + '.msup-dot{position:absolute;top:2px;right:2px;width:11px;height:11px;border-radius:50%;'
    + 'background:#ff4d4f;border:2px solid #fff;display:none;}'
    + '.msup-dot.show{display:block;}'
    + '.msup-panel{position:fixed;right:22px;bottom:88px;width:376px;max-width:calc(100vw - 32px);'
    + 'height:min(560px,calc(100vh - 120px));background:var(--card,#fff);border-radius:16px;'
    + 'box-shadow:0 16px 48px rgba(0,0,0,.22);z-index:9999;display:flex;flex-direction:column;'
    + 'overflow:hidden;opacity:0;transform:translateY(12px) scale(.97);pointer-events:none;'
    + 'transition:opacity .22s,transform .26s cubic-bezier(.22,1,.36,1);}'
    + '.msup-panel.show{opacity:1;transform:none;pointer-events:auto;}'
    + '.msup-head{background:linear-gradient(135deg,#5b8cff,#8b5cf6);color:#fff;padding:14px 16px;'
    + 'display:flex;align-items:center;gap:10px;flex:none;}'
    + '.msup-avatar{width:34px;height:34px;border-radius:50%;background:rgba(255,255,255,.22);'
    + 'display:flex;align-items:center;justify-content:center;font-size:17px;flex:none;}'
    + '.msup-title{font-weight:700;font-size:14.5px;line-height:1.2;}'
    + '.msup-sub{font-size:11.5px;opacity:.85;margin-top:2px;}'
    + '.msup-x{margin-left:auto;background:none;border:none;color:#fff;opacity:.8;cursor:pointer;'
    + 'font-size:20px;line-height:1;padding:4px;}'
    + '.msup-x:hover{opacity:1;}'
    + '.msup-body{flex:1;overflow-y:auto;padding:14px 14px 6px;background:var(--bg,#f7f8fb);'
    + 'display:flex;flex-direction:column;gap:10px;}'
    + '.msup-msg{max-width:86%;padding:9px 12px;border-radius:12px;font-size:13.5px;line-height:1.55;'
    + 'white-space:pre-wrap;word-break:break-word;animation:msupIn .22s ease;}'
    + '@keyframes msupIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}'
    + '.msup-msg.ai{background:var(--card,#fff);color:var(--fg,#222);align-self:flex-start;'
    + 'border-bottom-left-radius:4px;box-shadow:0 1px 3px rgba(0,0,0,.07);}'
    + '.msup-msg.me{background:linear-gradient(135deg,#5b8cff,#7c6cf6);color:#fff;align-self:flex-end;'
    + 'border-bottom-right-radius:4px;}'
    + '.msup-msg.sys{align-self:center;background:transparent;color:var(--muted,#888);font-size:12px;'
    + 'padding:2px 8px;text-align:center;max-width:100%;}'
    + '.msup-quick{display:flex;flex-wrap:wrap;gap:7px;padding:0 2px 6px;}'
    + '.msup-chip{font-size:12.5px;padding:6px 11px;border-radius:999px;border:1px solid var(--line,#e4e6eb);'
    + 'background:var(--card,#fff);color:var(--fg,#333);cursor:pointer;transition:all .15s;}'
    + '.msup-chip:hover{border-color:#5b8cff;color:#5b8cff;background:rgba(91,140,255,.06);}'
    + '.msup-typing{display:inline-flex;gap:4px;padding:4px 2px;}'
    + '.msup-typing i{width:6px;height:6px;border-radius:50%;background:#b9c2d0;animation:msupB 1.2s infinite;}'
    + '.msup-typing i:nth-child(2){animation-delay:.15s;}'
    + '.msup-typing i:nth-child(3){animation-delay:.3s;}'
    + '@keyframes msupB{0%,60%,100%{transform:translateY(0);opacity:.5}30%{transform:translateY(-4px);opacity:1}}'
    + '.msup-foot{flex:none;border-top:1px solid var(--line,#e8e8e8);padding:10px;display:flex;gap:8px;'
    + 'align-items:flex-end;background:var(--card,#fff);}'
    + '.msup-in{flex:1;resize:none;border:1.5px solid var(--line,#e4e6eb);border-radius:12px;'
    + 'padding:9px 12px;font-size:13.5px;font-family:inherit;line-height:1.45;max-height:96px;'
    + 'outline:none;background:var(--bg,#fff);color:var(--fg,#222);transition:border-color .15s;}'
    + '.msup-in:focus{border-color:#5b8cff;}'
    + '.msup-send{width:38px;height:38px;border-radius:50%;border:none;cursor:pointer;flex:none;'
    + 'background:linear-gradient(135deg,#5b8cff,#8b5cf6);color:#fff;display:flex;align-items:center;'
    + 'justify-content:center;transition:opacity .15s,transform .15s;}'
    + '.msup-send:disabled{opacity:.4;cursor:not-allowed;}'
    + '.msup-send:not(:disabled):hover{transform:scale(1.06);}'
    + '.msup-act{display:flex;flex-wrap:wrap;gap:7px;padding:2px 0 4px;}'
    + '.msup-act-btn{font-size:12.5px;padding:7px 13px;border-radius:9px;border:none;cursor:pointer;'
    + 'background:linear-gradient(135deg,#5b8cff,#8b5cf6);color:#fff;font-weight:600;'
    + 'box-shadow:0 2px 8px rgba(91,140,255,.3);transition:transform .15s;}'
    + '.msup-act-btn:hover{transform:translateY(-1px);}'
    + '.msup-act-btn.ghost{background:var(--card,#fff);color:#5b8cff;border:1px solid #5b8cff;box-shadow:none;}'
    + '.msup-post{display:block;font-size:13px;padding:7px 10px;border-radius:8px;margin-bottom:5px;'
    + 'background:var(--bg,#f4f6fb);color:var(--fg,#222);text-decoration:none;border-left:3px solid #5b8cff;}'
    + '.msup-post:hover{background:rgba(91,140,255,.1);}'
    + '.msup-player{display:none;align-items:center;gap:8px;padding:8px 12px;flex:none;'
    + 'background:linear-gradient(135deg,rgba(91,140,255,.1),rgba(139,92,246,.1));'
    + 'border-top:1px solid var(--line,#e8e8e8);font-size:12.5px;}'
    + '.msup-player.show{display:flex;}'
    + '.msup-player .pp{width:30px;height:30px;border-radius:50%;border:none;cursor:pointer;flex:none;'
    + 'background:linear-gradient(135deg,#5b8cff,#8b5cf6);color:#fff;display:flex;'
    + 'align-items:center;justify-content:center;font-size:12px;}'
    + '.msup-player .pt{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600;}'
    + '.msup-tip{position:fixed;right:22px;bottom:88px;max-width:220px;padding:9px 13px;'
    + 'background:var(--card,#fff);color:var(--fg,#222);border-radius:12px;font-size:13px;'
    + 'line-height:1.4;box-shadow:0 6px 22px rgba(0,0,0,.16);z-index:9997;cursor:pointer;'
    + 'opacity:0;transform:translateY(6px);transition:opacity .3s,transform .3s;pointer-events:none;}'
    + '.msup-tip.show{opacity:1;transform:none;pointer-events:auto;}'
    + '.msup-tip::after{content:"";position:absolute;right:26px;bottom:-6px;width:12px;height:12px;'
    + 'background:var(--card,#fff);transform:rotate(45deg);box-shadow:2px 2px 4px rgba(0,0,0,.06);}'
    + '.msup-tip b{color:#5b8cff;}'
    + '.msup-tip .x{float:right;margin:-4px -6px 0 8px;opacity:.5;font-size:14px;}'
    + '@media (max-width:520px){.msup-tip{right:16px;bottom:80px;max-width:200px;}}'
    + '@media (max-width:520px){'
    + '.msup-panel{right:8px;left:8px;bottom:84px;width:auto;max-width:none;'
    + 'height:calc(100vh - 100px);height:calc(100dvh - 100px);}'
    + '.msup-ball{right:16px;bottom:16px;width:52px;height:52px;}'
    + '}'
    + '@media (prefers-reduced-motion:reduce){.msup-panel,.msup-msg,.msup-ball{transition:none;animation:none}}';

  var ICON_CHAT = '<svg class="i-chat" width="26" height="26" viewBox="0 0 24 24" fill="none" '
    + 'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    + '<path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>'
    + '</svg>';
  var ICON_CLOSE = '<svg class="i-close" width="22" height="22" viewBox="0 0 24 24" fill="none" '
    + 'stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>';
  var ICON_SEND = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    + 'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/></svg>';

  var QUICKS = ['怎么赚积分？', '有没有《琵琶行》这首歌？', '有没有人讨论快排',
                '打开象棋', '我的积分有多少', '你是什么模型？'];

  var history = [];   // [{role, content}]
  var busy = false;
  var opened = false;

  function inject() {
    var style = document.createElement('style');
    style.textContent = CSS;
    document.head.appendChild(style);

    var ball = document.createElement('button');
    ball.className = 'msup-ball';
    ball.setAttribute('aria-label', 'AI 客服');
    ball.innerHTML = ICON_CHAT + ICON_CLOSE + '<span class="msup-dot" id="msupDot"></span>';
    document.body.appendChild(ball);

    var panel = document.createElement('div');
    panel.className = 'msup-panel';
    panel.innerHTML = ''
      + '<div class="msup-head">'
      + '  <div class="msup-avatar">🤖</div>'
      + '  <div><div class="msup-title">小柒</div>'
      + '  <div class="msup-sub">AI 客服 · 本地模型 · 随时问</div></div>'
      + '  <button class="msup-x" aria-label="关闭">×</button>'
      + '</div>'
      + '<div class="msup-body" id="msupBody"></div>'
      + '<div class="msup-player" id="msupPlayer">'
      + '  <button class="pp" id="msupPP">❚❚</button>'
      + '  <span class="pt" id="msupPT"></span>'
      + '</div>'
      + '<div class="msup-foot">'
      + '  <textarea class="msup-in" id="msupIn" rows="1" placeholder="问我社区的任何问题…" maxlength="500"></textarea>'
      + '  <button class="msup-send" id="msupSend" aria-label="发送">' + ICON_SEND + '</button>'
      + '</div>';
    document.body.appendChild(panel);

    /* 053：引导气泡——「有不懂的？问小柒」，指着悬浮球 */
    var tip = document.createElement('div');
    tip.className = 'msup-tip';
    tip.innerHTML = '<span class="x">×</span>有不懂的？<b>问小柒</b>';
    document.body.appendChild(tip);

    function hideTip() {
      tip.classList.remove('show');
      try { sessionStorage.setItem('msupTipDone', '1'); } catch (e) {}
    }
    tip.querySelector('.x').onclick = function (e) { e.stopPropagation(); hideTip(); };
    tip.onclick = function () { hideTip(); ball.click(); };

    try {
      if (!sessionStorage.getItem('msupTipDone')) {
        setTimeout(function () {
          if (!opened) tip.classList.add('show');
        }, 2600);
        setTimeout(function () {
          if (!opened) hideTip();
        }, 12000);
      }
    } catch (e) {}

    var body = panel.querySelector('#msupBody');
    var input = panel.querySelector('#msupIn');
    var send = panel.querySelector('#msupSend');
    var dot = ball.querySelector('#msupDot');

    /* ---- 053 深度嵌入：动作执行（播放音乐 / 跳转 / 帖子列表）---- */
    var player = panel.querySelector('#msupPlayer');
    var ppBtn = panel.querySelector('#msupPP');
    var ptLabel = panel.querySelector('#msupPT');
    var audio = null;
    var pendingTrack = null;   // 待用户确认播放的歌

    function playTrack(t) {
      if (!t || !t.id) return;
      // 用户要求：走全站播放器（/music-shell 外壳的 MonoPlayer，跨页面不断播），
      // 而不是客服面板里另起一摊。全站任何页面都包在 shell 里，parent 一定有。
      try {
        if (window.parent !== window && window.parent.MonoPlayer
            && window.parent.MonoPlayer.playTrack) {
          window.parent.MonoPlayer.playTrack(t.id);
          addMsg('ai', '好嘞，《' + t.title + '》已经在全站播放器里放上了 ♪');
          pendingTrack = null;
          return;
        }
      } catch (e) {}
      // 兜底：外壳不可用时才用面板内播放
      if (audio) { try { audio.pause(); } catch (e) {} }
      audio = new Audio('/music/audio/' + t.id + '.ogg?fmt=ogg');
      audio.onended = function () { player.classList.remove('show'); };
      audio.onerror = function () {
        addMsg('sys', '这首歌播放失败了，可以去 /music 页面直接听');
        player.classList.remove('show');
      };
      audio.play().catch(function () {
        addMsg('sys', '浏览器拦了自动播放，点一下播放条上的按钮');
      });
      ptLabel.textContent = '♪ ' + (t.title || '') + (t.artist ? ' - ' + t.artist : '');
      ppBtn.textContent = '❚❚';
      player.classList.add('show');
      pendingTrack = null;
    }

    ppBtn.onclick = function () {
      if (!audio) return;
      if (audio.paused) { audio.play(); ppBtn.textContent = '❚❚'; }
      else { audio.pause(); ppBtn.textContent = '▶'; }
    };

    function handleAction(a) {
      if (!a || !a.type) return;
      if (a.type === 'confirm_play' && a.track) {
        pendingTrack = a.track;
        var wrap = document.createElement('div');
        wrap.className = 'msup-act';
        var b = document.createElement('button');
        b.className = 'msup-act-btn';
        b.textContent = '▶ 播放《' + a.track.title + '》';
        b.onclick = function () { wrap.remove(); playTrack(a.track); };
        wrap.appendChild(b);
        // 有多个候选时给个"换一首"入口
        if (a.candidates && a.candidates.length > 1) {
          var b2 = document.createElement('button');
          b2.className = 'msup-act-btn ghost';
          b2.textContent = '看其他 ' + (a.candidates.length - 1) + ' 首';
          b2.onclick = function () {
            var list = document.createElement('div');
            list.className = 'msup-act';
            a.candidates.forEach(function (t) {
              var lb = document.createElement('button');
              lb.className = 'msup-act-btn ghost';
              lb.textContent = '《' + t.title + '》';
              lb.onclick = function () { list.remove(); playTrack(t); };
              list.appendChild(lb);
            });
            body.appendChild(list);
            scrollBottom();
          };
          wrap.appendChild(b2);
        }
        body.appendChild(wrap);
        scrollBottom();
      } else if (a.type === 'goto' && a.path) {
        // 用户要求：说"打开XX"就直接跳转（0.6 秒后，让 AI 的话先显示完）
        addMsg('ai', '好，正在打开「' + (a.label || '页面') + '」→');
        setTimeout(function () { location.href = a.path; }, 600);
      } else if (a.type === 'show_posts' && a.posts) {
        var pw = document.createElement('div');
        pw.className = 'msup-act';
        pw.style.display = 'block';
        pw.style.width = '100%';
        a.posts.forEach(function (p) {
          var link = document.createElement('a');
          link.className = 'msup-post';
          link.href = p.url;
          link.textContent = '📄 ' + p.title;
          pw.appendChild(link);
        });
        body.appendChild(pw);
        scrollBottom();
      }
    }

    function scrollBottom() { body.scrollTop = body.scrollHeight; }

    function addMsg(cls, text) {
      var d = document.createElement('div');
      d.className = 'msup-msg ' + cls;
      d.textContent = text;
      body.appendChild(d);
      scrollBottom();
      return d;
    }

    function addQuicks() {
      var wrap = document.createElement('div');
      wrap.className = 'msup-quick';
      wrap.id = 'msupQuick';
      QUICKS.forEach(function (q) {
        var b = document.createElement('button');
        b.className = 'msup-chip';
        b.textContent = q;
        b.onclick = function () {
          wrap.remove();
          input.value = q;
          doSend();
        };
        wrap.appendChild(b);
      });
      body.appendChild(wrap);
    }

    function welcome() {
      if (body.children.length) return;
      addMsg('ai', '你好，我是小柒 👋\n社区的事都可以问我：积分怎么赚、OJ 怎么用、象棋怎么玩、账号问题……');
      addQuicks();
    }

    function setBusy(b) {
      busy = b;
      send.disabled = b;
    }

    function doSend() {
      var text = (input.value || '').trim();
      if (!text || busy) return;
      // 053：待确认播放时，用户答「是/要/播放/好」直接播（不发请求，省一次跨洋）
      if (pendingTrack && /^(是|要|好|嗯|行|对|播放|放吧|来|可以|ok|OK)[！!。.]?$/.test(text)) {
        addMsg('me', text);
        input.value = '';
        input.style.height = 'auto';
        var t = pendingTrack;
        playTrack(t);
        addMsg('ai', '好嘞，正在播放《' + t.title + '》♪');
        return;
      }
      var quick = document.getElementById('msupQuick');
      if (quick) quick.remove();
      addMsg('me', text);
      input.value = '';
      input.style.height = 'auto';
      history.push({ role: 'user', content: text });
      setBusy(true);

      var typing = document.createElement('div');
      typing.className = 'msup-msg ai';
      typing.innerHTML = '<span class="msup-typing"><i></i><i></i><i></i></span>';
      body.appendChild(typing);
      scrollBottom();

      var aiEl = null;
      var acc = '';

      fetch('/api/ai/support', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ message: text, history: history.slice(-12) })
      }).then(function (r) {
        if (!r.ok) {
          return r.json().then(function (j) {
            throw new Error(j.msg || '服务暂时不可用');
          });
        }
        var reader = r.body.getReader();
        var dec = new TextDecoder();
        var buf = '';

        function pump() {
          return reader.read().then(function (res) {
            if (res.done) {
              if (typing.parentNode) typing.remove();
              if (acc) history.push({ role: 'assistant', content: acc });
              if (!opened) dot.classList.add('show');
              setBusy(false);
              return;
            }
            buf += dec.decode(res.value, { stream: true });
            var lines = buf.split('\n');
            buf = lines.pop();
            lines.forEach(function (line) {
              if (!line.startsWith('data: ')) return;
              var payload;
              try { payload = JSON.parse(line.slice(6)); } catch (e) { return; }
              if (payload.done) return;
              if (payload.action) { handleAction(payload.action); return; }
              if (payload.t) {
                if (typing.parentNode) typing.remove();
                if (!aiEl) aiEl = addMsg('ai', '');
                acc += payload.t;
                // 清理模型模板标签（Hunyuan 常在结尾吐 </answer>）
                aiEl.textContent = acc.replace(/<\/?(answer|response|output|result)>/gi, '').trim();
                scrollBottom();
              }
            });
            return pump();
          });
        }
        return pump();
      }).catch(function (err) {
        if (typing.parentNode) typing.remove();
        addMsg('sys', err.message || '出错了，稍后再试');
        setBusy(false);
      });
    }

    ball.onclick = function () {
      opened = !opened;
      panel.classList.toggle('show', opened);
      ball.classList.toggle('open', opened);
      if (opened) {
        dot.classList.remove('show');
        try { hideTip(); } catch (e) {}
        welcome();
        setTimeout(function () { input.focus(); }, 200);
      }
    };
    panel.querySelector('.msup-x').onclick = function () {
      opened = false;
      panel.classList.remove('show');
      ball.classList.remove('open');
    };
    send.onclick = doSend;
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        doSend();
      }
    });
    input.addEventListener('input', function () {
      input.style.height = 'auto';
      input.style.height = Math.min(input.scrollHeight, 96) + 'px';
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', inject);
  } else {
    inject();
  }
})();
