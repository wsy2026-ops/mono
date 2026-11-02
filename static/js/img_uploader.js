/* img_uploader.js — 站点功能规划第 043 项 · 图片上传体验重做
 *
 * 挂到任意 textarea（data-uploader 属性即启用）：
 *   - 拖拽上传（拖图片到输入框）
 *   - Ctrl+V 粘贴截图直接上传
 *   - 客户端先压缩（长边 ≤1600、WebP/JPEG 0.82）再传，省带宽
 *   - 多图排队、逐个上传，光标处先占位后替换
 *   - XHR 进度 + 失败重试
 *   - 上传成功插入 Markdown 图片语法：![文件名](url)
 *
 * 用法：<textarea data-uploader data-upload-api="/api/upload-image"></textarea>
 * 依赖：无（原生 XHR + canvas）
 */
(function () {
  'use strict';
  var MAX_EDGE = 1600;      // 客户端压缩长边上限
  var QUALITY = 0.82;
  var MAX_FILES = 9;        // 单次最多

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /* ---------- 客户端压缩：大图先缩再传 ---------- */
  function compress(file) {
    return new Promise(function (resolve) {
      if (!file.type || file.type.indexOf('image/') !== 0) return resolve(file);
      if (file.type === 'image/gif') return resolve(file);   // GIF 不动，保动画
      var url = URL.createObjectURL(file);
      var img = new Image();
      img.onload = function () {
        try {
          var w = img.naturalWidth, h = img.naturalHeight;
          if (w <= MAX_EDGE && h <= MAX_EDGE && file.size < 400 * 1024) {
            URL.revokeObjectURL(url);
            return resolve(file);   // 已经够小，原样传
          }
          var scale = Math.min(1, MAX_EDGE / Math.max(w, h));
          var cv = document.createElement('canvas');
          cv.width = Math.round(w * scale);
          cv.height = Math.round(h * scale);
          cv.getContext('2d').drawImage(img, 0, 0, cv.width, cv.height);
          URL.revokeObjectURL(url);
          cv.toBlob(function (blob) {
            resolve(blob || file);
          }, 'image/webp', QUALITY);
        } catch (e) { URL.revokeObjectURL(url); resolve(file); }
      };
      img.onerror = function () { URL.revokeObjectURL(url); resolve(file); };
      img.src = url;
    });
  }

  /* ---------- 单文件上传（XHR 带进度） ---------- */
  function upload(ta, file, token) {
    var api = ta.getAttribute('data-upload-api') || '/api/upload-image';
    return compress(file).then(function (blob) {
      return new Promise(function (resolve, reject) {
        var fd = new FormData();
        var name = (file.name || 'image').replace(/\.[^.]+$/, '') + '.webp';
        fd.append('file', blob, name);
        var xhr = new XMLHttpRequest();
        xhr.open('POST', api, true);
        xhr.upload.onprogress = function (e) {
          if (e.lengthComputable) replaceToken(ta, token, null,
            '![上传中 ' + Math.round(e.loaded / e.total * 100) + '%](' + token + ')');
        };
        xhr.onload = function () {
          if (xhr.status >= 200 && xhr.status < 300) {
            try {
              var j = JSON.parse(xhr.responseText);
              if (j.ok && j.url) { replaceToken(ta, token, null, '![' + esc(j.name || '图片') + '](' + j.url + ')'); resolve(j); return; }
              reject(new Error(j.msg || '上传失败'));
            } catch (e) { reject(new Error('响应解析失败')); }
          } else {
            reject(new Error('HTTP ' + xhr.status));
          }
        };
        xhr.onerror = function () { reject(new Error('网络错误')); };
        xhr.send(fd);
      });
    }).catch(function (err) {
      // 失败：占位符变成可点的重试
      replaceToken(ta, token, null,
        '![上传失败：' + esc(err.message || '?') + '，点击重试](upload-retry:' + token + ')');
      throw err;
    });
  }

  /* ---------- 占位符机制：光标处插 token，完成后替换 ---------- */
  function insertToken(ta, label) {
    var token = 'UP' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    var text = '![' + label + '](' + token + ')';
    var start = ta.selectionStart, end = ta.selectionEnd;
    ta.value = ta.value.slice(0, start) + text + ta.value.slice(end);
    ta.selectionStart = ta.selectionEnd = start + text.length;
    ta.dispatchEvent(new Event('input', { bubbles: true }));
    return token;
  }

  function replaceToken(ta, token, _old, text) {
    var re = new RegExp('!\\[[^\\]]*\\]\\(' + token + '\\)');
    if (re.test(ta.value)) {
      ta.value = ta.value.replace(re, text);
      ta.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }

  /* ---------- 入口：文件列表 ---------- */
  function handleFiles(ta, files) {
    var imgs = Array.prototype.filter.call(files, function (f) {
      return f.type && f.type.indexOf('image/') === 0;
    }).slice(0, MAX_FILES);
    if (!imgs.length) return;
    imgs.forEach(function (f, i) {
      var token = insertToken(ta, '图片' + (imgs.length > 1 ? ' ' + (i + 1) : ''));
      upload(ta, f, token);
    });
  }

  /* ---------- 挂载 ---------- */
  function bind(ta) {
    if (ta.dataset.uploaderOn) return;
    ta.dataset.uploaderOn = '1';

    // 拖拽
    ta.addEventListener('dragover', function (e) {
      if (e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types, 'Files') >= 0) {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'copy';
        ta.classList.add('drop-target');
      }
    });
    ta.addEventListener('dragleave', function () { ta.classList.remove('drop-target'); });
    ta.addEventListener('drop', function (e) {
      ta.classList.remove('drop-target');
      if (!e.dataTransfer || !e.dataTransfer.files.length) return;
      e.preventDefault();
      handleFiles(ta, e.dataTransfer.files);
    });

    // 粘贴（截图/复制的图片）
    ta.addEventListener('paste', function (e) {
      var items = (e.clipboardData || {}).items || [];
      for (var i = 0; i < items.length; i++) {
        if (items[i].kind === 'file' && items[i].type.indexOf('image/') === 0) {
          var f = items[i].getAsFile();
          if (f) { e.preventDefault(); handleFiles(ta, [f]); return; }
        }
      }
    });

    // 点击上传失败的占位符 → 重试（从原文件已丢，重新弹选择框）
    ta.addEventListener('click', function () {
      var pos = ta.selectionStart;
      var re = /!\[[^\]]*\]\(upload-retry:(UP[a-z0-9]+)\)/g, m;
      while ((m = re.exec(ta.value))) {
        if (pos >= m.index && pos <= m.index + m[0].length) {
          var inp = document.createElement('input');
          inp.type = 'file'; inp.accept = 'image/*';
          inp.onchange = function () { if (inp.files[0]) upload(ta, inp.files[0], m[1]); };
          inp.click();
          return;
        }
      }
    });
  }

  document.querySelectorAll('textarea[data-uploader]').forEach(bind);
  // 动态插入的（评论框）也管
  document.addEventListener('focusin', function (e) {
    if (e.target && e.target.tagName === 'TEXTAREA' && e.target.hasAttribute('data-uploader')) {
      bind(e.target);
    }
  });

  /* 工具栏「图片」按钮：弹选择框（多选）；移动端直接调相机 */
  window.imgUploader = {
    pick: function (ta) {
      var inp = document.createElement('input');
      inp.type = 'file';
      inp.accept = 'image/*';
      inp.multiple = true;
      if (/Mobi|Android|iPhone/i.test(navigator.userAgent)) {
        inp.capture = 'environment';   // 手机：直接后置摄像头
      }
      inp.onchange = function () {
        if (inp.files && inp.files.length) handleFiles(ta, inp.files);
      };
      inp.click();
    }
  };
})();
