/**
 * i18n — 中/英 多语言切换
 * 使用: t('key') 获取当前语言文本
 *       setLang('zh') / setLang('en') 切换
 *       HTML: <span data-i18n="key">fallback</span>
 */
var LANG = localStorage.getItem('workbench_lang') || 'zh';
var DICT = {};

// ── 词条 ──
var ZH = {
  // header
  'app.title': 'Agent Desktop 工作台',
  'app.connecting': '连接中...',
  'app.connected': '已连接',
  'app.reconnecting': '重连中...',
  // workspace
  'workspace.title': '工作区',
  'workspace.not_set': '未设置',
  'workspace.placeholder': '输入路径，或点击右侧选择...',
  'workspace.set_btn': '选择',
  'workspace.set_failed': '设置工作区失败',
  // tasks
  'task.title': '任务列表',
  'task.empty': '暂无任务',
  'task.no_desc': '未命名',
  'task.select_hint': '选择一个任务查看详情',
  'task.create_btn': '创建任务',
  'task.desc_placeholder': '输入任务描述...',
  'task.create_failed': '创建任务失败',
  // task types
  'task.type.worker': 'Worker 任务',
  'task.type.verified': '验证任务',
  'task.type.pipeline': 'Pipeline',
  'task.worker_disabled': 'Pipeline 任务不需要指定 Worker',
  // statuses
  'status.pending': '等待中',
  'status.running': '执行中',
  'status.completed': '已完成',
  'status.failed': '失败',
  'status.cancelled': '已取消',
  // detail labels
  'detail.id': 'ID',
  'detail.type': '类型',
  'detail.status': '状态',
  'detail.worker': '执行者',
  'detail.updated': '更新',
  'detail.workspace': '工作区',
  // log
  'log.title': '执行日志',
  'log.waiting': '等待执行...',
  'log.truncated': '条日志被截断',
  // result
  'result.title': '结果',
  'result.empty': '暂无结果',
  'result.pending': '任务等待执行...',
  'result.running': '任务执行中...',
  'result.unknown_error': '未知错误',
  // artifacts
  'artifacts.title': '产出文件',
  'artifacts.empty': '暂无产出',
  // language
  'lang.label': '语言',
};

var EN = {
  'app.title': 'Agent Desktop Workbench',
  'app.connecting': 'Connecting...',
  'app.connected': 'Connected',
  'app.reconnecting': 'Reconnecting...',
  'workspace.title': 'Workspace',
  'workspace.not_set': 'Not set',
  'workspace.placeholder': 'Enter path, or choose...',
  'workspace.set_btn': 'Choose',
  'workspace.set_failed': 'Failed to set workspace',
  'task.title': 'Tasks',
  'task.empty': 'No tasks yet',
  'task.no_desc': 'Untitled',
  'task.select_hint': 'Select a task to view details',
  'task.create_btn': 'Create Task',
  'task.desc_placeholder': 'Describe the task...',
  'task.create_failed': 'Failed to create task',
  'task.type.worker': 'Worker Task',
  'task.type.verified': 'Verified Task',
  'task.type.pipeline': 'Pipeline',
  'task.worker_disabled': 'Pipeline tasks do not require a worker',
  'status.pending': 'Pending',
  'status.running': 'Running',
  'status.completed': 'Completed',
  'status.failed': 'Failed',
  'status.cancelled': 'Cancelled',
  'detail.id': 'ID',
  'detail.type': 'Type',
  'detail.status': 'Status',
  'detail.worker': 'Worker',
  'detail.updated': 'Updated',
  'detail.workspace': 'Workspace',
  'log.title': 'Execution Log',
  'log.waiting': 'Waiting for execution...',
  'log.truncated': 'lines truncated',
  'result.title': 'Result',
  'result.empty': 'No result',
  'result.pending': 'Task pending...',
  'result.running': 'Task running...',
  'result.unknown_error': 'Unknown error',
  'artifacts.title': 'Artifacts',
  'artifacts.empty': 'No artifacts',
  'lang.label': 'Language',
};

// ── 初始化 ──
function loadDict() {
  if (LANG === 'zh') { DICT = ZH; }
  else { DICT = EN; }
  // 更新语言下拉
  var sel = document.getElementById('lang-select');
  if (sel) { sel.value = LANG; }
}

// ── 公共 API ──
function t(key) {
  return DICT[key] || key;
}

function setLang(lang) {
  LANG = lang;
  localStorage.setItem('workbench_lang', lang);
  loadDict();
  refreshAllUI();
}

// ── 刷新所有 UI ──
function refreshAllUI() {
  // 1. HTML 中 data-i18n 属性
  var els = document.querySelectorAll('[data-i18n]');
  for (var i = 0; i < els.length; i++) {
    var el = els[i];
    var key = el.getAttribute('data-i18n');
    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
      el.placeholder = t(key);
    } else {
      el.textContent = t(key);
    }
  }
  // 2. 重绘动态内容
  renderWorkspace(state._wsPath || null);
  renderTaskList();
  if (state.selectedTaskId) {
    fetchAndRenderDetail(state.selectedTaskId);
  }
  // 3. 刷新 worker 下拉（role 名称来自后端，但 type 下拉需要更新）
  refreshTypeSelect();
  // 4. 连接状态
  setConnectionStatus(state._connStatus || 'disconnected');
}

function refreshTypeSelect() {
  var sel = document.getElementById('task-type');
  if (!sel) return;
  sel.options[0].textContent = t('task.type.worker');
  sel.options[1].textContent = t('task.type.verified');
  if (sel.options[2]) {
    sel.options[2].textContent = t('task.type.pipeline');
  }
  if (typeof updateWorkerAvailability === 'function') {
    updateWorkerAvailability();
  }
}

// 启动时加载
loadDict();
