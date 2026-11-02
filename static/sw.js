// MONO 社区 Service Worker
// v4 起不再把首页塞进离线壳：首页是动态页面，缓存它迟早会端出几个月前的旧壳。
// 现在改成「带版本戳的壳」——版本对不上就退回离线提示页，绝不给旧界面。
// v3 起音频流不走缓存（缓存响应丢失 Range 支持，会导致拖动进度条/刷新续播定位失效）
// v5：域名从 Cloudflare 橙云切到灰云直连，源站换了证书（CF Origin CA → Let's Encrypt）。
// 旧版本号的 SW 会在 activate 时清掉旧缓存，这里 bump 一下强制重装，
// 否则手机上还挂着 v4 的 SW，握手失败时只会吐离线页。
const CACHE = 'mono-pwa-v7';
const VER_KEY = '/__ver';
const SHELLVER_KEY = '/__vof';
const SHELL = [
  '/static/manifest.json',
  '/static/icon.svg',
  '/static/default_avatar.png',
  '/static/css/base.css',
  '/static/js/offline-queue.js'
];

// 可以离线玩的页面（全是纯前端游戏，唯一的联网点是提交成绩 —— 由 offline-queue.js 兜住）
const OFFLINE_PAGES = [
  '/game/24', '/game/sudoku', '/game/color_blind', '/game/jump',
  '/game/xianyi', '/game/hanoi', '/game/klotski', '/game/klotski_cn', '/game/mine'
];

const OFFLINE_HTML = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
  + '<meta name="viewport" content="width=device-width,initial-scale=1">'
  + '<title>连接失败 · MONO</title><style>'
  + 'html,body{min-height:100%;margin:0}'
  + 'body{background:#141312;color:#f0ece4;font-family:system-ui,-apple-system,'
  + '"PingFang SC","Microsoft YaHei",sans-serif;padding:20px 18px 40px}'
  + 'h1{font-size:18px;margin:0 0 6px}'
  + '.sub{color:#a09a90;font-size:13px;margin:0 0 16px;line-height:1.7}'
  + 'button{font:inherit;font-weight:700;padding:11px 22px;border-radius:10px;border:0;'
  + 'background:#C9A227;color:#2a2205;cursor:pointer;margin:0 8px 8px 0}'
  + 'pre{background:#1e1d1b;border:1px solid #333;border-radius:10px;padding:12px;'
  + 'font-size:11.5px;line-height:1.65;color:#cfc9bf;white-space:pre-wrap;'
  + 'word-break:break-all;max-height:52vh;overflow:auto;margin:0}'
  + '.ok{color:#7fbf6a}.bad{color:#e0685f}.dim{color:#7a756c}'
  + '</style></head><body>'
  + '<h1>连不上 MONO 社区</h1>'
  + '<p class="sub">下面是诊断信息，长按可全选复制，发给管理员能直接定位问题。</p>'
  + '<div><button onclick="location.reload()">重试</button>'
  + '<button onclick="location.href=\'/\'">回首页</button>'
  + '<button onclick="go()">绕过缓存重试</button></div>'
  + '<pre id="log">正在检测…</pre>'
  + '<script>'
  + 'var out=[];'
  + 'function p(k,v,cls){out.push(\'<span class="\'+(cls||"")+\'">\'+k+\'</span>  \'+v);'
  + 'document.getElementById("log").innerHTML=out.join("\\n");}'
  + 'function go(){try{caches.keys().then(function(ks){return Promise.all('
  + 'ks.map(function(k){return caches.delete(k);}));}).then(function(){'
  + 'if(navigator.serviceWorker){navigator.serviceWorker.getRegistrations().then(function(rs){'
  + 'return Promise.all(rs.map(function(r){return r.unregister();}));});}'
  + 'return new Promise(function(r){setTimeout(r,300);});'
  + '}).then(function(){location.replace("/?_="+Date.now());});}catch(e){alert(e);}}'
  + 'p("时间", new Date().toString());'
  + 'p("页面地址", location.href);'
  + 'p("协议", location.protocol);'
  + 'if(location.protocol==="https:"){p("HTTPS","已启用 ✓","ok");}'
  + 'p("在线状态", navigator.onLine===true?"在线":"离线（浏览器判定）",'
  + 'navigator.onLine?"ok":"bad");'
  + 'p("网络类型", (navigator.connection&&navigator.connection.effectiveType)||"未知");'
  + 'p("SW 控制", navigator.serviceWorker&&navigator.serviceWorker.controller'
  + '?"已接管":"未接管（可能是首次访问或缓存被清）",'
  + 'navigator.serviceWorker&&navigator.serviceWorker.controller?"ok":"dim");'
  + 'function t0(){var s=performance.now();'
  + 'fetch("/api/mm/me?probe="+Date.now(),{cache:"no-store",credentials:"same-origin"})'
  + '.then(function(r){return r.text().then(function(x){return {s:r.status,t:x.slice(0,120)};});})'
  + '.then(function(r){'
  + 'p("接口探测 /api/mm/me", r.s+"  耗时"+Math.round(performance.now()-s)+"ms",'
  + 'r.s===200?"ok":"bad");'
  + 'if(r.t) p("返回", r.t, "dim");'
  + 'var n=new Image();var ts=performance.now();'
  + 'n.onload=function(){p("静态资源", "可加载 ✓  "+Math.round(performance.now()-ts)+"ms","ok");};'
  + 'n.onerror=function(){p("静态资源","加载失败 ✗","bad");};'
  + 'n.src="/static/icon.svg?_="+Date.now();'
  + '})'
  + '.catch(function(e){p("接口探测","失败："+e+"  耗时"+Math.round(performance.now()-s)+"ms","bad");'
  + 'p("说明","请求没到服务器，多半是 DNS / TLS / 防火墙，不是页面代码问题","dim");'
  + 'p("提示","先试「绕过缓存重试」；不行就换个网络（Wi-Fi ↔ 移动数据）","dim");});}'
  + 't0();'
  + 'setTimeout(function(){if(out.length<8){p("","如果这里只有几行且没报错，可能是脚本被 CSP 拦了","dim");}},2600);'
  + '<\/script></body></html>';

function offlinePage() {
  return new Response(OFFLINE_HTML, {
    status: 503,
    headers: { 'Content-Type': 'text/html; charset=utf-8' }
  });
}

self.addEventListener('install', function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) {
    return c.addAll(SHELL).catch(function () {}).then(function () {
      // 逐个抓，失败的跳过 —— 不能 addAll（一个失败会全丢）
      // 未登录时这些页面会 302 到 /login，用 r.redirected 排除掉，别把登录页当成游戏缓存
      return Promise.all(OFFLINE_PAGES.map(function (u) {
        return fetch(u, { credentials: 'same-origin' }).then(function (r) {
          if (r && r.ok && r.status === 200 && !r.redirected) return c.put(u, r.clone());
        }).catch(function () {});
      }));
    });
  }).then(function () { return self.skipWaiting(); }));
});

self.addEventListener('activate', function (e) {
  e.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.filter(function (k) { return k !== CACHE; })
        .map(function (k) { return caches.delete(k); }));
    }).then(function () { return self.clients.claim(); })
  );
});

// 页面把当前版本号报过来；版本一变就丢掉旧壳
self.addEventListener('message', function (e) {
  var d = e.data;
  if (!d || d.type !== 'ver' || !d.v) return;
  e.waitUntil(caches.open(CACHE).then(function (c) {
    return c.match(VER_KEY).then(function (r) { return r ? r.text() : ''; })
      .then(function (old) {
        if (old === d.v) return null;
        return c.put(VER_KEY, new Response(d.v)).then(function () {
          return old ? c.delete('/') : null;
        });
      });
  }).catch(function () {}));
});

// 在线时由页面触发：把可离线页面增量补进缓存（已有的跳过，所以后续几乎零成本）
self.addEventListener('message', function (e) {
  var d = e.data;
  if (!d || d.type !== 'warm_games') return;
  e.waitUntil(caches.open(CACHE).then(function (c) {
    return Promise.all(OFFLINE_PAGES.map(function (u) {
      return c.match(u).then(function (hit) {
        if (hit) return null;
        return fetch(u, { credentials: 'same-origin' }).then(function (r) {
          if (r && r.ok && r.status === 200 && !r.redirected) return c.put(u, r.clone());
        }).catch(function () {});
      });
    }));
  }).catch(function () {}));
});

function offlineFallback() {
  return caches.open(CACHE).then(function (c) {
    return Promise.all([
      c.match('/'),
      c.match(VER_KEY).then(function (r) { return r ? r.text() : ''; }),
      c.match(SHELLVER_KEY).then(function (r) { return r ? r.text() : ''; })
    ]).then(function (rs) {
      var shell = rs[0], cur = rs[1], shellVer = rs[2];
      if (shell && cur && shellVer && cur === shellVer) return shell;
      return offlinePage();
    });
  }).catch(function () { return offlinePage(); });
}

self.addEventListener('fetch', function (e) {
  var req = e.request;
  if (req.method !== 'GET') return;
  var url = new URL(req.url);
  if (url.origin !== location.origin) return;
  if (url.pathname.startsWith('/api/') ||
      url.pathname.startsWith('/uploads/') ||
      url.pathname.startsWith('/static/js/socket.io') ||
      url.pathname.indexOf('/socket.io/') === 0) return;
  if (url.pathname.startsWith('/music/stream/') || req.headers.has('range')) return;

  if (req.mode === 'navigate') {
    e.respondWith(
      fetch(req).then(function (res) {
        // 可离线页面：访问过就留一份，下次断网能直接打开
        if (res && res.ok && res.status === 200 && OFFLINE_PAGES.indexOf(url.pathname) >= 0) {
          var pcopy = res.clone();
          caches.open(CACHE).then(function (c) { c.put(url.pathname, pcopy); }).catch(function () {});
        }
        // 只有首页才留壳，并且打上当前版本戳
        if (res && res.ok && res.status === 200 && url.pathname === '/') {
          var copy = res.clone();
          caches.open(CACHE).then(function (c) {
            return c.match(VER_KEY).then(function (r) { return r ? r.text() : ''; })
              .then(function (v) {
                if (!v) return null;
                return c.put(SHELLVER_KEY, new Response(v)).then(function () {
                  return c.put('/', copy);
                });
              });
          }).catch(function () {});
        }
        return res;
      }).catch(function () {
        // 断网：优先用这个页面自己的缓存（游戏页离线可玩），再退回首页壳 / 离线页
        return caches.match(req).then(function (hit) {
          return hit || offlineFallback();
        });
      })
    );
    return;
  }

  e.respondWith(
    caches.match(req).then(function (hit) {
      return hit || fetch(req).then(function (res) {
        if (res && res.ok && res.status === 200) {
          var copy = res.clone();
          caches.open(CACHE).then(function (c) { c.put(req, copy); });
        }
        return res;
      }).catch(function () { return hit; });
    })
  );
});
