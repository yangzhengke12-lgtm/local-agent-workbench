/**
 * Agent Desktop Workbench — 渲染进程 UI 逻辑
 *
 * 4 面板布局：
 *   左侧: Workspace 选择器 + 任务列表
 *   中央: 任务详情头 + 执行日志流
 *   右侧: 结果展示 + Artifacts 列表
 *   底部: 任务输入栏
 */

// ── 全局状态 ──
var API_BASE = 'http://localhost:8000';
var state = {
  tasks: {},              // task_id -> task object
  taskOrder: [],          // 有序 task_id 列表
  selectedTaskId: null,   // 当前选中的任务
  ws: null,               // WebSocket 连接
  wsReconnectAttempts: 0, // 重连次数
  refreshTimer: null,     // 轮询兜底定时器
  lastWsUpdate: {},       // task_id -> 上次 WebSocket 更新时间 (去抖)
};

// ═══════════════════════════════════════════════════════
// Section 1: API helpers
// ═══════════════════════════════════════════════════════

async function apiGet(path) {
  try {
    var resp = await fetch(API_BASE + path);
    if (!resp.ok) {
      console.warn('API GET ' + path + ' -> ' + resp.status);
      return null;
    }
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
  if (state.ws) {
    try { state.ws.close(); } catch (e) {}
  }

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
    try {
      var msg = JSON.parse(event.data);
      handleWsMessage(msg);
    } catch (e) {
      console.warn('[ws] Bad JSON: ' + event.data.slice(0, 100));
    }
  };

  ws.onclose = function () {
    console.log('[ws] Disconnected');
    setConnectionStatus('disconnected');
    state.ws = null;

    // 指数退避重连
    var attempts = state.wsReconnectAttempts;
    var delay = Math.min(1000 * Math.pow(1.5, attempts), 30000);
    state.wsReconnectAttempts = attempts + 1;
    console.log('[ws] Reconnecting in ' + Math.round(delay / 1000) + 's (attempt ' + attempts + ')');
    setTimeout(connectWs, delay);
  };

  ws.onerror = function (err) {
    console.warn('[ws] Error');
  };
}

function handleWsMessage(msg) {
  if (msg.type === 'agent_task_update') {
    var taskId = msg.task_id;
    var now = Date.now();
    var last = state.lastWsUpdate[taskId] || 0;

    // 去抖：同一任务 500ms 内只处理一次
    if (now - last < 500) return;
    state.lastWsUpdate[taskId] = now;

    // 更新缓存中任务状态
    var task = state.tasks[taskId];
    if (task) {
      task.status = msg.status;
      task.progress = msg.progress;
    }

    // 如果是选中任务，刷新详情
    if (state.selectedTaskId === taskId) {
      fetchAndRenderDetail(taskId);
    }

    // 刷新任务列表（更新状态点颜色）
    renderTaskList();

    // 如果 task 不在缓存中（新建任务通过 WS 先到），全量刷新
    if (!task) {
      loadTasks();
    }
  }
}

// ═══════════════════════════════════════════════════════
// Section 3: Workspace management
// ═══════════════════════════════════════════════════════

async function loadWorkspace() {
  var data = await apiGet('/agent/workspace');
  if (data) {
    renderWorkspace(data.workspace);
  }
}

function renderWorkspace(wsPath) {
  var display = document.getElementById('workspace-display');
  if (wsPath) {
    display.textContent = wsPath;
    display.className = 'set';
    document.getElementById('workspace-input').value = wsPath;
  } else {
    display.textContent = 'Not set';
    display.className = '';
  }
}

async function setWorkspace() {
  var input = document.getElementById('workspace-input');
  var path = input.value.trim();
  if (!path) return;

  var result = await apiPost('/agent/workspace', { path: path });
  if (result.__error) {
    alert('设置 workspace 失败:\n' + result.detail);
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

  // 重建缓存
  state.taskOrder = [];
  for (var i = 0; i < data.items.length; i++) {
    var item = data.items[i];
    state.tasks[item.task_id] = item;
    state.taskOrder.push(item.task_id);
  }

  renderTaskList();

  // 如果当前选中任务有更新，刷新详情
  if (state.selectedTaskId && state.tasks[state.selectedTaskId]) {
    fetchAndRenderDetail(state.selectedTaskId);
  }
}

function renderTaskList() {
  var container = document.getElementById('task-list');

  if (state.taskOrder.length === 0) {
    container.innerHTML = '<div class="task-list-empty">No tasks yet</div>';
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
    html += '  <div class="task-desc">' + escHtml(task.description || 'Untitled') + '</div>';
    html += '  <div class="task-meta">';
    html += '    <span class="status-dot ' + statusClass + '"></span>';
    html += '    <span>' + statusToText(task.status) + '</span>';
    if (task.worker_name) {
      html += '    <span>' + escHtml(task.worker_name) + '</span>';
    }
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

  // 更新缓存
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

  var taskType = document.getElementById('task-type').value;
  var workerName = document.getElementById('task-worker').value;

  var body = {
    type: taskType,
    description: description,
    worker_name: workerName || null,
  };

  var result = await apiPost('/agent/tasks', body);
  if (result.__error) {
    alert('创建任务失败:\n' + result.detail);
    return;
  }

  // 清空输入
  descInput.value = '';
  descInput.focus();

  // 刷新列表并选中新任务
  await loadTasks();
  if (result.task_id) {
    selectTask(result.task_id);
  }
}

// ═══════════════════════════════════════════════════════
// Section 7: Detail / Log / Result rendering
// ═══════════════════════════════════════════════════════

function renderTaskDetail(task) {
  var container = document.getElementById('task-detail-header');
  var progress = document.getElementById('task-progress');

  var statusClass = statusToClass(task.status);
  var wsInfo = task.workspace_path ? ' | &#128193; ' + escHtml(task.workspace_path) : '';

  container.innerHTML =
    '<div class="detail-title">' +
      '<span class="status-dot ' + statusClass + '"></span> ' +
      escHtml(task.description || 'Untitled') +
    '</div>' +
    '<div class="detail-meta">' +
      '<span><strong>ID:</strong> ' + escHtml(task.task_id) + '</span>' +
      '<span><strong>Type:</strong> ' + escHtml(task.type) + '</span>' +
      '<span><strong>Status:</strong> ' + statusToText(task.status) + '</span>' +
      (task.worker_name ? '<span><strong>Worker:</strong> ' + escHtml(task.worker_name) + '</span>' : '') +
      '<span><strong>Updated:</strong> ' + formatTime(task.updated_at) + '</span>' +
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
    container.innerHTML = '<div class="placeholder">Waiting for execution...</div>';
    return;
  }

  // 限制最多渲染 500 行
  var toRender = logs.slice(-500);
  var html = '';
  if (logs.length > 500) {
    html += '<div class="log-line warn">... ' + (logs.length - 500) + ' lines truncated ...</div>';
  }

  for (var i = 0; i < toRender.length; i++) {
    var line = escHtml(toRender[i]);
    var cls = 'log-line';
    if (line.indexOf('异常') >= 0 || line.indexOf('Error') >= 0 || line.indexOf('failed') >= 0) {
      cls += ' error';
    } else if (line.indexOf('警告') >= 0 || line.indexOf('Warning') >= 0) {
      cls += ' warn';
    }
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
    resultContainer.innerHTML = '<span class="placeholder">Task pending...</span>';
    resultContainer.className = 'placeholder';
  } else if (task.status === 'running') {
    resultContainer.innerHTML = '<span class="placeholder">Task running...</span>';
    resultContainer.className = 'placeholder';
  } else if (task.status === 'failed') {
    resultContainer.innerHTML = '<span style="color:#E74C3C;">' + escHtml(task.error || 'Unknown error') + '</span>';
    resultContainer.className = '';
  } else if (task.result) {
    resultContainer.textContent = task.result;
    resultContainer.className = '';
  } else {
    resultContainer.innerHTML = '<span class="placeholder">No result</span>';
    resultContainer.className = 'placeholder';
  }

  // Artifacts
  var artifacts = task.artifacts || [];
  if (artifacts.length === 0) {
    artifactsContainer.innerHTML = '<span class="placeholder">No artifacts</span>';
    artifactsContainer.className = 'placeholder';
  } else {
    var html = '';
    for (var i = 0; i < artifacts.length; i++) {
      var a = artifacts[i];
      var path = typeof a === 'string' ? a : (a.path || a.name || JSON.stringify(a));
      html += '<div class="artifact-item">&#128196; ' + escHtml(path) + '</div>';
    }
    artifactsContainer.innerHTML = html;
    artifactsContainer.className = '';
  }
}

// ═══════════════════════════════════════════════════════
// Section 8: UI Helpers
// ═══════════════════════════════════════════════════════

function statusToText(status) {
  var map = {
    pending: 'Pending',
    running: 'Running',
    completed: 'Completed',
    failed: 'Failed',
    cancelled: 'Cancelled',
  };
  return map[status] || status;
}

function statusToClass(status) {
  var map = {
    pending: 'pending',
    running: 'running',
    completed: 'completed',
    failed: 'failed',
    cancelled: 'cancelled',
  };
  return map[status] || '';
}

function formatTime(ts) {
  if (!ts) return '';
  // "2026-06-20 17:30:45" -> "17:30" (today) or "06-20 17:30"
  var parts = ts.split(' ');
  if (parts.length < 2) return ts;
  var datePart = parts[0];
  var timePart = parts[1].slice(0, 5);

  // 如果是今天，只显示时间
  var today = new Date();
  var m = (today.getMonth() + 1).toString().padStart(2, '0');
  var d = today.getDate().toString().padStart(2, '0');
  var todayStr = today.getFullYear() + '-' + m + '-' + d;

  if (datePart === todayStr) {
    return timePart;
  }
  return datePart.slice(5) + ' ' + timePart;
}

function escHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function setConnectionStatus(status) {
  var el = document.getElementById('connection-status');
  el.textContent = status === 'connected' ? 'connected' : 'reconnecting...';
  el.className = status === 'connected' ? 'connected' : 'disconnected';
}

// ═══════════════════════════════════════════════════════
// Section 9: Initialization
// ═══════════════════════════════════════════════════════

function init() {
  // 设置事件监听
  document.getElementById('workspace-set-btn').addEventListener('click', setWorkspace);
  document.getElementById('create-task-btn').addEventListener('click', createTask);
  document.getElementById('task-description').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') createTask();
  });
  // Enter 在 workspace input 中也触发 set
  document.getElementById('workspace-input').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') setWorkspace();
  });

  // 加载初始数据
  loadWorkspace();
  loadWorkers();
  loadTasks();
  connectWs();

  // 轮询兜底：每 10 秒刷新任务列表
  state.refreshTimer = setInterval(function () {
    loadTasks();
  }, 10000);
}

window.addEventListener('DOMContentLoaded', init);
