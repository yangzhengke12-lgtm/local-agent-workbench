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
  _theme: localStorage.getItem('workbench_theme') || 'graphite',
  _taskQuery: '',
  _inspectorTab: 'files',
  _workers: [],
  _workspaceFiles: null,
  _workspacePath: '',
  _filePreview: null,
  _taskEvents: {},
  _memory: null,
  _rules: null,
  _settings: null,
  _settingsCategory: 'general',
  _settingsQuery: '',
  _applyingSettings: false,
  _tokenBudget: 1000000,
  _estimatedTokens: 0,
};
window.state = state;

var SETTINGS_CATEGORIES = [
  { id: 'general', label: '常规', desc: '默认任务和工作方式' },
  { id: 'appearance', label: '外观', desc: '主题、语言和布局' },
  { id: 'workspace', label: '工作区', desc: '本地项目目录' },
  { id: 'model', label: '模型与上下文', desc: 'Provider 和 1M 窗口' },
  { id: 'workers', label: 'Worker', desc: '角色、模型和工具' },
  { id: 'permissions', label: '权限与工具', desc: '危险工具和执行边界' },
  { id: 'data', label: '数据与记忆', desc: '本地持久化文件' },
  { id: 'about', label: '关于', desc: '版本和运行目录' },
];

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

async function apiPatch(path, data) {
  try {
    var resp = await fetch(API_BASE + path, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!resp.ok) {
      var errData = null;
      try { errData = await resp.json(); } catch (e) {}
      var detail = (errData && errData.detail) ? errData.detail : resp.statusText;
      console.warn('API PATCH ' + path + ' -> ' + resp.status + ': ' + detail);
      return { __error: true, status: resp.status, detail: detail };
    }
    return await resp.json();
  } catch (err) {
    console.warn('API PATCH error: ' + path + ' | ' + err.message);
    return { __error: true, status: 0, detail: err.message };
  }
}

// ═══════════════════════════════════════════════════════
// Section 2: WebSocket
// ═══════════════════════════════════════════════════════

function connectWs() {
  if (state.ws) { try { state.ws.close(); } catch (e) {} }
  var wsUrl = API_BASE.replace('http://', 'ws://') + '/ws';
  var ws = new WebSocket(wsUrl);
  state.ws = ws;

  ws.onopen = function () {
    state.wsReconnectAttempts = 0;
    setConnectionStatus('connected');
  };

  ws.onmessage = function (event) {
    try { handleWsMessage(JSON.parse(event.data)); }
    catch (e) { console.warn('[ws] Bad JSON: ' + event.data.slice(0, 100)); }
  };

  ws.onclose = function () {
    setConnectionStatus('disconnected');
    state.ws = null;
    var attempts = state.wsReconnectAttempts;
    var delay = Math.min(1000 * Math.pow(1.5, attempts), 30000);
    state.wsReconnectAttempts = attempts + 1;
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
  if (data) {
    renderWorkspace(data.workspace);
    if (data.workspace) { loadWorkspaceFiles(''); }
  }
}

function renderWorkspace(wsPath) {
  state._wsPath = wsPath;
  var display = document.getElementById('workspace-display');
  var tabName = document.getElementById('workspace-tab-name');
  if (wsPath) {
    display.textContent = wsPath;
    display.className = 'set';
    document.getElementById('workspace-input').value = wsPath;
    if (tabName) {
      var parts = wsPath.split(/[\\/]/).filter(Boolean);
      tabName.textContent = parts.length ? parts[parts.length - 1] : 'workspace';
    }
  } else {
    display.textContent = t('workspace.not_set');
    display.className = '';
    if (tabName) { tabName.textContent = 'my-agent'; }
  }
  updateContextMeter();
  renderSettingsIfOpen();
}

async function setWorkspace(pathOverride) {
  var input = document.getElementById('workspace-input');
  var path = pathOverride || input.value.trim();
  if (!path && window.workbench && window.workbench.selectWorkspaceDirectory) {
    path = await window.workbench.selectWorkspaceDirectory();
    if (path) { input.value = path; }
  }
  if (!path) return;
  var result = await apiPost('/agent/workspace', { path: path });
  if (result.__error) {
    alert(t('workspace.set_failed') + ':\n' + result.detail);
    return;
  }
  renderWorkspace(result.workspace);
  if (state._settings && state._settings.settings) {
    state._settings.settings.workspace_path = result.workspace;
  }
  showToast('Workspace updated');
}

async function chooseWorkspaceDirectory() {
  var input = document.getElementById('workspace-input');
  var path = '';
  if (window.workbench && window.workbench.selectWorkspaceDirectory) {
    path = await window.workbench.selectWorkspaceDirectory();
  }
  if (!path && input) {
    path = input.value.trim();
  }
  if (!path) {
    showToast('No workspace selected');
    return;
  }
  if (input) { input.value = path; }
  await setWorkspace(path);
}

// ═══════════════════════════════════════════════════════
// Section 4: Worker list
// ═══════════════════════════════════════════════════════

async function loadWorkers() {
  var data = await apiGet('/agent/workers');
  if (!data || !data.workers) return;
  state._workers = data.workers;
  var select = document.getElementById('task-worker');
  select.innerHTML = '';
  for (var i = 0; i < data.workers.length; i++) {
    var w = data.workers[i];
    var opt = document.createElement('option');
    opt.value = w.name;
    opt.textContent = w.name + ' (' + w.role + ')';
    select.appendChild(opt);
  }
  updateWorkerAvailability();
  applyDefaultTaskSettings();
  renderInspector();
  renderSettingsIfOpen();
}

async function loadSettings() {
  var data = await apiGet('/agent/settings');
  if (!data || !data.settings) {
    setSettingsSyncState('后端未连接');
    return null;
  }
  state._settings = data;
  if (data.runtime && data.runtime.model && data.runtime.model.context_window_tokens) {
    state._tokenBudget = data.runtime.model.context_window_tokens;
  }
  applySettingsDefaults(data.settings);
  renderSettingsIfOpen();
  return data;
}

function applySettingsDefaults(settings) {
  if (!settings) return;
  state._applyingSettings = true;
  try {
    if (settings.theme) {
      applyTheme(settings.theme);
    }
    if (settings.language && settings.language !== LANG) {
      LANG = settings.language;
      localStorage.setItem('workbench_lang', LANG);
      loadDict();
    }
    if (typeof settings.rail_collapsed === 'boolean') {
      setRailCollapsed(settings.rail_collapsed, true);
    }
    if (settings.inspector_tab) {
      state._inspectorTab = settings.inspector_tab;
      setInspectorActiveTab();
    }
    if (settings.refresh_interval_sec) {
      resetRefreshTimer(settings.refresh_interval_sec);
    }
    applyDefaultTaskSettings();
    refreshAllUI();
  } finally {
    state._applyingSettings = false;
  }
}

function applyDefaultTaskSettings() {
  var settings = state._settings && state._settings.settings;
  if (!settings) return;
  var typeSelect = document.getElementById('task-type');
  var workerSelect = document.getElementById('task-worker');
  if (typeSelect && settings.default_task_type) {
    typeSelect.value = settings.default_task_type;
    updateWorkerAvailability();
  }
  if (workerSelect && settings.default_worker) {
    for (var i = 0; i < workerSelect.options.length; i++) {
      if (workerSelect.options[i].value === settings.default_worker) {
        workerSelect.value = settings.default_worker;
        break;
      }
    }
  }
}

function resetRefreshTimer(seconds) {
  if (state.refreshTimer) {
    clearInterval(state.refreshTimer);
    state.refreshTimer = null;
  }
  var ms = Math.max(3, Math.min(60, Number(seconds) || 10)) * 1000;
  state.refreshTimer = setInterval(function () { loadTasks(); }, ms);
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
  var count = document.getElementById('task-count');
  var statusCount = document.getElementById('status-task-count');
  var visibleTaskIds = [];
  var query = (state._taskQuery || '').toLowerCase();
  for (var j = 0; j < state.taskOrder.length; j++) {
    var id = state.taskOrder[j];
    var item = state.tasks[id];
    if (!item) continue;
    var haystack = [
      item.description || '',
      item.task_id || '',
      item.worker_name || '',
      item.type || '',
      item.status || '',
    ].join(' ').toLowerCase();
    if (!query || haystack.indexOf(query) >= 0) {
      visibleTaskIds.push(id);
    }
  }
  if (count) { count.textContent = String(state.taskOrder.length); }
  if (statusCount) { statusCount.textContent = String(state.taskOrder.length); }
  if (visibleTaskIds.length === 0) {
    container.innerHTML = '<div class="task-list-empty">' + (query ? 'No matching tasks' : t('task.empty')) + '</div>';
    return;
  }
  var html = '';
  for (var i = 0; i < visibleTaskIds.length; i++) {
    var taskId = visibleTaskIds[i];
    var task = state.tasks[taskId];
    if (!task) continue;
    var selectedClass = (taskId === state.selectedTaskId) ? ' selected' : '';
    var statusClass = statusToClass(task.status);
    html += '<div class="task-item' + selectedClass + '" onclick="selectTask(\'' + taskId + '\')">';
    html += '  <div class="task-desc">' + escHtml(task.description || t('task.no_desc')) + '</div>';
    html += '  <div class="task-meta">';
    html += '    <span class="status-dot ' + statusClass + '"></span>';
    html += '    <span>' + statusToText(task.status) + '</span>';
    html += '    <span>' + typeToText(task.type) + '</span>';
    if (task.worker_name) { html += '    <span>' + escHtml(task.worker_name) + '</span>'; }
    html += '    <span>' + formatTime(task.created_at) + '</span>';
    html += '  </div>';
    html += '</div>';
  }
  container.innerHTML = html;
  updateContextMeter();
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
  loadTaskEvents(taskId);
}

// ═══════════════════════════════════════════════════════
// Section 6: Task creation
// ═══════════════════════════════════════════════════════

async function createTask() {
  if (state._connStatus !== 'connected') {
    showToast('后端未连接，无法创建任务');
    return;
  }
  var descInput = document.getElementById('task-description');
  var description = descInput.value.trim();
  if (!description) return;
  var taskType = document.getElementById('task-type').value;

  var body = {
    type: taskType,
    description: description,
    worker_name: taskType === 'project_pipeline_task'
      ? null
      : (document.getElementById('task-worker').value || null),
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
  var wsInfo = task.workspace_path
    ? '<span><strong>' + t('detail.workspace') + ':</strong> ' + escHtml(task.workspace_path) + '</span>'
    : '';

  container.innerHTML =
    '<div class="detail-title">' +
      '<span class="status-badge ' + statusClass + '">' + statusToText(task.status) + '</span>' +
      escHtml(task.description || t('task.no_desc')) +
    '</div>' +
    '<div class="detail-meta">' +
      '<span><strong>' + t('detail.id') + ':</strong> ' + escHtml(task.task_id) + '</span>' +
      '<span><strong>' + t('detail.type') + ':</strong> ' + typeToText(task.type) + '</span>' +
      (task.worker_name ? '<span><strong>' + t('detail.worker') + ':</strong> ' + escHtml(task.worker_name) + '</span>' : '') +
      '<span><strong>' + t('detail.updated') + ':</strong> ' + formatTime(task.updated_at) + '</span>' +
      wsInfo +
    '</div>';

  if (task.progress && task.status === 'running') {
    progress.innerHTML = '&#9201; ' + escHtml(task.progress);
  } else {
    progress.innerHTML = '';
  }
  updateContextMeter();
}

function renderLogs(task) {
  var container = document.getElementById('log-stream');
  var logs = task.logs || [];
  var count = document.getElementById('log-count');
  if (count) { count.textContent = String(logs.length); }
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
  updateContextMeter();
}

function renderResult(task) {
  renderInspector();
}

// ═══════════════════════════════════════════════════════
// Section 8: Inspector data / views
// ═══════════════════════════════════════════════════════

async function loadWorkspaceFiles(path) {
  var query = path ? '?path=' + encodeURIComponent(path) : '';
  var data = await apiGet('/agent/workspace/files' + query);
  if (!data) {
    state._workspaceFiles = null;
    renderInspector();
    return;
  }
  state._workspaceFiles = data;
  state._workspacePath = data.relative_path || '';
  state._filePreview = null;
  renderInspector();
}

async function previewWorkspaceFile(path) {
  var data = await apiGet('/agent/workspace/file?path=' + encodeURIComponent(path));
  if (!data) {
    showToast('File preview failed');
    return;
  }
  state._filePreview = data;
  state._inspectorTab = 'files';
  setInspectorActiveTab();
  renderInspector();
  updateContextMeter();
}

async function loadTaskEvents(taskId) {
  if (!taskId) return;
  var data = await apiGet('/agent/tasks/' + taskId + '/events');
  if (data) {
    state._taskEvents[taskId] = data.events || [];
    renderInspector();
  }
}

async function loadMemory() {
  var data = await apiGet('/agent/memory');
  if (data) {
    state._memory = data;
    renderInspector();
  }
}

async function loadRules() {
  var data = await apiGet('/agent/rules');
  if (data) {
    state._rules = data;
    renderInspector();
  }
}

function renderInspector() {
  var container = document.getElementById('inspector-content');
  if (!container) return;
  if (state._inspectorTab === 'files') {
    container.innerHTML = renderFilesPanel();
  } else if (state._inspectorTab === 'tools') {
    container.innerHTML = renderToolsPanel();
  } else if (state._inspectorTab === 'memory') {
    container.innerHTML = renderMemoryPanel();
  } else {
    container.innerHTML = renderRulesPanel();
  }
  updateContextMeter();
}

function renderFilesPanel() {
  var task = state.selectedTaskId ? state.tasks[state.selectedTaskId] : null;
  var html = '<div class="inspector-scroll">';
  html += '<div class="panel-title">工作区文件</div>';
  if (!state._wsPath) {
    html += '<div class="inspector-empty">先选择工作区，文件树会显示在这里。</div>';
  } else if (!state._workspaceFiles) {
    html += '<div class="inspector-empty">正在读取工作区文件...</div>';
  } else {
    html += '<div class="file-nav">';
    html += '<span>' + escHtml(state._workspaceFiles.relative_path || '/') + '</span>';
    if (state._workspaceFiles.parent !== null) {
      html += '<button data-open-dir="' + escAttr(state._workspaceFiles.parent || '') + '">上级</button>';
    }
    html += '</div>';
    html += '<div class="file-list">';
    var entries = state._workspaceFiles.entries || [];
    if (entries.length === 0) {
      html += '<div class="inspector-empty compact">这个目录没有可展示文件。</div>';
    }
    for (var i = 0; i < entries.length; i++) {
      var e = entries[i];
      var dataAttr = e.type === 'directory'
        ? 'data-open-dir="' + escAttr(e.relative_path) + '"'
        : 'data-preview-file="' + escAttr(e.relative_path) + '"';
      html += '<button class="file-row ' + e.type + '" ' + dataAttr + '>';
      html += '<span class="file-name">' + escHtml(e.name) + '</span>';
      html += '<span class="file-meta">' + (e.type === 'directory' ? 'dir' : formatBytes(e.size)) + '</span>';
      html += '</button>';
    }
    html += '</div>';
  }

  html += '<div class="panel-title">文件预览</div>';
  if (!state._filePreview) {
    html += '<div class="inspector-empty">点击上方文件，或点击任务产物文件进行预览。</div>';
  } else if (!state._filePreview.previewable) {
    html += '<div class="inspector-empty">' + escHtml(state._filePreview.reason || '不可预览') + '</div>';
  } else {
    html += '<div class="file-preview-title">' + escHtml(state._filePreview.relative_path) + '</div>';
    html += '<pre class="file-preview">' + escHtml(state._filePreview.content) + '</pre>';
    if (state._filePreview.truncated) {
      html += '<div class="inspector-note">内容过长，已截断显示。</div>';
    }
  }

  html += '<div class="panel-title">当前任务产物</div>';
  html += renderTaskResultBlock(task);
  html += renderArtifactsBlock(task);
  html += '</div>';
  return html;
}

function renderTaskResultBlock(task) {
  if (!task) return '<div class="inspector-empty">选择任务后会显示结果摘要。</div>';
  if (task.status === 'pending') return '<div class="inspector-empty">' + t('result.pending') + '</div>';
  if (task.status === 'running') return '<div class="inspector-empty">' + t('result.running') + '</div>';
  if (task.status === 'failed') return '<div class="result-block error">' + escHtml(task.error || t('result.unknown_error')) + '</div>';
  if (!task.result) return '<div class="inspector-empty">' + t('result.empty') + '</div>';
  return '<pre class="result-block">' + escHtml(task.result) + '</pre>';
}

function renderArtifactsBlock(task) {
  var artifacts = task ? (task.artifacts || []) : [];
  if (artifacts.length === 0) return '<div class="inspector-empty compact">' + t('artifacts.empty') + '</div>';
  var html = '<div class="artifact-list">';
  for (var i = 0; i < artifacts.length; i++) {
    var a = artifacts[i];
    var ap = typeof a === 'string' ? a : (a.path || a.name || JSON.stringify(a));
    var summary = typeof a === 'object' ? (a.summary || a.type || '') : '';
    html += '<button class="artifact-item" data-preview-file="' + escAttr(ap) + '">';
    html += '<strong>' + escHtml(ap) + '</strong>';
    if (summary) html += '<span>' + escHtml(summary) + '</span>';
    html += '</button>';
  }
  html += '</div>';
  return html;
}

function renderToolsPanel() {
  var taskId = state.selectedTaskId;
  var events = taskId ? (state._taskEvents[taskId] || []) : [];
  var html = '<div class="inspector-scroll">';
  html += '<div class="panel-title">任务工具轨迹</div>';
  if (!taskId) {
    html += '<div class="inspector-empty">选择任务后，这里会显示真实工具调用和返回。</div>';
  } else if (events.length === 0) {
    html += '<div class="inspector-empty">这个任务还没有工具事件。</div>';
  } else {
    html += '<div class="event-list">';
    for (var i = 0; i < events.length; i++) {
      var ev = events[i];
      html += '<div class="event-item ' + escHtml(ev.type) + '">';
      html += '<div class="event-head"><span>' + escHtml(ev.type) + '</span><span>#' + ev.index + '</span></div>';
      html += '<div class="event-text">' + escHtml(ev.text) + '</div>';
      if (ev.tool_name) html += '<div class="event-tool">' + escHtml(ev.tool_name) + '</div>';
      html += '</div>';
    }
    html += '</div>';
  }
  html += '<div class="panel-title">Worker 工具权限</div>';
  html += renderWorkerToolMatrix(state._workers);
  html += '</div>';
  return html;
}

function renderWorkerToolMatrix(workers) {
  if (!workers || workers.length === 0) return '<div class="inspector-empty">正在加载 worker 权限...</div>';
  var html = '<div class="worker-matrix">';
  for (var i = 0; i < workers.length; i++) {
    var w = workers[i];
    html += '<div class="worker-card">';
    html += '<div class="worker-card-head"><strong>' + escHtml(w.name) + '</strong><span>' + escHtml(w.role || '') + '</span></div>';
    var tools = w.tools || [];
    html += '<div class="tool-tags">';
    if (tools.length === 0) html += '<span class="tool-tag muted">manager tools</span>';
    for (var j = 0; j < tools.length; j++) {
      html += '<span class="tool-tag">' + escHtml(tools[j]) + '</span>';
    }
    html += '</div></div>';
  }
  html += '</div>';
  return html;
}

function renderMemoryPanel() {
  if (!state._memory) {
    loadMemory();
    return '<div class="inspector-scroll"><div class="inspector-empty">正在加载本地记忆...</div></div>';
  }
  var m = state._memory;
  var html = '<div class="inspector-scroll">';
  html += '<div class="metric-grid">';
  html += renderMetric('Sessions', m.sessions.count);
  html += renderMetric('Knowledge', m.knowledge.count);
  html += renderMetric('Task Board', m.task_board.count);
  html += renderMetric('Project State', m.project_states.count);
  html += '</div>';
  html += '<div class="panel-title">最近知识</div>';
  var recent = m.knowledge.recent || [];
  if (recent.length === 0) {
    html += '<div class="inspector-empty">本地知识库还没有记录。</div>';
  } else {
    for (var i = 0; i < recent.length; i++) {
      var item = recent[i];
      html += '<div class="knowledge-item"><strong>' + escHtml(item.topic || 'Untitled') + '</strong>';
      html += '<p>' + escHtml(item.content || '') + '</p></div>';
    }
  }
  html += '<div class="panel-title">存储位置</div>';
  html += '<div class="path-list">';
  html += '<div>' + escHtml(m.knowledge.file) + '</div>';
  html += '<div>' + escHtml(m.project_states.directory) + '</div>';
  html += '</div>';
  html += '</div>';
  return html;
}

function renderRulesPanel() {
  if (!state._rules) {
    loadRules();
    return '<div class="inspector-scroll"><div class="inspector-empty">正在加载运行规则...</div></div>';
  }
  var r = state._rules;
  var html = '<div class="inspector-scroll">';
  html += '<div class="panel-title">任务类型规则</div>';
  for (var i = 0; i < r.task_types.length; i++) {
    var tt = r.task_types[i];
    html += '<div class="rule-card"><div><strong>' + escHtml(tt.label) + '</strong><span>' + escHtml(tt.type) + '</span></div>';
    html += '<p>' + escHtml(tt.description) + '</p>';
    html += '<small>' + (tt.requires_worker ? '必须选择 Worker' : '不需要指定 Worker') + '</small></div>';
  }
  html += '<div class="panel-title">Workspace 安全边界</div>';
  html += '<div class="rule-card"><p>' + escHtml(r.workspace_policy.file_preview) + '</p><p>' + escHtml(r.workspace_policy.command_execution) + '</p></div>';
  html += '<div class="panel-title">高风险工具</div>';
  html += '<div class="tool-tags">';
  for (var j = 0; j < r.dangerous_tools.length; j++) {
    html += '<span class="tool-tag danger">' + escHtml(r.dangerous_tools[j]) + '</span>';
  }
  html += '</div>';
  html += renderWorkerToolMatrix(r.workers || []);
  html += '</div>';
  return html;
}

function renderMetric(label, value) {
  return '<div class="metric-card"><strong>' + escHtml(String(value || 0)) + '</strong><span>' + escHtml(label) + '</span></div>';
}

// ═══════════════════════════════════════════════════════
// Section 9: Helpers
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

function typeToText(type) {
  var map = {
    worker_task: t('task.type.worker'),
    verified_task: t('task.type.verified'),
    project_pipeline_task: t('task.type.pipeline'),
  };
  return map[type] || type || '';
}

function updateWorkerAvailability() {
  var typeSelect = document.getElementById('task-type');
  var workerSelect = document.getElementById('task-worker');
  if (!typeSelect || !workerSelect) return;
  var isPipeline = typeSelect.value === 'project_pipeline_task';
  workerSelect.disabled = isPipeline;
  workerSelect.title = isPipeline ? t('task.worker_disabled') : '';
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

function escAttr(str) {
  return escHtml(str).replace(/'/g, '&#39;');
}

function formatBytes(bytes) {
  var value = Number(bytes || 0);
  if (value < 1024) return value + ' B';
  if (value < 1024 * 1024) return (value / 1024).toFixed(1) + ' KB';
  return (value / 1024 / 1024).toFixed(1) + ' MB';
}

function estimateTokens(text) {
  var value = String(text || '').trim();
  if (!value) return 0;
  var asciiWords = (value.match(/[A-Za-z0-9_]+/g) || []).length;
  var cjkChars = (value.match(/[\u3400-\u9fff]/g) || []).length;
  var punctuation = Math.ceil((value.length - asciiWords - cjkChars) / 8);
  return Math.max(1, Math.ceil(asciiWords * 1.25 + cjkChars * 1.05 + punctuation));
}

function estimateCurrentContextTokens() {
  var parts = [];
  if (state._wsPath) parts.push(state._wsPath);
  if (state.selectedTaskId && state.tasks[state.selectedTaskId]) {
    var task = state.tasks[state.selectedTaskId];
    parts.push(task.description || '');
    parts.push(task.progress || '');
    parts.push((task.logs || []).slice(-80).join('\n'));
    parts.push(task.result || '');
    parts.push(JSON.stringify(task.artifacts || []));
  }
  if (state._filePreview && state._filePreview.previewable) {
    parts.push(state._filePreview.relative_path || '');
    parts.push(state._filePreview.content || '');
  }
  if (state._inspectorTab === 'tools' && state.selectedTaskId) {
    parts.push(JSON.stringify(state._taskEvents[state.selectedTaskId] || []));
  }
  if (state._inspectorTab === 'memory' && state._memory) {
    parts.push(JSON.stringify(state._memory));
  }
  if (state._inspectorTab === 'rules' && state._rules) {
    parts.push(JSON.stringify(state._rules));
  }
  return estimateTokens(parts.join('\n'));
}

function updateContextMeter() {
  var tokens = estimateCurrentContextTokens();
  state._estimatedTokens = tokens;
  var budget = state._tokenBudget;
  var pct = Math.min(100, Math.round(tokens / budget * 100));
  var value = document.getElementById('context-token-value');
  var fill = document.getElementById('context-token-fill');
  var percent = document.getElementById('context-token-percent');
  var source = document.getElementById('context-token-source');
  var status = document.getElementById('status-token-count');
  if (value) value.textContent = formatTokenCount(tokens) + ' / 1M';
  if (fill) fill.style.width = Math.max(2, pct) + '%';
  if (percent) percent.textContent = pct + '%';
  if (source) source.textContent = state.selectedTaskId ? 'est. selected task context · window 1M' : 'est. workspace context · window 1M';
  if (status) status.textContent = 'tokens ' + formatTokenCount(tokens);
}

function formatTokenCount(value) {
  if (value >= 1000) return (value / 1000).toFixed(value >= 10000 ? 0 : 1) + 'k';
  return String(value);
}

function setSettingsSyncState(text) {
  var el = document.getElementById('settings-sync-state');
  if (el) { el.textContent = text; }
}

function openSettings(category) {
  if (category) { state._settingsCategory = category; }
  document.body.classList.add('settings-open');
  var view = document.getElementById('settings-view');
  if (view) { view.setAttribute('aria-hidden', 'false'); }
  if (!state._settings) {
    loadSettings();
  } else {
    renderSettings();
  }
}

function closeSettings() {
  document.body.classList.remove('settings-open');
  var view = document.getElementById('settings-view');
  if (view) { view.setAttribute('aria-hidden', 'true'); }
}

function renderSettingsIfOpen() {
  if (document.body.classList.contains('settings-open')) {
    renderSettings();
  }
}

function renderSettings() {
  renderSettingsNav();
  renderSettingsContent();
}

function renderSettingsNav() {
  var nav = document.getElementById('settings-nav');
  if (!nav) return;
  var query = (state._settingsQuery || '').toLowerCase();
  var html = '';
  for (var i = 0; i < SETTINGS_CATEGORIES.length; i++) {
    var cat = SETTINGS_CATEGORIES[i];
    var haystack = (cat.label + ' ' + cat.desc).toLowerCase();
    if (query && haystack.indexOf(query) === -1) continue;
    html += '<button class="settings-nav-item' + (cat.id === state._settingsCategory ? ' active' : '') + '" data-settings-category="' + cat.id + '">';
    html += '<strong>' + escHtml(cat.label) + '</strong><span>' + escHtml(cat.desc) + '</span></button>';
  }
  nav.innerHTML = html || '<div class="settings-empty">没有匹配的设置</div>';
}

function renderSettingsContent() {
  var container = document.getElementById('settings-content');
  if (!container) return;
  if (!state._settings) {
    container.innerHTML = '<div class="settings-card"><h2>正在连接后端</h2><p>设置需要读取本地 Agent Runtime 状态。</p></div>';
    return;
  }
  setSettingsSyncState(state._connStatus === 'connected' ? '已连接' : '离线缓存');
  var category = state._settingsCategory || 'general';
  if (category === 'general') container.innerHTML = renderGeneralSettings();
  else if (category === 'appearance') container.innerHTML = renderAppearanceSettings();
  else if (category === 'workspace') container.innerHTML = renderWorkspaceSettings();
  else if (category === 'model') container.innerHTML = renderModelSettings();
  else if (category === 'workers') container.innerHTML = renderWorkerSettings();
  else if (category === 'permissions') container.innerHTML = renderPermissionSettings();
  else if (category === 'data') container.innerHTML = renderDataSettings();
  else container.innerHTML = renderAboutSettings();
}

function settingsData() {
  return state._settings || { settings: {}, schema: {}, runtime: {} };
}

function optionList(values, selected) {
  var html = '';
  values = values || [];
  for (var i = 0; i < values.length; i++) {
    var value = values[i];
    html += '<option value="' + escAttr(value) + '"' + (value === selected ? ' selected' : '') + '>' + escHtml(value) + '</option>';
  }
  return html;
}

function workerOptionList(selected) {
  var workers = (state._settings && state._settings.runtime && state._settings.runtime.workers) || state._workers || [];
  var html = '<option value="">自动/未指定</option>';
  for (var i = 0; i < workers.length; i++) {
    var w = workers[i];
    html += '<option value="' + escAttr(w.name) + '"' + (w.name === selected ? ' selected' : '') + '>' + escHtml(w.name + ' - ' + (w.role || 'Worker')) + '</option>';
  }
  return html;
}

function renderGeneralSettings() {
  var data = settingsData();
  var s = data.settings;
  var schema = data.schema || {};
  var taskTypes = schema.choices && schema.choices.default_task_type ? schema.choices.default_task_type : ['worker_task', 'verified_task', 'project_pipeline_task'];
  return '' +
    '<section class="settings-section"><h2>常规</h2><p>这些设置会真实影响任务创建和桌面刷新行为。</p>' +
    '<div class="settings-card">' +
      '<label class="settings-row"><span><strong>默认任务类型</strong><small>创建新任务时自动选择</small></span><select data-setting-field="default_task_type">' + optionList(taskTypes, s.default_task_type) + '</select></label>' +
      '<label class="settings-row"><span><strong>默认 Worker</strong><small>Worker/验证任务的默认执行者</small></span><select data-setting-field="default_worker">' + workerOptionList(s.default_worker) + '</select></label>' +
      '<label class="settings-row"><span><strong>任务刷新间隔</strong><small>3-60 秒，影响任务列表轮询</small></span><input type="number" min="3" max="60" data-setting-field="refresh_interval_sec" value="' + escAttr(String(s.refresh_interval_sec || 10)) + '"></label>' +
    '</div></section>';
}

function renderAppearanceSettings() {
  var s = settingsData().settings;
  return '' +
    '<section class="settings-section"><h2>外观</h2><p>外观设置会立即保存，并在重启后保留。</p>' +
    '<div class="settings-card">' +
      '<label class="settings-row"><span><strong>主题颜色</strong><small>同步到标题栏和状态栏</small></span><select data-setting-field="theme">' + optionList(['graphite', 'ember', 'blue', 'violet', 'green'], s.theme) + '</select></label>' +
      '<label class="settings-row"><span><strong>语言</strong><small>中文 / English</small></span><select data-setting-field="language"><option value="zh"' + (s.language === 'zh' ? ' selected' : '') + '>中文</option><option value="en"' + (s.language === 'en' ? ' selected' : '') + '>English</option></select></label>' +
      '<label class="settings-row"><span><strong>默认收起左侧栏</strong><small>重启后保持当前布局偏好</small></span><input type="checkbox" data-setting-field="rail_collapsed"' + (s.rail_collapsed ? ' checked' : '') + '></label>' +
      '<label class="settings-row"><span><strong>默认 Inspector</strong><small>右侧面板启动时打开的页签</small></span><select data-setting-field="inspector_tab">' + optionList(['files', 'tools', 'memory', 'rules'], s.inspector_tab) + '</select></label>' +
    '</div></section>';
}

function renderWorkspaceSettings() {
  var s = settingsData().settings;
  var workspace = s.workspace_path || state._wsPath || '';
  return '' +
    '<section class="settings-section"><h2>工作区</h2><p>工作区会持久化到本地设置文件，桌面端重启后自动恢复。</p>' +
    '<div class="settings-card">' +
      '<div class="settings-path"><strong>当前目录</strong><code>' + escHtml(workspace || '未设置') + '</code></div>' +
      '<label class="settings-row stack"><span><strong>工作区路径</strong><small>浏览器环境可手动输入；Electron 内点击选择目录</small></span><div class="settings-inline"><input type="text" id="settings-workspace-input" value="' + escAttr(workspace) + '" placeholder="C:\\\\path\\\\to\\\\project"><button id="settings-workspace-choose">选择</button><button id="settings-workspace-save">保存</button></div></label>' +
      '<div class="settings-readonly"><span>只读规则</span><p>文件预览只允许访问当前 workspace 内文件，并排除 node_modules、.git、venv 等目录。</p></div>' +
    '</div></section>';
}

function renderModelSettings() {
  var runtime = settingsData().runtime || {};
  var model = runtime.model || {};
  var providers = runtime.providers || {};
  var html = '<section class="settings-section"><h2>模型与上下文</h2><p>Provider 状态来自运行时环境变量；这里不写入 API key。</p>';
  html += '<div class="settings-card"><div class="settings-kv"><span>默认模型</span><strong>' + escHtml(model.default_model || 'unknown') + '</strong></div><div class="settings-kv"><span>上下文窗口</span><strong>' + escHtml(model.context_window || '1M') + '</strong></div></div>';
  html += '<div class="settings-grid">';
  for (var key in providers) {
    if (!Object.prototype.hasOwnProperty.call(providers, key)) continue;
    var p = providers[key];
    html += '<div class="settings-card provider-card"><div><strong>' + escHtml(key) + '</strong><span class="badge ' + (p.configured ? 'ok' : 'warn') + '">' + (p.configured ? '已配置' : '未配置') + '</span></div><p>' + escHtml(p.default_model || '') + '</p><small>' + escHtml(p.env || '') + '</small></div>';
  }
  html += '</div></section>';
  return html;
}

function renderWorkerSettings() {
  var workers = (settingsData().runtime || {}).workers || [];
  var html = '<section class="settings-section"><h2>Worker</h2><p>Worker 配置来自 workers.json。当前页只读展示，不提供假开关。</p><div class="settings-grid">';
  for (var i = 0; i < workers.length; i++) {
    var w = workers[i];
    html += '<div class="settings-card worker-card"><strong>' + escHtml(w.name) + '</strong><p>' + escHtml(w.role || '') + '</p><small>' + escHtml(w.model || '') + '</small><div class="tool-chip-list">';
    var tools = w.tools || [];
    for (var j = 0; j < tools.length; j++) {
      var dangerous = (w.dangerous_tools || []).indexOf(tools[j]) !== -1;
      html += '<span class="tool-chip' + (dangerous ? ' danger' : '') + '">' + escHtml(tools[j]) + '</span>';
    }
    html += '</div></div>';
  }
  return html + '</div></section>';
}

function renderPermissionSettings() {
  var permissions = (settingsData().runtime || {}).permissions || {};
  var policy = permissions.workspace_policy || {};
  var dangerous = permissions.dangerous_tools || [];
  var html = '<section class="settings-section"><h2>权限与工具</h2><p>这些是运行时真实边界。首版只读展示，避免让用户误以为可以绕过权限。</p><div class="settings-card">';
  html += '<div class="settings-readonly"><span>工作区边界</span><p>' + escHtml(policy.file_preview || '') + '</p></div>';
  html += '<div class="settings-readonly"><span>命令执行</span><p>' + escHtml(policy.command_execution || '') + '</p></div>';
  html += '<div class="tool-chip-list">';
  for (var i = 0; i < dangerous.length; i++) {
    html += '<span class="tool-chip danger">' + escHtml(dangerous[i]) + '</span>';
  }
  html += '</div></div></section>';
  return html;
}

function renderDataSettings() {
  var memory = (settingsData().runtime || {}).memory || {};
  var keys = ['session_file', 'knowledge_file', 'task_board_file', 'score_file', 'project_state_dir'];
  var html = '<section class="settings-section"><h2>数据与记忆</h2><p>本地持久化路径只读展示，方便面试和排查。</p><div class="settings-card">';
  for (var i = 0; i < keys.length; i++) {
    var key = keys[i];
    html += '<div class="settings-path"><strong>' + escHtml(key) + '</strong><code>' + escHtml(memory[key] || '') + '</code></div>';
  }
  return html + '</div></section>';
}

function renderAboutSettings() {
  var app = ((settingsData().runtime || {}).app) || {};
  return '' +
    '<section class="settings-section"><h2>关于</h2><p>桌面端当前连接的运行时信息。</p>' +
    '<div class="settings-card">' +
      '<div class="settings-kv"><span>应用</span><strong>' + escHtml(app.name || 'local-agent-workbench') + '</strong></div>' +
      '<div class="settings-kv"><span>版本</span><strong>' + escHtml(app.version || '') + '</strong></div>' +
      '<div class="settings-kv"><span>运行时</span><strong>' + escHtml(app.runtime || '') + '</strong></div>' +
      '<div class="settings-path"><strong>项目目录</strong><code>' + escHtml(app.project_dir || '') + '</code></div>' +
      '<div class="settings-path"><strong>设置文件</strong><code>' + escHtml(app.settings_file || '') + '</code></div>' +
    '</div></section>';
}

async function saveSetting(field, value) {
  var patch = {};
  patch[field] = value;
  setSettingsSyncState('保存中...');
  var result = await apiPatch('/agent/settings', patch);
  if (result.__error) {
    setSettingsSyncState('保存失败');
    showToast('设置保存失败: ' + result.detail);
    return false;
  }
  state._settings = result;
  if (result.runtime && result.runtime.model && result.runtime.model.context_window_tokens) {
    state._tokenBudget = result.runtime.model.context_window_tokens;
  }
  applySettingsDefaults(result.settings);
  setSettingsSyncState('已保存');
  showToast('设置已保存');
  renderSettings();
  return true;
}

function readSettingInput(target) {
  var field = target.getAttribute('data-setting-field');
  if (!field) return null;
  var value = target.type === 'checkbox' ? target.checked : target.value;
  if (field === 'refresh_interval_sec' || field === 'log_max_lines') {
    value = Number(value);
  }
  return { field: field, value: value };
}

async function handleSettingsChange(e) {
  var data = readSettingInput(e.target);
  if (!data) return;
  await saveSetting(data.field, data.value);
}

async function chooseSettingsWorkspace() {
  var input = document.getElementById('settings-workspace-input');
  var path = '';
  if (window.workbench && window.workbench.selectWorkspaceDirectory) {
    path = await window.workbench.selectWorkspaceDirectory();
  }
  if (path && input) {
    input.value = path;
  }
}

async function saveSettingsWorkspace() {
  var input = document.getElementById('settings-workspace-input');
  var path = input ? input.value.trim() : '';
  if (!path) {
    showToast('请先选择工作区目录');
    return;
  }
  var ok = await saveSetting('workspace_path', path);
  if (ok) {
    renderWorkspace(path);
    var wsInput = document.getElementById('workspace-input');
    if (wsInput) { wsInput.value = path; }
    loadWorkspaceFiles('');
  }
}

function setInspectorActiveTab() {
  var buttons = document.querySelectorAll('.inspector-tabs button');
  for (var i = 0; i < buttons.length; i++) {
    var tab = buttons[i].getAttribute('data-inspector-tab');
    buttons[i].classList.toggle('active', tab === state._inspectorTab);
  }
}

function setRailCollapsed(collapsed, skipSave) {
  document.body.classList.toggle('rail-collapsed', collapsed);
  localStorage.setItem('workbench_rail_collapsed', collapsed ? '1' : '0');
  if (!skipSave && state._settings && state._settings.settings) {
    state._settings.settings.rail_collapsed = collapsed;
  }
}

function setConnectionStatus(status) {
  state._connStatus = status;
  var el = document.getElementById('connection-status');
  if (status === 'connected') {
    if (el) {
      el.textContent = t('app.connected');
      el.className = 'connected';
    }
  } else {
    if (el) {
      el.textContent = t('app.reconnecting');
      el.className = 'disconnected';
    }
  }
  var createBtn = document.getElementById('create-task-btn');
  if (createBtn) {
    createBtn.disabled = status !== 'connected';
    createBtn.title = status === 'connected' ? '' : '后端未连接，无法创建任务';
  }
  setSettingsSyncState(status === 'connected' ? '已连接' : '后端未连接');
}

function applyTheme(theme) {
  state._theme = theme || 'graphite';
  document.documentElement.setAttribute('data-theme', state._theme);
  localStorage.setItem('workbench_theme', state._theme);
  var select = document.getElementById('theme-select');
  if (select) { select.value = state._theme; }
  var statusTheme = document.getElementById('status-theme');
  if (statusTheme) {
    statusTheme.textContent = state._theme.charAt(0).toUpperCase() + state._theme.slice(1);
  }
  if (state._settings && state._settings.settings) {
    state._settings.settings.theme = state._theme;
  }
}

function showToast(message) {
  var toast = document.getElementById('toast');
  if (!toast) return;
  toast.textContent = message;
  toast.className = 'show';
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(function () {
    toast.className = '';
  }, 2200);
}

async function copySelectedTask() {
  var task = state.selectedTaskId ? state.tasks[state.selectedTaskId] : null;
  if (!task) {
    showToast('Select a task first');
    return;
  }
  var text = task.result || (task.logs || []).join('\n') || task.description || '';
  if (!text) {
    showToast('Nothing to copy');
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    showToast('Copied task output');
  } catch (err) {
    showToast('Copy failed');
  }
}

function exportSelectedTask() {
  var task = state.selectedTaskId ? state.tasks[state.selectedTaskId] : null;
  if (!task) {
    showToast('Select a task first');
    return;
  }
  var blob = new Blob([JSON.stringify(task, null, 2)], { type: 'application/json' });
  var url = URL.createObjectURL(blob);
  var a = document.createElement('a');
  a.href = url;
  a.download = 'agent-task-' + task.task_id + '.json';
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  showToast('Exported task JSON');
}

function switchInspectorTab(button) {
  var tab = button.getAttribute('data-inspector-tab');
  state._inspectorTab = tab || 'files';
  setInspectorActiveTab();
  if (state._inspectorTab === 'memory' && !state._memory) { loadMemory(); }
  if (state._inspectorTab === 'rules' && !state._rules) { loadRules(); }
  if (state._inspectorTab === 'tools' && state.selectedTaskId && !state._taskEvents[state.selectedTaskId]) {
    loadTaskEvents(state.selectedTaskId);
  }
  renderInspector();
}

function handleFooterAction(action) {
  if (action === 'policy') {
    state._inspectorTab = 'rules';
    setInspectorActiveTab();
    loadRules();
    showToast('Showing runtime rules');
    return;
  }
  if (action === 'settings') {
    openSettings();
    return;
  }
  showToast('Local Agent Workbench - local-first multi-agent task runtime');
}

function startNewTask() {
  state.selectedTaskId = null;
  renderTaskList();
  var detail = document.getElementById('task-detail-header');
  var progress = document.getElementById('task-progress');
  var logs = document.getElementById('log-stream');
  var logCount = document.getElementById('log-count');
  if (detail) { detail.innerHTML = '<div class="placeholder">' + t('task.select_hint') + '</div>'; }
  if (progress) { progress.innerHTML = ''; }
  if (logs) { logs.innerHTML = '<div class="placeholder">' + t('log.waiting') + '</div>'; }
  if (logCount) { logCount.textContent = '0'; }
  var input = document.getElementById('task-description');
  if (input) {
    input.value = '';
    input.focus();
  }
  renderInspector();
  showToast('Ready for a new task');
}

function handleGlobalShortcut(e) {
  if (!(e.ctrlKey || e.metaKey)) return;
  var key = String(e.key || '').toLowerCase();
  if (key === 'n') {
    e.preventDefault();
    startNewTask();
  } else if (key === 'k') {
    e.preventDefault();
    var search = document.getElementById('task-search');
    if (search) {
      search.focus();
      search.select();
      showToast('Search tasks');
    }
  } else if (key === ',') {
    e.preventDefault();
    openSettings();
  }
}

function handleInspectorClick(e) {
  var target = e.target;
  while (target && target !== document) {
    if (target.getAttribute) {
      var dir = target.getAttribute('data-open-dir');
      var file = target.getAttribute('data-preview-file');
      if (dir !== null) {
        loadWorkspaceFiles(dir);
        return;
      }
      if (file !== null) {
        previewWorkspaceFile(file);
        return;
      }
    }
    target = target.parentNode;
  }
}

// ═══════════════════════════════════════════════════════
// Section 9: Init
// ═══════════════════════════════════════════════════════

function init() {
  // Apply initial translations
  applyTheme(state._theme);
  setRailCollapsed(localStorage.getItem('workbench_rail_collapsed') === '1');
  refreshAllUI();

  var minimizeBtn = document.getElementById('window-minimize');
  var maximizeBtn = document.getElementById('window-maximize');
  var closeBtn = document.getElementById('window-close');
  if (window.workbench) {
    if (minimizeBtn && window.workbench.minimizeWindow) {
      minimizeBtn.addEventListener('click', function () { window.workbench.minimizeWindow(); });
    }
    if (maximizeBtn && window.workbench.toggleMaximizeWindow) {
      maximizeBtn.addEventListener('click', function () { window.workbench.toggleMaximizeWindow(); });
    }
    if (closeBtn && window.workbench.closeWindow) {
      closeBtn.addEventListener('click', function () { window.workbench.closeWindow(); });
    }
  }

  document.getElementById('workspace-set-btn').addEventListener('click', chooseWorkspaceDirectory);
  document.getElementById('create-task-btn').addEventListener('click', createTask);
  var themeSelect = document.getElementById('theme-select');
  if (themeSelect) {
    themeSelect.addEventListener('change', function () {
      applyTheme(themeSelect.value);
      if (state._settings) { saveSetting('theme', themeSelect.value); }
    });
  }
  var railToggle = document.getElementById('rail-toggle');
  if (railToggle) {
    railToggle.addEventListener('click', function () {
      var collapsed = !document.body.classList.contains('rail-collapsed');
      setRailCollapsed(collapsed);
      if (state._settings) { saveSetting('rail_collapsed', collapsed); }
      showToast(document.body.classList.contains('rail-collapsed') ? 'Sidebar collapsed' : 'Sidebar expanded');
    });
  }
  var moreButton = document.getElementById('more-button');
  if (moreButton) {
    moreButton.addEventListener('click', function () {
      state._inspectorTab = 'rules';
      setInspectorActiveTab();
      loadRules();
      showToast('Showing workbench capabilities');
    });
  }
  var historyRefresh = document.getElementById('history-refresh-btn');
  if (historyRefresh) {
    historyRefresh.addEventListener('click', async function () {
      await loadTasks();
      showToast('Task list refreshed');
    });
  }
  var quickNew = document.getElementById('quick-new-task');
  var headerNew = document.getElementById('header-new-task');
  var tabNew = document.getElementById('new-task-btn');
  if (quickNew) { quickNew.addEventListener('click', startNewTask); }
  if (headerNew) { headerNew.addEventListener('click', startNewTask); }
  if (tabNew) { tabNew.addEventListener('click', startNewTask); }
  var taskSearch = document.getElementById('task-search');
  if (taskSearch) {
    taskSearch.addEventListener('input', function () {
      state._taskQuery = taskSearch.value.trim();
      renderTaskList();
    });
  }
  var copyButton = document.getElementById('copy-task-btn');
  var exportButton = document.getElementById('export-task-btn');
  if (copyButton) { copyButton.addEventListener('click', copySelectedTask); }
  if (exportButton) { exportButton.addEventListener('click', exportSelectedTask); }
  var inspectorButtons = document.querySelectorAll('.inspector-tabs button');
  for (var i = 0; i < inspectorButtons.length; i++) {
    inspectorButtons[i].addEventListener('click', function () { switchInspectorTab(this); });
  }
  var footerButtons = document.querySelectorAll('.footer-link');
  for (var k = 0; k < footerButtons.length; k++) {
    footerButtons[k].addEventListener('click', function () {
      handleFooterAction(this.getAttribute('data-action'));
    });
  }
  var settingsBack = document.getElementById('settings-back');
  if (settingsBack) { settingsBack.addEventListener('click', closeSettings); }
  var settingsSearch = document.getElementById('settings-search-input');
  if (settingsSearch) {
    settingsSearch.addEventListener('input', function () {
      state._settingsQuery = settingsSearch.value.trim();
      renderSettingsNav();
    });
  }
  var settingsNav = document.getElementById('settings-nav');
  if (settingsNav) {
    settingsNav.addEventListener('click', function (e) {
      var target = e.target;
      while (target && target !== settingsNav) {
        if (target.getAttribute && target.getAttribute('data-settings-category')) {
          state._settingsCategory = target.getAttribute('data-settings-category');
          renderSettings();
          return;
        }
        target = target.parentNode;
      }
    });
  }
  var settingsContent = document.getElementById('settings-content');
  if (settingsContent) {
    settingsContent.addEventListener('change', handleSettingsChange);
    settingsContent.addEventListener('click', function (e) {
      var target = e.target;
      if (target && target.id === 'settings-workspace-choose') {
        chooseSettingsWorkspace();
      } else if (target && target.id === 'settings-workspace-save') {
        saveSettingsWorkspace();
      }
    });
  }
  document.getElementById('task-type').addEventListener('change', updateWorkerAvailability);
  document.getElementById('task-description').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') createTask();
  });
  document.getElementById('workspace-input').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') setWorkspace();
  });
  var inspectorContent = document.getElementById('inspector-content');
  if (inspectorContent) { inspectorContent.addEventListener('click', handleInspectorClick); }
  document.addEventListener('keydown', handleGlobalShortcut);

  resetRefreshTimer(10);
  loadWorkspace();
  loadWorkers();
  loadSettings();
  loadTasks();
  loadRules();
  loadMemory();
  renderInspector();
  updateContextMeter();
  connectWs();
}

window.addEventListener('DOMContentLoaded', init);
