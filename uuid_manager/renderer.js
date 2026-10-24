const dot = document.getElementById('dot');
const statusText = document.getElementById('status-text');
const statusPage = document.getElementById('status-page');
const statusPanel = document.getElementById('status-panel');
const statusToggle = document.getElementById('status-toggle');
const statusIndicator = document.getElementById('status-indicator');
const btnNew = document.getElementById('btn-new');
const btnBackfill = document.getElementById('btn-backfill');
const activeTitle = document.getElementById('active-title');
const activeBadge = document.getElementById('active-badge');
const btnUnlock = document.getElementById('btn-unlock');
const listEl = document.getElementById('list');
const logContainer = document.getElementById('log-container');

statusToggle.addEventListener('click', () => {
  const isCollapsed = statusPanel.classList.contains('collapsed');
  if (isCollapsed) { statusPanel.classList.remove('collapsed'); statusIndicator.textContent = '系统状态 ▲'; }
  else { statusPanel.classList.add('collapsed'); statusIndicator.textContent = '系统状态 ▼'; }
});

document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', (e) => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    e.target.classList.add('active');
    const targetId = e.target.getAttribute('data-target');
    document.querySelectorAll('.main-content').forEach(m => m.style.display = 'none');
    document.getElementById(targetId).style.display = 'flex';
  });
});

document.querySelectorAll('.toggle-title').forEach(title => {
  title.addEventListener('click', () => {
    const targetId = title.getAttribute('data-target');
    const content = document.getElementById(targetId);
    const arrow = title.querySelector('.arrow');
    if (content.classList.contains('open')) { content.classList.remove('open'); content.classList.add('closed'); arrow.style.transform = 'rotate(-90deg)'; }
    else { content.classList.remove('closed'); content.classList.add('open'); arrow.style.transform = 'rotate(0deg)'; }
  });
});

btnNew.addEventListener('click', () => {
  btnNew.textContent = '正在初始化...';
  btnNew.disabled = true;
  window.api.newSession().then(() => { setTimeout(() => { btnNew.textContent = '立即初始化'; btnNew.disabled = false; }, 3000); });
});

if (btnBackfill) {
  btnBackfill.addEventListener('click', () => {
    btnBackfill.disabled = true;
    btnBackfill.textContent = '回填中...';
    window.api.backfillTitles().then(() => { btnBackfill.disabled = false; btnBackfill.textContent = '回填标题'; });
  });
}

btnUnlock.addEventListener('click', () => { window.api.unlockUuid(); });

const AMP = String.fromCharCode(38);
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    if (c === AMP) return AMP + 'amp;';
    if (c === '<') return AMP + 'lt;';
    if (c === '>') return AMP + 'gt;';
    if (c === '"') return AMP + 'quot;';
    if (c === "'") return AMP + '#39;';
    return c;
  });
}

function render(data) {
  if (!data || !data.connected) {
    dot.className = 'status-dot';
    statusText.textContent = '未连接';
    statusText.style.color = 'var(--danger)';
    statusPage.textContent = data ? (data.error || '请确认 9222 端口已开启') : '等待中';
    btnNew.disabled = true;
    
    if (data && data.stateData) {
      activeTitle.textContent = data.currentUuid ? '已锁定（未连接）' : '等待连接...';
      if (data.currentUuid) {
        activeBadge.style.display = 'inline-block';
        btnUnlock.style.display = 'inline-block';
      } else {
        activeBadge.style.display = 'none';
        btnUnlock.style.display = 'none';
      }
      renderList(data.stateData, data.currentUuid);
    } else {
      listEl.innerHTML = '';
    }
    return;
  }
  
  dot.className = 'status-dot online';
  statusText.textContent = '已连接';
  statusText.style.color = 'var(--success)';
  statusPage.textContent = data.currentTitle || '未知';
  btnNew.disabled = false;
  if (data.currentTitle) { activeTitle.textContent = data.currentTitle; }
  else if (data.currentUuid) { activeTitle.textContent = '已锁定（未打开页面）'; }
  else { activeTitle.textContent = '自由模式（网页决定）'; }
  
  if (data.currentUuid) { activeBadge.style.display = 'inline-block'; btnUnlock.style.display = 'inline-block'; }
  else { activeBadge.style.display = 'none'; btnUnlock.style.display = 'none'; }

  renderList(data.stateData || {}, data.currentUuid, data.pageUuid, data.currentTitle);
}

function renderList(stateData, currentUuid, pageUuid, currentTitle) {
  const map = new Map();
  Object.keys(stateData).forEach(function (k) {
    if (k === '__current__') return;
    const s = stateData[k];
    if (!s || !s.uuid) return;
    const existing = map.get(s.uuid);
    if (existing) {
      existing.count = Math.max(existing.count, s.count || 0);
      if (s.title && s.title !== '历史会话' && s.title !== '当前网页会话') existing.title = s.title;
      existing.lastUsed = Math.max(existing.lastUsed, s.last_used || 0);
    } else {
      map.set(s.uuid, { uuid: s.uuid, title: s.title || '历史会话', count: s.count || 0, lastUsed: s.last_used || 0 });
    }
  });
  if (pageUuid) {
    const ex = map.get(pageUuid);
    if (ex) { ex.title = currentTitle || ex.title; }
    else { map.set(pageUuid, { uuid: pageUuid, title: currentTitle || '当前网页会话', count: 0, lastUsed: Math.floor(Date.now() / 1000) }); }
  }
  const arr = Array.from(map.values()).sort((a, b) => b.lastUsed - a.lastUsed);
  if (arr.length === 0) { listEl.innerHTML = '<div class="empty">暂无历史记录</div>'; return; }
  
  listEl.innerHTML = arr.map(function (it) {
    const locked = (it.uuid === currentUuid);
    const btnClass = locked ? 'btn' : 'btn primary';
    const disabled = locked ? 'disabled' : '';
    return '<div class="session-item">' +
      '<div>' +
      '<h4>' + esc(it.title) + '</h4>' +
      '<p>' + '次数: ' + it.count + (locked ? ' | <span style="color:var(--accent);font-weight:700">已锁定</span>' : '') + '</p>' +
      '</div>' +
      '<button class="' + btnClass + '" data-lock="' + esc(it.uuid) + '" ' + disabled + '>锁定</button>' +
      '<button class="btn danger" data-del="' + esc(it.uuid) + '">删除</button>' +
      '</div>';
  }).join('');
}

listEl.addEventListener('click', function (ev) {
  const lockU = ev.target.getAttribute && ev.target.getAttribute('data-lock');
  const delU = ev.target.getAttribute && ev.target.getAttribute('data-del');
  if (lockU) { window.api.lockUuid(lockU); }
  if (delU) { if (confirm('确认从历史删除该会话？')) window.api.deleteSession(delU); }
});

window.api.onStateUpdate(render);
window.api.onLogUpdate(function (data) {
  const div = document.createElement('div');
  div.className = 'log-line ' + data.type;
  div.textContent = '[' + data.time + '] ' + data.msg;
  logContainer.appendChild(div);
  logContainer.scrollTop = logContainer.scrollHeight;
  if (logContainer.childNodes.length > 200) { logContainer.removeChild(logContainer.firstChild); }
});
window.api.onTriggerNewSession(function () { if (!btnNew.disabled) btnNew.click(); });
