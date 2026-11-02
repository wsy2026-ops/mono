/* 本地截图工具（站点内容在 #appFrame iframe 里，所有操作都要进 frame）
 *
 * 直接跑这个文件需要先准备会话 cookie；一般用配套的 shot_page.py 更方便：
 *   python shot_page.py /game/xqmaster/codex 输出名 [宽] [高] [full] [点击选择器] [再点选择器]
 *
 * 环境变量：
 *   SHOT_COOKIE  会话 cookie（"session=xxx"），由 shot_page.py 生成
 *   SHOT_EVAL    截图前在 iframe 里执行的 JS（例如强制切横屏态）
 */
const { spawn } = require('child_process');
const fs = require('fs');

const CHROME_CANDIDATES = [
  'C:/Users/weiha/AppData/Local/ms-playwright/chromium-1234/chrome-win64/chrome.exe',
  'C:/Users/weiha/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe',
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
];
const CHROME = CHROME_CANDIDATES.find(p => fs.existsSync(p));
if (!CHROME) { console.error('找不到可用的 Chromium/Chrome'); process.exit(1); }

const ROOT = __dirname.replace(/\\/g, '/');
const PORT = 9447;
const OUT = ROOT + '/_shots';
const PATH_ = process.argv[2] || '/game/xqmaster/codex';
const NAME = process.argv[3] || 'shot';
const W = parseInt(process.argv[4] || '1280', 10);
const H = parseInt(process.argv[5] || '900', 10);
const FULL = process.argv[6] === 'full';
const SEL1 = process.argv[7] || '';
const SEL2 = process.argv[8] || '';
const COOKIE = (process.env.SHOT_COOKIE || '').trim();
if (!COOKIE) { console.error('缺 SHOT_COOKIE（用 shot_page.py 跑）'); process.exit(1); }

fs.mkdirSync(OUT, { recursive: true });
const PROFILE = 'C:/Users/weiha/AppData/Local/Temp/_shot_profile';
fs.rmSync(PROFILE, { recursive: true, force: true });

const chrome = spawn(CHROME, [
  '--headless=new', '--no-sandbox', '--disable-gpu', '--hide-scrollbars',
  '--remote-debugging-port=' + PORT,
  '--window-size=' + W + ',' + H,
  '--user-data-dir=' + PROFILE,
  'about:blank'
], { stdio: 'ignore' });
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

/* 在 iframe 里求值的表达式包装 */
const inFrame = (expr) => `(function(){
  var f = document.getElementById('appFrame');
  var d = (f && f.contentDocument) ? f.contentDocument : document;
  return (function(){ ${expr} })();
})()`;

async function main() {
  let page = null;
  for (let i = 0; i < 50; i++) {
    try {
      const r = await fetch('http://127.0.0.1:' + PORT + '/json/list');
      page = (await r.json()).find(t => t.type === 'page');
      if (page) break;
    } catch (e) {}
    await sleep(300);
  }
  if (!page) throw new Error('没找到页面目标');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

  let id = 0; const waiting = new Map(); const logs = [];
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && waiting.has(m.id)) { waiting.get(m.id)(m); waiting.delete(m.id); return; }
    if (m.method === 'Runtime.consoleAPICalled')
      logs.push('[console] ' + (m.params.args || []).map(a => a.value).join(' '));
    if (m.method === 'Runtime.exceptionThrown') {
      const d = m.params.exceptionDetails;
      logs.push('[ERROR] ' + ((d.exception && d.exception.description) || d.text));
    }
  };
  const send = (method, params) => new Promise((res) => {
    const mid = ++id; waiting.set(mid, res);
    ws.send(JSON.stringify({ id: mid, method, params: params || {} }));
  });
  const evalIn = (expr) => send('Runtime.evaluate',
    { expression: inFrame(expr), returnByValue: true }).then(r => r.result.result.value);

  await send('Page.enable'); await send('Runtime.enable'); await send('Network.enable');
  await send('Emulation.setDeviceMetricsOverride',
    { width: W, height: H, deviceScaleFactor: 2, mobile: W < 700 });
  const kv = COOKIE.split('=');
  await send('Network.setCookie', { name: kv[0], value: kv.slice(1).join('='),
    domain: '127.0.0.1', path: '/', httpOnly: true });
  await send('Page.navigate', { url: 'http://127.0.0.1:5000' + PATH_ });
  await sleep(4000);

  for (const sel of [SEL1, SEL2]) {
    if (!sel) continue;
    const r = await evalIn('var el = d.querySelector(' + JSON.stringify(sel) +
      '); if(!el) return "not found"; el.click(); return "clicked";');
    console.log('点击 ' + sel + ': ' + r);
    await sleep(2200);
  }

  // 把棋盘滚进视野（移动端棋盘在折叠下方）
  await evalIn(`
    var m = d.querySelector('.xqm-mb') || d.querySelector('.xqm-demo');
    if (m) m.scrollIntoView({block: 'center'});
    return m ? 'scrolled' : 'no board';
  `).then(r => console.log('滚动: ' + r));
  await sleep(600);

  // 额外 JS（比如强制切到横屏态）
  if (process.env.SHOT_EVAL) {
    const r = await evalIn(process.env.SHOT_EVAL);
    console.log('额外 JS: ' + r);
    await sleep(1200);
  }

  // 棋盘几何诊断
  const geo = await evalIn(`
    var out = {};
    var g = d.querySelector('.xqm-mb-grid');
    if (g) { var cs = getComputedStyle(g), r = g.getBoundingClientRect();
      out.grid = { cols: cs.gridTemplateColumns, rows: cs.gridTemplateRows,
                   gap: cs.gap, w: +r.width.toFixed(1), h: +r.height.toFixed(1) }; }
    var mb = d.querySelector('.xqm-mb');
    if (mb) { var cs2 = getComputedStyle(mb), r2 = mb.getBoundingClientRect();
      out.mb = { w: +r2.width.toFixed(1), h: +r2.height.toFixed(1), aspect: cs2.aspectRatio,
                 pad: cs2.padding, pos: cs2.position, box: cs2.boxSizing }; }
    var cells = d.querySelectorAll('.xqm-mb-grid > *');
    out.cells = cells.length;
    if (cells.length) { var c0 = cells[0].getBoundingClientRect();
      var c1 = cells[1] ? cells[1].getBoundingClientRect() : null;
      out.cell0 = { w: +c0.width.toFixed(2), h: +c0.height.toFixed(2), x: +c0.x.toFixed(1), y: +c0.y.toFixed(1) };
      if (c1) out.cell1 = { x: +c1.x.toFixed(1), y: +c1.y.toFixed(1) }; }
    return JSON.stringify(out);
  `);
  console.log('棋盘诊断: ' + geo);

  const shot = await send('Page.captureScreenshot',
    FULL ? { format: 'png', captureBeyondViewport: true } : { format: 'png' });
  fs.writeFileSync(OUT + '/' + NAME + '.png', Buffer.from(shot.result.data, 'base64'));
  console.log('已保存: ' + OUT + '/' + NAME + '.png');
  if (logs.length) console.log('页面日志:\n  ' + logs.slice(0, 15).join('\n  '));
  else console.log('页面无报错');

  await send('Browser.close').catch(() => {});
  chrome.kill(); process.exit(0);
}
main().catch(e => { console.error('失败: ' + e.message); chrome.kill(); process.exit(1); });
