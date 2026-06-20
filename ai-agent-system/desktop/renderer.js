/**
 * Agent Desktop Workbench — 渲染进程 UI 逻辑
 * 依赖: i18n.js (提供 t(), setLang(), refreshAllUI())
 */
var API_BASE = 'http://localhost:8000';
var state = {
  tasks: {},
  taskOrder: [],
  selectedTaskId: null,
  ws: null,
  wsReconnectAttempts: 0,
  refreshTimer: null,
  lastWsUpdate: {},
  _wsPath: null,
  _connStatus: 'disconnected',
};

// ═══════════════════════════════════════════════════════
// Section 1: API helpers
// ═══════════════════════════════════════════════════════

async function apiGet(path) {
  try {
    var resp = await fetch(API_BASE + path);
    if (!resp.ok) { console.warn('API GET ' + path + ' -> ' + resp.status); return null; }
    return await resp.json();
  } catch (err) {
    console.warn('API GET error: ' + path + ' | ' + err.message);
    return null;
  }
}

async function apiPost(path, data) {
  try {
    var resp = await fetch(API_BASE + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!resp.ok) {
      var errData = null;
      try { errData = await resp.json(); } catch (e) {}
      var detail = (errData && errData.detail) ? errData.detail : resp.statusText;
      console.warn('API POST ' + path + ' -> ' + resp.status + ': ' + detail);
      return { __error: true, status: resp.status, detail: detail };
    }
    return await resp.json();
  } catch (err) {
    console.warn('API POST error: ' + path + ' | ' + err.message);
    return { __error: true, status: 0, detail: err.message };
  }
}

// ═══════════════════════════════════════════════════════
// Section 2: WebSocket
// ═══════════════════════════════════════════════════════

function connectWs() {
  if (state.ws) { try { state.ws.close(); } catch (e) {} }
  var wsUrl = API_BASE.replace('http://', 'ws://') + '/ws';
  console.log('[ws] Connecting to ' + wsUrl);
  var ws = new WebSocket(wsUrl);
  state.ws = ws;

  ws.onopen = function () {
    console.log('[ws] Connected');
    state.wsReconnectAttempts = 0;
    setConnectionStatus('connected');
  };

  ws.onmessage = function (event) {
    try { handleWsMessage(JSON.parse(event.data)); }
    catch (e) { console.warn('[ws] Bad JSON: ' + event.data.slice(0, 100)); }
  };

  ws.onclose = function () {
    console.log('[ws] Disconnected');
    setConnectionStatus('disconnected');
    state.ws = null;
    var attempts = state.wsReconnectAttempts;
    var delay = Math.min(1000 * Math.pow(1.5, attempts), 30000);
    state.wsReconnectAttempts = attempts + 1;
    console.log('[ws] Reconnecting in ' + Math.round(delay / 1000) + 's');
    setTimeout(connectWs, delay);
  };

  ws.onerror = function () {};
}

function handleWsMessage(msg) {
  if (msg.type === 'agent_task_update') {
    var taskId = msg.task_id;
    var now = Date.now();
    var last = state.lastWsUpdate[taskId] || 0;
    if (now - last < 500) return;
    state.lastWsUpdate[taskId] = now;

    var task = state.tasks[taskId];
    if (task) { task.status = msg.status; task.progress = msg.progress; }
    if (state.selectedTaskId === taskId) { fetchAndRenderDetail(taskId); }
    renderTaskList();
    if (!task) { loadTasks(); }
  }
}

// ═══════════════════════════════════════════════════════
// Section 3: Workspace
// ═══════════════════════════════════════════════════════

async function loadWorkspace() {
  var data = await apiGet('/agent/workspace');
  if (data) { renderWorkspace(data.workspace); }
}

function renderWorkspace(wsPath) {
  state._wsPath = wsPath;
  var display = document.getElementById('workspace-display');
  if (wsPath) {
    display.textContent = wsPath;
    display.className = 'set';
    document.getElementById('workspace-input').value = wsPath;
  } else {
    display.textContent = t('workspace.not_set');
    display.className = '';
  }
}

async function setWorkspace() {
  var input = document.getElementById('workspace-input');
  var path = input.value.trim();
  if (!path) return;
  var result = await apiPost('/agent/workspace', { path: path });
  if (result.__error) {
    alert(t('workspace.set_failed') + ':\n' + result.detail);
    return;
  }
  renderWorkspace(result.workspace);
}

// ═══════════════════════════════════════════════════════
// Section 4: Worker list
// ═══════════════════════════════════════════════════════

async function loadWorkers() {
  var data = await apiGet('/agent/workers');
  if (!data || !data.workers) return;
  var select = document.getElementById('task-worker');
  select.innerHTML = '';
  for (var i = 0; i < data.workers.length; i++) {
    var w = data.workers[i];
    var opt = document.createElement('option');
    opt.value = w.name;
    opt.textContent = w.name + ' (' + w.role + ')';
    select.appendChild(opt);
  }
}

// ═══════════════════════════════════════════════════════
// Section 5: Task list
// ═══════════════════════════════════════════════════════

async function loadTasks() {
  var data = await apiGet('/agent/tasks?limit=100');
  if (!data || !data.items) return;
  state.taskOrder = [];
  for (var i = 0; i < data.items.length; i++) {
    var item = data.items[i];
    state.tasks[item.task_id] = item;
    state.taskOrder.push(item.task_id);
  }
  renderTaskList();
  if (state.selectedTaskId && state.tasks[state.selectedTaskId]) {
    fetchAndRenderDetail(state.selectedTaskId);
  }
}

function renderTaskList() {
  var container = document.getElementById('task-list');
  if (state.taskOrder.length === 0) {
    container.innerHTML = '<div class="task-list-empty">' + t('task.empty') + '</div>';
    return;
  }
  var html = '';
  for (var i = 0; i < state.taskOrder.length; i++) {
    var taskId = state.taskOrder[i];
    var task = state.tasks[taskId];
    if (!task) continue;
    var selectedClass = (taskId === state.selectedTaskId) ? ' selected' : '';
    var statusClass = statusToClass(task.status);
    html += '<div class="task-item' + selectedClass + '" onclick="selectTask(\'' + taskId + '\')">';
    html += '  <div class="task-desc">' + escHtml(task.description || t('task.no_desc')) + '</div>';
    html += '  <div class="task-meta">';
    html += '    <span class="status-dot ' + statusClass + '"></span>';
    html += '    <span>' + statusToText(task.status) + '</span>';
    if (task.worker_name) { html += '    <span>' + escHtml(task.worker_name) + '</span>'; }
    html += '    <span>' + formatTime(task.created_at) + '</span>';
    html += '  </div>';
    html += '</div>';
  }
  container.innerHTML = html;
}

function selectTask(taskId) {
  state.selectedTaskId = taskId;
  renderTaskList();
  fetchAndRenderDetail(taskId);
}

async function fetchAndRenderDetail(taskId) {
  var data = await apiGet('/agent/tasks/' + taskId + '/detail');
  if (!data) return;
  state.tasks[taskId] = data;
  renderTaskDetail(data);
  renderLogs(data);
  renderResult(data);
}

// ═══════════════════════════════════════════════════════
// Section 6: Task creation
// ═══════════════════════════════════════════════════════

async function createTask() {
  var descInput = document.getElementById('task-description');
  var description = descInput.value.trim();
  if (!description) return;

  var body = {
    type: document.getElementById('task-type').value,
    description: description,
    worker_name: document.getElementById('task-worker').value || null,
  };

  var result = await apiPost('/agent/tasks', body);
  if (result.__error) {
    alert(t('task.create_failed') + ':\n' + result.detail);
    return;
  }
  descInput.value = '';
  descInput.focus();
  await loadTasks();
  if (result.task_id) { selectTask(result.task_id); }
}

// ═══════════════════════════════════════════════════════
// Section 7: Detail / Log / Result
// ═══════════════════════════════════════════════════════

function renderTaskDetail(task) {
  var container = document.getElementById('task-detail-header');
  var progress = document.getElementById('task-progress');
  var statusClass = statusToClass(task.status);
  var wsInfo = task.workspace_path ? ' | &#128193; ' + escHtml(task.workspace_path) : '';

  container.innerHTML =
    '<div class="detail-title">' +
      '<span class="status-dot ' + statusClass + '"></span> ' +
      escHtml(task.description || t('task.no_desc')) +
    '</div>' +
    '<div class="detail-meta">' +
      '<span><strong>' + t('detail.id') + ':</strong> ' + escHtml(task.task_id) + '</span>' +
      '<span><strong>' + t('detail.type') + ':</strong> ' + escHtml(task.type) + '</span>' +
      '<span><strong>' + t('detail.status') + ':</strong> ' + statusToText(task.status) + '</span>' +
      (task.worker_name ? '<span><strong>' + t('detail.worker') + ':</strong> ' + escHtml(task.worker_name) + '</span>' : '') +
      '<span><strong>' + t('detail.updated') + ':</strong> ' + formatTime(task.updated_at) + '</span>' +
      wsInfo +
    '</div>';

  if (task.progress && task.status === 'running') {
    progress.innerHTML = '&#9201; ' + escHtml(task.progress);
  } else {
    progress.innerHTML = '';
  }
}

function renderLogs(task) {
  var container = document.getElementById('log-stream');
  var logs = task.logs || [];
  if (logs.length === 0) {
    container.innerHTML = '<div class="placeholder">' + t('log.waiting') + '</div>';
    return;
  }
  var toRender = logs.slice(-500);
  var html = '';
  if (logs.length > 500) {
    html += '<div class="log-line warn">... ' + (logs.length - 500) + ' ' + t('log.truncated') + ' ...</div>';
  }
  for (var i = 0; i < toRender.length; i++) {
    var line = escHtml(toRender[i]);
    var cls = 'log-line';
    if (line.indexOf('异常') >= 0 || line.indexOf('Error') >= 0 || line.indexOf('failed') >= 0) { cls += ' error'; }
    else if (line.indexOf('警告') >= 0 || line.indexOf('Warning') >= 0) { cls += ' warn'; }
    html += '<div class="' + cls + '">' + line + '</div>';
  }
  container.innerHTML = html;
  container.scrollTop = container.scrollHeight;
}

function renderResult(task) {
  var resultContainer = document.getElementById('result-content');
  var artifactsContainer = document.getElementById('artifacts-content');

  // Result
  if (task.status === 'pending') {
    resultContainer.innerHTML = '<span class="placeholder">' + t('result.pending') + '</span>';
    resultContainer.className = 'placeholder';
  } else if (task.status === 'running') {
    resultContainer.innerHTML = '<span class="placeholder">' + t('result.running') + '</span>';
    resultContainer.className = 'placeholder';
  } else if (task.status === 'failed') {
    resultContainer.innerHTML = '<span style="color:#E74C3C;">' + escHtml(task.error || t('result.unknown_error')) + '</span>';
    resultContainer.className = '';
  } else if (task.result) {
    resultContainer.textContent = task.result;
    resultContainer.className = '';
  } else {
    resultContainer.innerHTML = '<span class="placeholder">' + t('result.empty') + '</span>';
    resultContainer.className = 'placeholder';
  }

  // Artifacts
  var artifacts = task.artifacts || [];
  if (artifacts.length === 0) {
    artifactsContainer.innerHTML = '<span class="placeholder">' + t('artifacts.empty') + '</span>';
    artifactsContainer.className = 'placeholder';
  } else {
    var html = '';
    for (var i = 0; i < artifacts.length; i++) {
      var a = artifacts[i];
      var ap = typeof a === 'string' ? a : (a.path || a.name || JSON.stringify(a));
      html += '<div class="artifact-item">&#128196; ' + escHtml(ap) + '</div>';
    }
    artifactsContainer.innerHTML = html;
    artifactsContainer.className = '';
  }
}

// ═══════════════════════════════════════════════════════
// Section 8: Helpers
// ═══════════════════════════════════════════════════════

function statusToText(status) {
  var map = {
    pending: t('status.pending'),
    running: t('status.running'),
    completed: t('status.completed'),
    failed: t('status.failed'),
    cancelled: t('status.cancelled'),
  };
  return map[status] || status;
}

function statusToClass(status) {
  var map = { pending: 'pending', running: 'running', completed: 'completed', failed: 'failed', cancelled: 'cancelled' };
  return map[status] || '';
}

function formatTime(ts) {
  if (!ts) return '';
  var parts = ts.split(' ');
  if (parts.length < 2) return ts;
  var datePart = parts[0], timePart = parts[1].slice(0, 5);
  var today = new Date();
  var m = (today.getMonth() + 1).toString().padStart(2, '0');
  var d = today.getDate().toString().padStart(2, '0');
  var todayStr = today.getFullYear() + '-' + m + '-' + d;
  if (datePart === todayStr) { return timePart; }
  return datePart.slice(5) + ' ' + timePart;
}

function escHtml(str) {
  if (!str) return '';
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function setConnectionStatus(status) {
  state._connStatus = status;
  var el = document.getElementById('connection-status');
  if (status === 'connected') {
    el.textContent = t('app.connected');
    el.className = 'connected';
  } else {
    el.textContent = t('app.reconnecting');
    el.className = 'disconnected';
  }
}

// ═══════════════════════════════════════════════════════
// Section 9: Init
// ═══════════════════════════════════════════════════════

function init() {
  // Apply initial translations
  refreshAllUI();

  document.getElementById('workspace-set-btn').addEventListener('click', setWorkspace);
  document.getElementById('create-task-btn').addEventListener('click', createTask);
  document.getElementById('task-description').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') createTask();
  });
  document.getElementById('workspace-input').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') setWorkspace();
  });

  loadWorkspace();
  loadWorkers();
  loadTasks();
  connectWs();

  state.refreshTimer = setInterval(function () { loadTasks(); }, 10000);
}

window.addEventListener('DOMContentLoaded', init);
