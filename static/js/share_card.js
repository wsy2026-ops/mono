/* share_card.js — 站点功能规划第 033 项 · 分享卡片图片生成
 *
 * 纯前端 canvas 绘制：标题、摘要、作者头像、站点标识，黑白极简风（不加装饰）。
 * 用法：<button data-share-card data-title data-summary data-author data-avatar>
 *      引入本脚本即可；点击弹预览 → 下载 PNG / 复制链接。
 */
(function () {
  var W = 750, H = 420;           // 卡片画布（2x 导出）
  var FONT = '"PingFang SC","Microsoft YaHei",system-ui,sans-serif';

  function wrap(ctx, text, maxW) {
    var lines = [], line = "";
    for (var i = 0; i < text.length; i++) {
      var ch = text[i];
      if (ch === "\n") { lines.push(line); line = ""; continue; }
      if (ctx.measureText(line + ch).width > maxW && line) {
        lines.push(line); line = ch;
      } else {
        line += ch;
      }
    }
    if (line) lines.push(line);
    return lines;
  }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function drawCard(opt) {
    var cv = document.createElement('canvas');
    cv.width = W * 2; cv.height = H * 2;
    var ctx = cv.getContext('2d');
    ctx.scale(2, 2);

    // 白底黑字的极简底
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, W, H);
    ctx.strokeStyle = '#111111';
    ctx.lineWidth = 2;
    ctx.strokeRect(18, 18, W - 36, H - 36);

    // 顶部站点标识
    ctx.fillStyle = '#111111';
    ctx.font = '700 17px ' + FONT;
    ctx.textBaseline = 'alphabetic';
    ctx.fillText('MONO 社区', 44, 66);
    ctx.font = '400 12px ' + FONT;
    ctx.fillStyle = '#8a8a8a';
    ctx.fillText(location.host, 44, 84);

    // 作者头像（黑白圆）
    var avX = W - 96, avY = 46, avR = 24;
    ctx.save();
    ctx.beginPath();
    ctx.arc(avX + avR, avY + avR, avR, 0, Math.PI * 2);
    ctx.closePath();
    ctx.clip();
    ctx.fillStyle = '#e8e8e8';
    ctx.fillRect(avX, avY, avR * 2, avR * 2);
    var img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = function () { ctx.drawImage(img, avX, avY, avR * 2, avR * 2); };
    img.onerror = function () {
      ctx.fillStyle = '#111';
      ctx.font = '700 20px ' + FONT;
      ctx.textAlign = 'center';
      ctx.fillText((opt.author || '?').slice(0, 1), avX + avR, avY + avR + 7);
      ctx.textAlign = 'left';
    };
    if (opt.avatar) img.src = opt.avatar;

    // 作者名
    ctx.font = '400 12px ' + FONT;
    ctx.fillStyle = '#8a8a8a';
    ctx.textAlign = 'right';
    ctx.fillText('@' + (opt.author || '匿名'), W - 44, avY + avR + 40);
    ctx.textAlign = 'left';

    // 标题（粗体大字，最多 3 行）
    ctx.fillStyle = '#111111';
    ctx.font = '800 30px ' + FONT;
    var titleLines = wrap(ctx, opt.title || '无标题', W - 100).slice(0, 3);
    var y = 168;
    titleLines.forEach(function (ln) { ctx.fillText(ln, 44, y); y += 40; });

    // 摘要（灰字，最多 3 行）
    y += 12;
    ctx.font = '400 15px ' + FONT;
    ctx.fillStyle = '#5a5a5a';
    var sumLines = wrap(ctx, opt.summary || '', W - 100).slice(0, 3);
    sumLines.forEach(function (ln) { ctx.fillText(ln, 44, y); y += 24; });

    // 底部链接
    ctx.font = '400 12px ' + FONT;
    ctx.fillStyle = '#8a8a8a';
    ctx.fillText((opt.url || location.href).slice(0, 90), 44, H - 44);

    return cv;
  }

  function openPanel(opt) {
    var old = document.getElementById('shareCardPanel');
    if (old) old.remove();

    var mask = document.createElement('div');
    mask.id = 'shareCardPanel';
    mask.style.cssText = 'position:fixed;inset:0;z-index:3000;background:rgba(0,0,0,.5);' +
      'display:flex;align-items:center;justify-content:center;padding:20px;';

    var box = document.createElement('div');
    box.style.cssText = 'background:var(--card,#fff);border-radius:16px;padding:20px;' +
      'max-width:640px;width:100%;box-shadow:0 18px 60px rgba(0,0,0,.25);';

    var cv = drawCard(opt);
    cv.style.cssText = 'width:100%;height:auto;border-radius:10px;border:1px solid var(--line,#e5e5e5);';

    var row = document.createElement('div');
    row.style.cssText = 'display:flex;gap:10px;margin-top:14px;justify-content:flex-end;';

    var dl = document.createElement('button');
    dl.textContent = '下载图片';
    dl.className = 'btn sm';
    dl.onclick = function () {
      var a = document.createElement('a');
      a.download = 'mono-share-' + Date.now() + '.png';
      a.href = cv.toDataURL('image/png');
      a.click();
    };

    var cp = document.createElement('button');
    cp.textContent = '复制链接';
    cp.className = 'btn sm ghost';
    cp.onclick = function () {
      var done = function () { cp.textContent = '已复制 ✓'; setTimeout(function () { cp.textContent = '复制链接'; }, 1500); };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(opt.url || location.href).then(done, function () { fallbackCopy(opt.url || location.href); done(); });
      } else { fallbackCopy(opt.url || location.href); done(); }
    };

    var close = document.createElement('button');
    close.textContent = '关闭';
    close.className = 'btn sm ghost';
    close.onclick = function () { mask.remove(); };

    row.appendChild(cp);
    row.appendChild(dl);
    row.appendChild(close);

    var ttl = document.createElement('div');
    ttl.textContent = '分享卡片';
    ttl.style.cssText = 'font-size:13px;font-weight:700;margin-bottom:10px;';

    box.appendChild(ttl);
    box.appendChild(cv);
    box.appendChild(row);
    mask.appendChild(box);
    mask.addEventListener('click', function (e) { if (e.target === mask) mask.remove(); });
    document.body.appendChild(mask);
  }

  function fallbackCopy(text) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;opacity:0;';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch (e) {}
    ta.remove();
  }

  document.addEventListener('click', function (e) {
    var b = e.target.closest ? e.target.closest('[data-share-card]') : null;
    if (!b) return;
    e.preventDefault();
    openPanel({
      title: b.getAttribute('data-title') || document.title,
      summary: b.getAttribute('data-summary') || '',
      author: b.getAttribute('data-author') || '',
      avatar: b.getAttribute('data-avatar') || '',
      url: b.getAttribute('data-url') || location.href
    });
  });
})();
