/* md_editor.js — 站点功能规划第 042 项 · Markdown 编辑器增强
 *
 * 挂在 #editor（发帖主编辑器）上自动生效：
 *   - 工具栏扩展：表格、任务列表（配合 edit_post.html 里的按钮）
 *   - 快捷键：Ctrl+B 粗体 / Ctrl+I 斜体 / Ctrl+K 链接 / Ctrl+E 代码块 / Ctrl+S 存草稿
 *   - 源码模式切换：分栏预览 ↔ 纯源码全宽
 *   - 粘贴清洗：剪贴板里的 HTML（从网页/Word 复制的富文本）自动转成 Markdown
 *   - 图片上传交给 img_uploader.js（043），这里只负责插入语法
 */
(function () {
  'use strict';
  var ta = document.getElementById('editor');
  if (!ta) return;

  function fire() { ta.dispatchEvent(new Event('input', { bubbles: true })); }
  function surround(before, after, placeholder) {
    var s = ta.selectionStart, e = ta.selectionEnd;
    var sel = ta.value.slice(s, e) || placeholder || '';
    ta.value = ta.value.slice(0, s) + before + sel + after + ta.value.slice(e);
    ta.focus();
    ta.selectionStart = s + before.length;
    ta.selectionEnd = s + before.length + sel.length;
    fire();
  }
  function prefixLines(prefix) {
    var s = ta.selectionStart, e = ta.selectionEnd;
    var v = ta.value;
    var ls = v.lastIndexOf('\n', s - 1) + 1;
    var le = v.indexOf('\n', e); if (le < 0) le = v.length;
    var block = v.slice(ls, le).split('\n').map(function (l) {
      return l.startsWith(prefix) ? l : prefix + l;
    }).join('\n');
    ta.value = v.slice(0, ls) + block + v.slice(le);
    ta.focus();
    ta.selectionStart = ls; ta.selectionEnd = ls + block.length;
    fire();
  }
  function insertText(text, caretOffset) {
    var s = ta.selectionStart, e = ta.selectionEnd;
    ta.value = ta.value.slice(0, s) + text + ta.value.slice(e);
    ta.focus();
    var c = s + (caretOffset == null ? text.length : caretOffset);
    ta.selectionStart = ta.selectionEnd = c;
    fire();
  }

  /* ---------- 042 工具栏动作（edit_post.html 的按钮调用） ---------- */
  window.mdInsertTable = function () {
    insertText('| 列1 | 列2 | 列3 |\n| --- | --- | --- |\n|  |  |  |\n|  |  |  |\n', 8);
  };
  window.mdInsertTask = function () { prefixLines('- [ ] '); };
  window.mdToggleSource = function () {
    document.body.classList.toggle('editor-source-only');
    var b = document.getElementById('srcModeBtn');
    if (b) b.textContent = document.body.classList.contains('editor-source-only')
      ? '分栏模式' : '源码模式';
  };

  /* ---------- 快捷键 ---------- */
  ta.addEventListener('keydown', function (e) {
    if (!(e.ctrlKey || e.metaKey)) return;
    var k = e.key.toLowerCase();
    if (k === 'b') { e.preventDefault(); surround('**', '**', '加粗文字'); }
    else if (k === 'i') { e.preventDefault(); surround('*', '*', '斜体文字'); }
    else if (k === 'k') { e.preventDefault(); surround('[', '](https://)', '链接文字'); }
    else if (k === 'e') { e.preventDefault(); surround('```\n', '\n```', '代码'); }
    else if (k === 's') { e.preventDefault(); if (window.monoSaveDraft) window.monoSaveDraft(); }
  });

  /* ---------- 粘贴清洗：HTML → Markdown ---------- */
  function htmlToMd(html) {
    var doc = new DOMParser().parseFromString(html, 'text/html');
    doc.querySelectorAll('script,style,iframe,noscript').forEach(function (n) { n.remove(); });

    function inline(node) {
      var out = '';
      node.childNodes.forEach(function (c) {
        if (c.nodeType === 3) { out += c.nodeValue.replace(/\s+/g, ' '); return; }
        if (c.nodeType !== 1) return;
        var tag = c.tagName.toLowerCase();
        var inner = inline(c);
        if (tag === 'br') out += '\n';
        else if (tag === 'strong' || tag === 'b') out += '**' + inner + '**';
        else if (tag === 'em' || tag === 'i') out += '*' + inner + '*';
        else if (tag === 'code') out += '`' + inner + '`';
        else if (tag === 'a') out += '[' + inner + '](' + (c.getAttribute('href') || '') + ')';
        else if (tag === 'img') out += '![' + (c.getAttribute('alt') || '图片') + '](' + (c.getAttribute('src') || '') + ')';
        else out += inner;
      });
      return out;
    }

    var blocks = [];
    doc.body.childNodes.forEach(function (c) {
      if (c.nodeType === 3) {
        var t = c.nodeValue.trim();
        if (t) blocks.push(t);
        return;
      }
      if (c.nodeType !== 1) return;
      var tag = c.tagName.toLowerCase();
      if (/^h[1-6]$/.test(tag)) {
        blocks.push('#'.repeat(+tag[1]) + ' ' + inline(c).trim());
      } else if (tag === 'p' || tag === 'div') {
        var t2 = inline(c).trim();
        if (t2) blocks.push(t2);
      } else if (tag === 'blockquote') {
        blocks.push(inline(c).trim().split('\n').map(function (l) { return '> ' + l; }).join('\n'));
      } else if (tag === 'ul' || tag === 'ol') {
        var i = 1, lines = [];
        c.querySelectorAll('li').forEach(function (li) {
          var line = (tag === 'ul' ? '- ' : (i++) + '. ') + inline(li).trim();
          // 任务列表（Word/网页复制的复选框）
          var cb = li.querySelector('input[type=checkbox]');
          if (cb) line = line.replace(/^- /, '- [ ] ');
          lines.push(line);
        });
        blocks.push(lines.join('\n'));
      } else if (tag === 'pre') {
        blocks.push('```\n' + (c.textContent || '').trim() + '\n```');
      } else if (tag === 'table') {
        var rows = [];
        c.querySelectorAll('tr').forEach(function (tr) {
          rows.push(Array.prototype.map.call(tr.querySelectorAll('th,td'), function (td) {
            return inline(td).trim().replace(/\|/g, '\\|');
          }));
        });
        if (rows.length) {
          if (!c.querySelector('th')) {
            rows.splice(1, 0, rows[0].map(function () { return '---'; }));
          } else {
            rows.splice(1, 0, rows[0].map(function () { return '---'; }));
          }
          blocks.push(rows.map(function (r) { return '| ' + r.join(' | ') + ' |'; }).join('\n'));
        }
      } else {
        var t3 = inline(c).trim();
        if (t3) blocks.push(t3);
      }
    });
    return blocks.join('\n\n').replace(/\n{3,}/g, '\n\n').trim();
  }

  ta.addEventListener('paste', function (e) {
    var cd = e.clipboardData;
    if (!cd) return;
    // 图片优先交给 img_uploader（它自己也监听，这里跳过避免双插）
    var items = cd.items || [];
    for (var i = 0; i < items.length; i++) {
      if (items[i].kind === 'file' && items[i].type.indexOf('image/') === 0) return;
    }
    var html = cd.getData('text/html');
    if (!html) return;                       // 纯文本走默认粘贴
    var md = htmlToMd(html);
    if (!md) { e.preventDefault(); return; }
    e.preventDefault();
    var s = ta.selectionStart, en = ta.selectionEnd;
    ta.value = ta.value.slice(0, s) + md + ta.value.slice(en);
    ta.selectionStart = ta.selectionEnd = s + md.length;
    fire();
  });

  /* ---------- 源码模式样式（跟着 body class 走） ---------- */
  var st = document.createElement('style');
  st.textContent =
    'body.editor-source-only .preview-pane{display:none!important;}' +
    'body.editor-source-only .editor-split .editor-pane{max-width:none;}' +
    'textarea.drop-target{border-color:#c9a227!important;box-shadow:0 0 0 3px rgba(201,162,39,.18);}';
  document.head.appendChild(st);
})();
