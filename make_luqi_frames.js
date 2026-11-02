const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const CHROME = 'C:/Users/weiha/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe';
const PAGE = 'file:///' + encodeURI('E:/9.2/文件夹/test6/static/luqi/splash.html').replace(/^file:\/\/\//, '');
const OUT = 'C:/Users/weiha/AppData/Local/Temp/luqi_frames';
const PORT = 9333;
const W = 460, H = 620;
const FPS = 20, DURATION = 4.6;

fs.rmSync(OUT, { recursive: true, force: true });
fs.mkdirSync(OUT, { recursive: true });

const chrome = spawn(CHROME, [
  '--headless=new', '--no-sandbox', '--disable-gpu', '--hide-scrollbars',
  '--remote-debugging-port=' + PORT,
  '--window-size=' + W + ',' + H,
  '--user-data-dir=' + OUT + '/profile',
  'about:blank'
], { stdio: 'ignore' });

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function main() {
  let targets = null;
  for (let i = 0; i < 40; i++) {
    try {
      const r = await fetch('http://127.0.0.1:' + PORT + '/json/list');
      targets = await r.json();
      if (targets.some(t => t.type === 'page')) break;
    } catch (e) {}
    await sleep(300);
  }
  const page = targets.find(t => t.type === 'page');
  if (!page) throw new Error('没找到页面目标');

  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

  let id = 0;
  const waiting = new Map();
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && waiting.has(m.id)) { waiting.get(m.id)(m); waiting.delete(m.id); }
  };
  const send = (method, params) => new Promise((res) => {
    const mid = ++id;
    waiting.set(mid, res);
    ws.send(JSON.stringify({ id: mid, method, params: params || {} }));
  });

  await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride',
    { width: W, height: H, deviceScaleFactor: 2, mobile: false });
  await send('Page.navigate', { url: PAGE });
  await sleep(1800);

  const info = await send('Runtime.evaluate', {
    expression: 'document.getAnimations().length', returnByValue: true
  });
  console.log('页面上有', info.result.result.value, '个动画');

  const total = Math.round(FPS * DURATION);
  for (let f = 0; f <= total; f++) {
    const t = (f / FPS) * 1000;
    await send('Runtime.evaluate', {
      expression: `document.getAnimations().forEach(function(a){a.pause();a.currentTime=${t};})`,
      returnByValue: true
    });
    await sleep(35);
    const shot = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(path.join(OUT, 'f' + String(f).padStart(4, '0') + '.png'),
                     Buffer.from(shot.result.data, 'base64'));
  }
  console.log('截了', total + 1, '帧 ->', OUT);

  ws.close();
  chrome.kill();
  process.exit(0);
}

main().catch(e => { console.error('出错:', e.message); chrome.kill(); process.exit(1); });
