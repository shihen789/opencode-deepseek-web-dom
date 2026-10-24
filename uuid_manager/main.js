const { app, BrowserWindow, ipcMain, Menu, Tray, nativeImage } = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');
const http = require('http');
const { WebSocket } = require('ws');

const STATE_PATH = 'C:/Users/39841/Documents/Default Project/网页AI/opencode-provider/state/session_map.json';
const PROMPT_LOG_PATH = 'C:/Users/39841/Documents/Default Project/网页AI/opencode-provider/logs/last_prompt.txt';
const MAX_HISTORY = 10;
const HISTORY_BUDGET = 30000;

let mainWindow = null;
let ws = null;
let msgId = 1;
let pending = new Map();
let pollTimer = null;
let capturing = false;
let connState = 'init';
let connFailReason = '';
let connFailLogged = 0;
let tray = null;
let isQuitting = false;
let backfillRunning = false;

function sendLog(type, msg) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    const time = new Date().toLocaleTimeString();
    mainWindow.webContents.send('log-update', { time, type, msg });
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 960,
    height: 780,
    title: 'Session Manager',
    backgroundColor: '#05070f',
    frame: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false
    }
  });
  mainWindow.on('close', (e) => {
    if (!isQuitting) { e.preventDefault(); mainWindow.hide(); }
  });
  mainWindow.loadFile('index.html');
  Menu.setApplicationMenu(null);
}

function createTray() {
  const iconPath = path.join(__dirname, 'icon.png');
  let img = nativeImage.createFromPath(iconPath);
  if (img.isEmpty()) img = nativeImage.createEmpty();
  tray = new Tray(img);
  tray.setToolTip('UUID 光核矩阵');
  const menu = Menu.buildFromTemplate([
    { label: '显示窗口', click: () => { if (mainWindow) { mainWindow.show(); mainWindow.focus(); } } },
    { label: '立即初始化', click: () => { if (mainWindow) { mainWindow.show(); mainWindow.webContents.send('trigger-new-session'); } } },
    { type: 'separator' },
    { label: '退出', click: () => { isQuitting = true; app.quit(); } }
  ]);
  tray.setContextMenu(menu);
  tray.on('click', () => {
    if (!mainWindow) return;
    if (mainWindow.isVisible()) mainWindow.hide();
    else { mainWindow.show(); mainWindow.focus(); }
  });
}

function getCdpPages() {
  return new Promise((resolve) => {
    const req = http.get('http://127.0.0.1:9222/json/list', (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => { try { resolve(JSON.parse(d)); } catch (e) { resolve([]); } });
    });
    req.on('error', () => resolve([]));
    req.setTimeout(1200, () => { req.destroy(); resolve([]); });
  });
}

function sendCmd(method, params) {
  return new Promise((resolve, reject) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) { reject(new Error('WS 未连接')); return; }
    const id = msgId++;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
    setTimeout(() => {
      if (pending.has(id)) { pending.delete(id); reject(new Error('CDP 超时')); }
    }, 5000);
  });
}

async function cdpEval(expression) {
  const r = await sendCmd('Runtime.evaluate', { expression, returnByValue: true });
  return r && r.result ? r.result.value : undefined;
}

async function cdpGoto(url) {
  await sendCmd('Page.navigate', { url });
}

function failConn(reason) {
  connFailReason = reason;
  const now = Date.now();
  if (connState !== 'disconnected' || now - connFailLogged > 15000) {
    sendLog('error', 'CDP 连接失败: ' + reason + '（每 1.5s 重试中）');
    connFailLogged = now;
  }
  connState = 'disconnected';
  return false;
}

async function ensureConnected() {
  if (ws && ws.readyState === WebSocket.OPEN) { return true; }
  const pages = await getCdpPages();
  if (!pages || pages.length === 0) { return failConn('端口 9222 无响应'); }
  const ds = pages.find((p) => (p.url || '').includes('chat.deepseek.com'));
  if (!ds) { return failConn('9222 已连但未找到 chat.deepseek.com 页面'); }
  if (!ds.webSocketDebuggerUrl) { return failConn('页面缺少 webSocketDebuggerUrl'); }
  if (ws) { try { ws.close(); } catch (e) {} ws = null; }
  const ok = await new Promise((resolve) => {
    let done = false;
    const finish = (v) => { if (!done) { done = true; resolve(v); } };
    try { ws = new WebSocket(ds.webSocketDebuggerUrl); } catch (e) { finish(false); return; }
    ws.on('open', async () => {
      try {
        await sendCmd('Runtime.enable', {});
        await sendCmd('Page.enable', {});
        await sendCmd('DOM.enable', {});
        finish(true);
      } catch (e) { finish(false); }
    });
    ws.on('message', (d) => {
      try {
        const m = JSON.parse(d);
        if (m.id && pending.has(m.id)) {
          const p = pending.get(m.id);
          pending.delete(m.id);
          if (m.error) { p.reject(new Error(m.error.message)); } else { p.resolve(m.result); }
        }
      } catch (e) {}
    });
    ws.on('close', () => { ws = null; finish(false); });
    ws.on('error', () => { ws = null; finish(false); });
    setTimeout(() => finish(false), 3000);
  });
  if (ok) {
    if (connState !== 'connected') sendLog('success', 'CDP 已连接');
    connState = 'connected';
    connFailReason = '';
    return true;
  }
  return failConn('WebSocket 握手失败');
}

function readSessionMap() {
  try {
    if (fs.existsSync(STATE_PATH)) {
      return JSON.parse(fs.readFileSync(STATE_PATH, 'utf-8'));
    }
  } catch (e) {}
  return {};
}

function writeSessionMap(data) {
  try {
    fs.writeFileSync(STATE_PATH, JSON.stringify(data, null, 2), 'utf-8');
  } catch (e) {
    sendLog('error', '写入 session_map.json 失败: ' + e.message);
  }
}

function touchSession(uuid, opts) {
  opts = opts || {};
  if (!uuid) return null;
  const sm = readSessionMap();
  const now = Math.floor(Date.now() / 1000);
  const prev = (sm[uuid] && typeof sm[uuid] === 'object') ? sm[uuid] : {};
  const entry = {
    uuid: uuid,
    last_used: now,
    count: (Number(prev.count) || 0) + (opts.bumpCount ? 1 : 0)
  };
  if (prev.title) entry.title = prev.title;
  if (opts.title) entry.title = opts.title;
  sm[uuid] = entry;
  const protectedUuids = new Set([uuid, (sm['__current__'] || {}).uuid]);
  const rest = Object.keys(sm)
    .filter(k => k !== '__current__' && !protectedUuids.has(k))
    .sort((a, b) => ((sm[b].last_used || 0) - (sm[a].last_used || 0)));
  const keepSlots = Math.max(0, MAX_HISTORY - protectedUuids.size);
  rest.slice(keepSlots).forEach(k => { delete sm[k]; });
  writeSessionMap(sm);
  return entry;
}

function getCurrentUuid() {
  const sm = readSessionMap();
  return (sm['__current__'] || {}).uuid || null;
}

function cleanTitle(raw) {
  let t = String(raw == null ? '' : raw).trim();
  const suffixes = [' - DeepSeek', '- DeepSeek', ' - DeepSeek Chat'];
  for (const s of suffixes) {
    if (t.toLowerCase().endsWith(s.toLowerCase())) { t = t.slice(0, -s.length).trim(); break; }
  }
  if (!t || t === 'DeepSeek') t = '新对话';
  return t;
}

function findFormatSpecPath() {
  const home = os.homedir();
  const candidates = [
    path.join(home, 'Desktop', 'Web-Tool-Call-Format-Spec.md'),
    path.join(home, 'Desktop', 'Web-Tool-Call-Format-Spec.txt'),
    'C:/Users/39841/Desktop/Web-Tool-Call-Format-Spec.md'
  ];
  for (const p of candidates) {
    try { if (fs.existsSync(p)) return p; } catch (e) {}
  }
  return null;
}

async function waitForExpr(expr, timeoutMs, interval) {
  timeoutMs = timeoutMs || 15000;
  interval = interval || 300;
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try { if (await cdpEval(expr)) return true; } catch (e) {}
    await new Promise(r => setTimeout(r, interval));
  }
  return false;
}

async function findFileInputNodeId() {
  const doc = await sendCmd('DOM.getDocument', { depth: -1 });
  let q = await sendCmd('DOM.querySelector', { nodeId: doc.root.nodeId, selector: 'input[type=file]' });
  if (q && q.nodeId) return q.nodeId;
  await cdpEval(`(function(){var els=[].slice.call(document.querySelectorAll('button,[role=button],label'));var up=els.find(function(b){return /上传|附件|attach|upload|file/i.test((b.getAttribute('aria-label')||'')+(b.title||'')+(b.textContent||''));});if(up){up.click();return true;}return false;})()`);
  await new Promise(r => setTimeout(r, 600));
  const doc2 = await sendCmd('DOM.getDocument', { depth: -1 });
  q = await sendCmd('DOM.querySelector', { nodeId: doc2.root.nodeId, selector: 'input[type=file]' });
  return q && q.nodeId ? q.nodeId : null;
}

async function uploadFiles(files) {
  const nodeId = await findFileInputNodeId();
  if (!nodeId) throw new Error('未找到文件上传 input');
  await sendCmd('DOM.setFileInputFiles', { files: files, nodeId: nodeId });
}

function buildHistoryFile() {
  if (!fs.existsSync(PROMPT_LOG_PATH)) return null;
  const raw = fs.readFileSync(PROMPT_LOG_PATH, 'utf-8');
  let body = raw;
  if (body.length > HISTORY_BUDGET) {
    const head = body.slice(0, 4000);
    const tail = body.slice(body.length - (HISTORY_BUDGET - 4000));
    body = head + '\n\n[... 中间早期对话已压缩省略 ...]\n\n' + tail;
  }
  const header = '# 历史对话上下文（自动压缩）\n生成时间: ' + new Date().toISOString() + '\n原始长度: ' + raw.length + ' 字符 / 保留: ' + body.length + ' 字符\n\n---\n\n';
  const out = path.join(os.tmpdir(), 'uuid-manager-history-' + Date.now() + '.md');
  fs.writeFileSync(out, header + body, 'utf-8');
  return out;
}

async function tick() {
  const ok = await ensureConnected();
  if (!ok) {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('state-update', { connected: false, error: connFailReason });
    }
    return;
  }
  try {
    const info = await cdpEval('({href: location.href, title: document.title})');
    if (!info) throw new Error('无返回');
    const href = String(info.href || '');
    const title = cleanTitle(info.title);
    const marker = '/a/chat/s/';
    const idx = href.indexOf(marker);
    const pageUuid = idx >= 0 ? href.slice(idx + marker.length).split(/[?#]/)[0] : null;
    let currentUuid = getCurrentUuid();
    if (capturing && pageUuid) {
      const sm = readSessionMap();
      sm['__current__'] = { uuid: pageUuid, last_used: Math.floor(Date.now() / 1000) };
      writeSessionMap(sm);
      touchSession(pageUuid, { bumpCount: true, title: title });
      currentUuid = pageUuid;
      capturing = false;
      sendLog('success', '已捕获并锁定新会话: ' + pageUuid.substring(0, 8));
    }
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('state-update', {
        connected: true,
        currentUuid: currentUuid,
        pageUuid: pageUuid,
        currentTitle: title,
        stateData: readSessionMap()
      });
    }
  } catch (e) {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('state-update', { connected: false, error: e.message });
    }
  }
}

async function backfillTitles() {
  if (backfillRunning) { sendLog('info', '标题回填已在进行中'); return { ok: false, error: 'busy' }; }
  backfillRunning = true;
  try {
    if (!(await ensureConnected())) throw new Error('浏览器未连接');
    const originHref = await cdpEval('location.href');
    const sm = readSessionMap();
    const targets = Object.keys(sm).filter(function (k) {
      if (k === '__current__') return false;
      const s = sm[k];
      if (!s || !s.uuid) return false;
      const t = s.title;
      return !t || t === '历史会话' || t === '当前网页会话';
    });
    if (targets.length === 0) { sendLog('info', '无需回填，所有会话已有标题'); return { ok: true, updated: 0 }; }
    sendLog('info', '开始回填 ' + targets.length + ' 个会话标题...');
    let updated = 0;
    for (const uuid of targets) {
      try {
        await cdpGoto('https://chat.deepseek.com/a/chat/s/' + uuid);
        await waitForExpr(`document.readyState === 'complete' && !!document.title`, 12000);
        await new Promise(r => setTimeout(r, 800));
        const t = await cdpEval('document.title');
        const cleaned = cleanTitle(t);
        if (cleaned && cleaned !== '新对话') {
          const cur = readSessionMap();
          if (cur[uuid]) {
            cur[uuid].title = cleaned;
            writeSessionMap(cur);
            updated++;
            sendLog('success', '回填 ' + uuid.substring(0, 8) + ' -> ' + cleaned);
          }
        }
      } catch (e) {
        sendLog('error', '回填 ' + uuid.substring(0, 8) + ' 失败: ' + e.message);
      }
    }
    if (originHref) { try { await cdpGoto(originHref); } catch (e) {} }
    sendLog('success', '标题回填完成，更新 ' + updated + ' 条');
    tick();
    return { ok: true, updated: updated };
  } catch (err) {
    sendLog('error', '回填失败: ' + err.message);
    return { ok: false, error: err.message };
  } finally {
    backfillRunning = false;
  }
}

app.whenReady().then(() => {
  createWindow();
  try { createTray(); } catch (e) { sendLog('error', '托盘初始化失败: ' + e.message); }
  try { app.setLoginItemSettings({ openAtLogin: true, path: process.execPath }); } catch (e) { sendLog('error', '开机自启设置失败: ' + e.message); }
  pollTimer = setInterval(() => { if (mainWindow && !mainWindow.isDestroyed()) tick(); }, 1500);
  setTimeout(tick, 500);
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
});

app.on('window-all-closed', () => {
});

ipcMain.handle('lock', async (e, uuid) => {
  try {
    sendLog('info', '收到锁定请求: ' + uuid);
    capturing = false;
    const sm = readSessionMap();
    sm['__current__'] = { uuid: uuid, last_used: Math.floor(Date.now() / 1000) };
    writeSessionMap(sm);
    touchSession(uuid, { bumpCount: true });
    sendLog('success', '已锁定会话并写入 JSON: ' + uuid.substring(0, 8));
    
    sendLog('info', '准备推送 UI 刷新事件...');
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('state-update', {
        connected: connState === 'connected',
        currentUuid: getCurrentUuid(),
        currentTitle: '锁定完成',
        stateData: sm
      });
    }
    
    if (connState === 'connected') {
      const href = await cdpEval('location.href');
      if (!String(href || '').includes(uuid)) {
        sendLog('info', '浏览器正在跳转至目标会话...');
        await cdpGoto('https://chat.deepseek.com/a/chat/s/' + uuid);
      }
      tick();
    }
    return { ok: true };
  } catch (err) {
    sendLog('error', '锁定失败: ' + err.message);
    return { ok: false, error: err.message };
  }
});

ipcMain.handle('unlock', async () => {
  try {
    capturing = false;
    const sm = readSessionMap();
    delete sm['__current__'];
    writeSessionMap(sm);
    sendLog('info', '已解除会话锁定');
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('state-update', { connected: connState === 'connected', currentUuid: getCurrentUuid(), currentTitle: wasCurrent ? '已删除' : '未加载', stateData: sm });
    }
    setTimeout(() => { if (connState === 'connected') tick(); }, 500);
    return { ok: true };
  } catch (err) {
    sendLog('error', '删除失败: ' + err.message);
    return { ok: false, error: err.message };
  }
});

ipcMain.handle('delete-session', async (e, uuid) => {
  try {
    sendLog('info', '收到删除请求: ' + uuid);
    if (!uuid) return { ok: false, error: 'empty uuid' };
    capturing = false;
    const sm = readSessionMap();
    let needLeave = false;
    let deletedCount = 0;

    // 【核心修复】：遍历所有 key，揪出所有包含此 uuid 的幽灵记录
    for (const key of Object.keys(sm)) {
      if (key === uuid || (sm[key] && sm[key].uuid === uuid)) {
        delete sm[key];
        deletedCount++;
        if (key === '__current__' || (sm[key] && sm[key].uuid === uuid && key === '__current__')) {
          needLeave = true;
        }
      }
    }
    
    // 如果 __current__ 也是它，需要解除锁定并离开页面
    if (sm['__current__'] && sm['__current__'].uuid === uuid) {
        delete sm['__current__'];
        needLeave = true;
    }

    writeSessionMap(sm);
    sendLog('info', `清除了 ${deletedCount} 条关联记录`);

    if (connState === 'connected') {
      try {
        const href = await cdpEval('location.href');
        if (String(href || '').includes(uuid)) needLeave = true;
      } catch (e) {}
    }

    if (needLeave && connState === 'connected') {
      sendLog('info', '正在强制浏览器跳回首页...');
      await cdpGoto('https://chat.deepseek.com/');
      await new Promise(r => setTimeout(r, 1000));
    }

    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('state-update', {
        connected: connState === 'connected',
        currentUuid: getCurrentUuid(),
        pageUuid: needLeave ? null : undefined,
        currentTitle: needLeave ? '未加载' : undefined,
        stateData: sm
      });
      sendLog('success', '已发送 UI 刷新数据');
    }

    if (connState === 'connected') {
      setTimeout(() => tick(), 500);
    }
    return { ok: true };
  } catch (err) {
    sendLog('error', '删除失败: ' + err.message);
    return { ok: false, error: err.message };
  }
});

ipcMain.handle('backfill-titles', async () => await backfillTitles());

ipcMain.handle('new-session', async () => {
  try {
    if (!(await ensureConnected())) {
      sendLog('error', '浏览器未连接，无法初始化');
      return { ok: false, error: 'not connected' };
    }
    const sm = readSessionMap();
    delete sm['__current__'];
    writeSessionMap(sm);
    sendLog('info', '正在开启新会话...');

    const specPath = findFormatSpecPath();
    if (!specPath) { sendLog('error', '未找到格式规范文件'); return { ok: false, error: 'format spec not found' }; }
    const historyPath = buildHistoryFile();
    const files = [specPath];
    if (historyPath) files.push(historyPath);
    sendLog('info', '待上传文件: ' + files.map(function (f) { return path.basename(f); }).join(', '));

    await cdpGoto('https://chat.deepseek.com/');
    const ready = await waitForExpr(`!!document.querySelector('textarea,[contenteditable]')`, 15000);
    if (!ready) throw new Error('页面输入框未就绪');

    await uploadFiles(files);
    sendLog('info', '文件已注入，等待附件就绪...');
    await new Promise(r => setTimeout(r, 1500));

    const clicked = await cdpEval(`(function(){var ta=document.querySelector('textarea');if(ta&&!ta.value){var v='【系统前置初始化】已上传格式规范与历史对话文件，请阅读并严格遵守。';var desc=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value');desc.set.call(ta,v);try{if(ta._valueTracker)ta._valueTracker.setValue('__f_'+Math.random());}catch(e){}ta.dispatchEvent(new Event('input',{bubbles:true}));}var root=ta?(ta.closest('form')||ta.parentElement.parentElement):document.body;var btns=[].slice.call(root.querySelectorAll('button,[role=button]')).reverse();var send=btns.find(function(b){return /发送|send/i.test((b.getAttribute('aria-label')||'')+(b.title||''));});if(send){send.click();return 'clicked-aria';}return 'no-send-button';})()`);
    sendLog('info', '发送动作: ' + clicked);

    const sent = await waitForExpr(`(function(){var ta=document.querySelector('textarea');return !ta || !ta.value || ta.value.length===0;})()`, 20000);
    sendLog(sent ? 'success' : 'error', sent ? '消息已发出，等待 UUID...' : '发送可能失败，请手动检查网页');

    capturing = true;
    tick();
    return { ok: true };
  } catch (err) {
    sendLog('error', '开启新会话失败: ' + err.message);
    return { ok: false, error: err.message };
  }
});

ipcMain.on('window-minimize', (e) => { const win = BrowserWindow.fromWebContents(e.sender); if (win) win.minimize(); });
ipcMain.on('window-close', (e) => { const win = BrowserWindow.fromWebContents(e.sender); if (win) win.close(); });
