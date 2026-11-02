(function(){
    'use strict';
    if (!document.body || !window.fetch) return;

    /* ========== 017 @ 提及自动补全 ========== */
    var pop = null;
    function hidePop(){ if (pop){ pop.remove(); pop = null; } }
    function esc(s){ return String(s==null?'':s).replace(/[&<>"']/g, function(c){
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }

    function caretWord(el){
      var pos = el.selectionStart, v = el.value;
      var left = v.slice(0, pos);
      var m = left.match(/(^|\s)@([^\s@]{0,19})$/);
      return m ? { start: pos - m[2].length, q: m[2] } : null;
    }

    function renderPop(items, ta, start){
      hidePop();
      if (!items.length) return;
      pop = document.createElement('div');
      pop.className = 'mono-mention-pop';
      var html = '';
      for (var i = 0; i < items.length; i++){
        html += '<div class="mono-mention-item" data-v="'+esc(items[i].username)+'">'
             + '<span class="mono-mention-name">'+esc(items[i].username)+'</span></div>';
      }
      pop.innerHTML = html;
      document.body.appendChild(pop);
      var r = ta.getBoundingClientRect();
      pop.style.left = Math.min(r.left, window.innerWidth - 220) + 'px';
      pop.style.top = (r.bottom + window.scrollY + 4) + 'px';
      var lines = Math.min(items.length, 8);
      pop.style.minWidth = Math.min(200, Math.max(r.width, 160)) + 'px';
      pop.style.maxHeight = (lines * 30 + 8) + 'px';
    }

    function insertMention(ta, start, name){
      var v = ta.value;
      var end = ta.selectionStart;
      ta.value = v.slice(0, start) + '@' + name + ' ' + v.slice(end);
      var p = start + name.length + 2;
      ta.focus();
      try { ta.setSelectionRange(p, p); } catch(e){}
      hidePop();
    }

    var mentionTimer = null;
    function onInput(e){
      var ta = e.target;
      if (!ta || ta.tagName !== 'TEXTAREA') return;
      if (!ta.name || ta.name !== 'content') return;
      var w = caretWord(ta);
      if (!w) { hidePop(); return; }
      if (mentionTimer) clearTimeout(mentionTimer);
      mentionTimer = setTimeout(function(){
        fetch('/api/users/mention?q=' + encodeURIComponent(w.q), {headers:{'X-Requested-With':'XMLHttpRequest'}})
          .then(function(r){ return r.json(); })
          .then(function(j){
            var st = caretWord(ta);
            if (!st || st.start !== w.start) return;
            renderPop(j.users || [], ta, w.start);
          }).catch(function(){});
      }, 180);
    }

    document.addEventListener('input', onInput, true);
    document.addEventListener('keydown', function(e){
      if (!pop) return;
      if (e.key === 'Escape'){ hidePop(); return; }
      if (e.key !== 'Enter' && e.key !== 'Tab') return;
      var first = pop.querySelector('.mono-mention-item');
      if (!first) return;
      var ta = document.activeElement;
      var st = caretWord(ta);
      if (!st) return;
      e.preventDefault();
      insertMention(ta, st.start, first.getAttribute('data-v'));
    }, true);
    document.addEventListener('click', function(e){
      if (!pop) return;
      if (pop.contains(e.target)) return;
      hidePop();
    }, true);
    window.addEventListener('blur', hidePop);

    /* ========== 018 草稿自动保存 ========== */
    var DRAFT_KEY = 'mono_draft_';
    function draftKey(ta){
      var f = ta.form;
      var pid = (f && f.action && f.action.match(/(?:edit_post|post)\/(\d+)/)) || null;
      var postId = pid ? pid[1] : (ta.name || 'x');
      var kind = ta.name === 'content'
        ? (ta.placeholder && ta.placeholder.indexOf('帖子') >= 0 ? 'post' : 'reply')
        : (ta.name || 'x');
      var host = location.pathname.replace(/[^a-zA-Z0-9]/g, '_');
      return DRAFT_KEY + kind + '_' + host + (postId ? '_' + postId : '');
    }

    function initDraft(ta){
      if (ta.dataset.draftOn) return;
      ta.dataset.draftOn = '1';
      var key = draftKey(ta);
      // 恢复
      try {
        var saved = localStorage.getItem(key);
        if (saved && !ta.value.trim()) {
          ta.value = saved;
          ta.dispatchEvent(new Event('input', {bubbles:true}));
          flash('已恢复上次未完成的草稿');
        }
      } catch(e){}
      // 定时存
      var timer = null;
      function save(){
        if (!ta.value.trim()){ try { localStorage.removeItem(key); } catch(e){} return; }
        try { localStorage.setItem(key, ta.value); } catch(e){}
      }
      ta.addEventListener('input', function(){
        if (timer) clearTimeout(timer);
        timer = setTimeout(save, 1200);
      });
      // 提交成功后清掉
      if (ta.form) {
        ta.form.addEventListener('submit', function(){
          setTimeout(function(){ try { localStorage.removeItem(key); } catch(e){} }, 100);
        });
      }
    }

    function flash(msg){
      var wrap = document.getElementById('toastWrap');
      if (!wrap) return;
      var t = document.createElement('div');
      t.style.cssText = 'background:#111;color:#fff;padding:10px 14px;border-radius:10px;'
                      + 'box-shadow:0 6px 18px rgba(0,0,0,.2);font-size:13px;';
      t.textContent = msg;
      wrap.appendChild(t);
      setTimeout(function(){ t.remove(); }, 3200);
    }

    var tas = document.querySelectorAll('textarea[name="content"], textarea#editor');
    for (var i = 0; i < tas.length; i++) initDraft(tas[i]);

    /* ========== 019 返回列表保持滚动位置 ========== */
    if ('scrollRestoration' in history) {
      try { history.scrollRestoration = 'manual'; } catch(e){}
    }
    var SKIP = /^\/(inbox|settings|create-post|edit_post|login|register)/;
    var skey = null;
    if (!SKIP.test(location.pathname)) {
      skey = 'mono_scroll_' + location.pathname;
      try {
        var savedY = parseInt(sessionStorage.getItem(skey) || '0', 10);
        if (savedY > 0) {
          // 等布局稳定后再滚，避免图片加载完位置全乱
          var tries = 0;
          (function restore(){
            window.scrollTo(0, savedY);
            tries++;
            if (tries < 6 && window.pageYOffset !== savedY) setTimeout(restore, 90);
          })();
        }
      } catch(e){}
      window.addEventListener('beforeunload', function(){
        try { sessionStorage.setItem(skey, String(window.pageYOffset || window.scrollY || 0)); } catch(e){}
      });
    }

    // 点进详情前先记当前位置，返回时（bfcache 或重新渲染）由上面的 restore 恢复
    document.addEventListener('click', function(e){
      if (!skey) return;
      var a = e.target.closest ? e.target.closest('a[href]') : null;
      if (!a) return;
      var href = a.getAttribute('href') || '';
      if (href.charAt(0) !== '/' || href === location.pathname) return;
      // 列表页 → 详情页：存住当前滚动位置
      if (!/\/(view_post|post)\/\d+/.test(href)) return;
      try { sessionStorage.setItem(skey, String(window.pageYOffset || window.scrollY || 0)); } catch(e){}
    }, true);
  })();
