(function () {
  var TILE = 16;
  var cvs = document.getElementById('gCanvas');
  if (!cvs) return;
  var ctx = cvs.getContext('2d');
  ctx.imageSmoothingEnabled = false;

  var VW = cvs.width, VH = cvs.height;

  var map = null, tilesImg = null, spritesImg = null;
  var solid = [];
  var torches = [];

  var player = {
    x: 0, y: 0, w: 10, h: 8,
    dir: 0, moving: false, anim: 0, frame: 0,
    speed: 72, color: 0
  };

  var keys = {};
  var joy = { active: false, dx: 0, dy: 0, id: null };

  var lampCanvas = document.createElement('canvas');
  lampCanvas.width = VW;
  lampCanvas.height = VH;
  var lampCtx = lampCanvas.getContext('2d');
  var flick = 0, lastT = 0, acc = 0;

  function loadAssets(cb) {
    var n = 0, done = 0;
    function step() { if (++done >= n) cb(); }
    function img(src, fn) {
      n++;
      var im = new Image();
      im.onload = function () { fn(im); step(); };
      im.src = src;
    }
    img('/static/guling/tiles.png', function (im) { tilesImg = im; });
    img('/static/guling/sprites.png', function (im) { spritesImg = im; });
    n++;
    fetch('/static/guling/map.json').then(function (r) { return r.json(); }).then(function (j) {
      map = j;
      solid = [];
      for (var i = 0; i < j.solid.length; i++) solid[j.solid[i]] = true;
      torches = [];
      for (var y = 0; y < j.h; y++) {
        for (var x = 0; x < j.w; x++) {
          if (j.tiles[y][x] === 5 || j.tiles[y][x] === 6) {
            torches.push({ x: x, y: y, ph: (x + y) % 2 });
          }
        }
      }
      player.x = j.spawn[0] * TILE + 3;
      player.y = j.spawn[1] * TILE + 6;
      step();
    }).catch(step);
  }

  function isSolid(px, py) {
    var tx = Math.floor(px / TILE), ty = Math.floor(py / TILE);
    if (tx < 0 || ty < 0 || tx >= map.w || ty >= map.h) return true;
    return !!solid[map.tiles[ty][tx]];
  }

  function collide(nx, ny) {
    var x0 = nx - player.w / 2, x1 = nx + player.w / 2;
    var y0 = ny - player.h / 2, y1 = ny + player.h / 2;
    for (var tx = Math.floor(x0 / TILE); tx <= Math.floor((x1 - 0.01) / TILE); tx++) {
      for (var ty = Math.floor(y0 / TILE); ty <= Math.floor((y1 - 0.01) / TILE); ty++) {
        if (tx < 0 || ty < 0 || tx >= map.w || ty >= map.h) return true;
        if (solid[map.tiles[ty][tx]]) return true;
      }
    }
    return false;
  }

  function update(dt) {
    var dx = 0, dy = 0;
    if (keys['w'] || keys['arrowup']) dy -= 1;
    if (keys['s'] || keys['arrowdown']) dy += 1;
    if (keys['a'] || keys['arrowleft']) dx -= 1;
    if (keys['d'] || keys['arrowright']) dx += 1;
    if (joy.active) {
      dx += joy.dx;
      dy += joy.dy;
    }
    var len = Math.sqrt(dx * dx + dy * dy);
    player.moving = len > 0.15;
    if (player.moving) {
      dx /= len; dy /= len;
      var sp = player.speed * dt;
      var nx = player.x + dx * sp;
      var ny = player.y + dy * sp;
      if (!collide(nx, player.y)) player.x = nx;
      if (!collide(player.x, ny)) player.y = ny;
      if (Math.abs(dx) > Math.abs(dy)) {
        player.dir = dx > 0 ? 2 : 3;
      } else {
        player.dir = dy > 0 ? 0 : 1;
      }
      player.anim += dt * 8;
      player.frame = Math.floor(player.anim) % 2;
    } else {
      player.anim = 0;
      player.frame = 0;
    }
    flick += dt * 9;
  }

  function camPos() {
    var cx = player.x - VW / 2;
    var cy = player.y - VH / 2;
    cx = Math.max(0, Math.min(map.w * TILE - VW, cx));
    cy = Math.max(0, Math.min(map.h * TILE - VH, cy));
    if (map.w * TILE < VW) cx = (map.w * TILE - VW) / 2;
    if (map.h * TILE < VH) cy = (map.h * TILE - VH) / 2;
    return { x: Math.round(cx), y: Math.round(cy) };
  }

  function drawSprite(idx, sx, sy) {
    var col = idx % 8, row = Math.floor(idx / 8);
    ctx.drawImage(spritesImg, col * TILE, row * TILE, TILE, TILE, sx, sy, TILE, TILE);
  }

  function playerIdx() {
    return player.color * 8 + player.dir * 2 + player.frame;
  }

  function render() {
    var cam = camPos();
    ctx.fillStyle = '#08080a';
    ctx.fillRect(0, 0, VW, VH);
    var tx0 = Math.max(0, Math.floor(cam.x / TILE));
    var ty0 = Math.max(0, Math.floor(cam.y / TILE));
    var tx1 = Math.min(map.w - 1, Math.floor((cam.x + VW) / TILE));
    var ty1 = Math.min(map.h - 1, Math.floor((cam.y + VH) / TILE));
    var tphase = Math.floor(flick) % 2;
    for (var ty = ty0; ty <= ty1; ty++) {
      for (var tx = tx0; tx <= tx1; tx++) {
        var t = map.tiles[ty][tx];
        if (t === 5) t = 5 + tphase;
        ctx.drawImage(tilesImg, t * TILE, 0, TILE, TILE, tx * TILE - cam.x, ty * TILE - cam.y, TILE, TILE);
      }
    }
    drawSprite(playerIdx(), Math.round(player.x - 8 - cam.x), Math.round(player.y - 11 - cam.y));

    lampCtx.globalCompositeOperation = 'source-over';
    lampCtx.clearRect(0, 0, VW, VH);
    lampCtx.fillStyle = 'rgba(0,0,0,0.94)';
    lampCtx.fillRect(0, 0, VW, VH);
    lampCtx.globalCompositeOperation = 'destination-out';
    var px = player.x - cam.x, py = player.y - cam.y;
    var g = lampCtx.createRadialGradient(px, py, 8, px, py, 76);
    g.addColorStop(0, 'rgba(0,0,0,1)');
    g.addColorStop(0.6, 'rgba(0,0,0,0.85)');
    g.addColorStop(1, 'rgba(0,0,0,0)');
    lampCtx.fillStyle = g;
    lampCtx.beginPath();
    lampCtx.arc(px, py, 76, 0, Math.PI * 2);
    lampCtx.fill();
    for (var i = 0; i < torches.length; i++) {
      var t2 = torches[i];
      var sx = t2.x * TILE + 8 - cam.x, sy = t2.y * TILE + 6 - cam.y;
      if (sx < -40 || sy < -40 || sx > VW + 40 || sy > VH + 40) continue;
      var fr = t2.ph === (tphase % 2) ? 22 : 26;
      var g2 = lampCtx.createRadialGradient(sx, sy, 2, sx, sy, fr);
      g2.addColorStop(0, 'rgba(0,0,0,0.9)');
      g2.addColorStop(1, 'rgba(0,0,0,0)');
      lampCtx.fillStyle = g2;
      lampCtx.beginPath();
      lampCtx.arc(sx, sy, fr, 0, Math.PI * 2);
      lampCtx.fill();
    }
    ctx.drawImage(lampCanvas, 0, 0);
  }

  function loop(t) {
    if (!map || !tilesImg || !spritesImg) { requestAnimationFrame(loop); return; }
    if (!lastT) lastT = t;
    var dt = (t - lastT) / 1000;
    lastT = t;
    if (dt > 0.1) dt = 0.1;
    acc += dt;
    while (acc >= 1 / 120) {
      update(1 / 120);
      acc -= 1 / 120;
    }
    render();
    requestAnimationFrame(loop);
  }

  document.addEventListener('keydown', function (e) {
    var k = (e.key || '').toLowerCase();
    keys[k] = true;
    if (['arrowup', 'arrowdown', 'arrowleft', 'arrowright'].indexOf(k) >= 0) e.preventDefault();
  });
  document.addEventListener('keyup', function (e) {
    keys[(e.key || '').toLowerCase()] = false;
  });

  var zone = document.getElementById('gJoy');
  if (zone) {
    var knob = document.getElementById('gJoyKnob');
    function joyMove(e) {
      if (!joy.active) return;
      var r = zone.getBoundingClientRect();
      var cx = r.left + r.width / 2, cy = r.top + r.height / 2;
      var t = e.touches ? e.touches[0] : e;
      var dx = t.clientX - cx, dy = t.clientY - cy;
      var max = r.width / 2;
      var d = Math.sqrt(dx * dx + dy * dy);
      if (d > max) { dx = dx / d * max; dy = dy / d * max; }
      joy.dx = dx / max;
      joy.dy = dy / max;
      if (knob) knob.style.transform = 'translate(' + dx + 'px,' + dy + 'px)';
      e.preventDefault();
    }
    function joyEnd() {
      joy.active = false;
      joy.dx = joy.dy = 0;
      if (knob) knob.style.transform = 'translate(0,0)';
    }
    zone.addEventListener('touchstart', function (e) {
      joy.active = true;
      joy.id = e.touches[0].identifier;
      joyMove(e);
    }, { passive: false });
    zone.addEventListener('touchmove', joyMove, { passive: false });
    zone.addEventListener('touchend', joyEnd);
    zone.addEventListener('touchcancel', joyEnd);
  }

  loadAssets(function () { });
  requestAnimationFrame(loop);
})();
