/**
 * Agent Desktop Workbench — Electron 主进程
 *
 * 职责：
 *   1. 启动 python server.py 子进程
 *   2. 等待 /health 返回 ok
 *   3. 打开 BrowserWindow 加载 renderer.html
 *   4. 退出时干净关闭 Python 进程
 *   5. 检测端口占用，复用已有服务
 *
 * 注意：如果 ELECTRON_RUN_AS_NODE 环境变量被设成了 1，
 * Electron 会降级为纯 Node.js 运行时，导致 require("electron") 失败。
 * 启动前确保该变量未设置。
 */
const { app, BrowserWindow, Menu, dialog, ipcMain } = require('electron');
const { spawn } = require('child_process');
const http = require('http');
const path = require('path');
const net = require('net');
const fs = require('fs');

// ── 配置 ──
const PORT = 8000;
const HEALTH_URL = `http://localhost:${PORT}/health`;
const HEALTH_RETRIES = 30;
const HEALTH_INTERVAL_MS = 1000;
const BACKEND_RESTART_DELAY_MS = 1500;
const HEALTH_MONITOR_INTERVAL_MS = 5000;

let mainWindow = null;
let pythonProcess = null;
let isAppQuitting = false;
let restartTimer = null;
let healthMonitorTimer = null;
const LOG_FILE = path.join(__dirname, 'desktop-main.log');

function ignoreBrokenPipe(stream) {
  if (!stream || !stream.on) return;
  stream.on('error', (err) => {
    if (err && err.code === 'EPIPE') return;
  });
}

ignoreBrokenPipe(process.stdout);
ignoreBrokenPipe(process.stderr);

function safeLog(message) {
  try {
    fs.appendFileSync(LOG_FILE, `${new Date().toISOString()} ${message}\n`, 'utf8');
  } catch (_) {}
}

function safeError(message) {
  safeLog(message);
}

const gotSingleInstanceLock = app.requestSingleInstanceLock();
if (!gotSingleInstanceLock) {
  safeLog('[main] Another desktop instance is already running, quitting duplicate instance');
  app.exit(0);
}

// ── 端口检测：返回 true=空闲，false=已占用 ──
function checkPort(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once('error', () => resolve(false));  // 端口被占用
    server.once('listening', () => {
      server.close();
      resolve(true);  // 端口空闲
    });
    server.listen(port, '127.0.0.1');
  });
}

// ── 启动 Python 后端 ──
function startPythonServer() {
  // server.py 位于 AI-Agent管理系统/ 目录下（desktop/ 的父目录）
  const projectDir = path.join(__dirname, '..');
  const serverPy = path.join(projectDir, 'server.py');
  const cwd = projectDir;

  safeLog(`[main] Starting Python: python "${serverPy}" (cwd: ${cwd})`);

  // 依次尝试 python3 → python → py（Windows 上 python 不一定在 PATH）
  const pythonCandidates = process.platform === 'win32'
    ? ['python', 'py', 'python3']
    : ['python3', 'python'];
  let pythonCmd = pythonCandidates[0];
  for (const candidate of pythonCandidates) {
    try {
      const check = require('child_process').spawnSync(candidate, ['--version'], { timeout: 3000 });
      if (check.status === 0) {
        pythonCmd = candidate;
        safeLog(`[main] Found Python: ${candidate}`);
        break;
      }
    } catch (_) {}
  }

  pythonProcess = spawn(pythonCmd, [serverPy], {
    cwd: cwd,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });

  pythonProcess.stdout.on('data', (data) => {
    safeLog(`[python] ${data.toString().trim()}`);
  });

  pythonProcess.stderr.on('data', (data) => {
    safeError(`[python:err] ${data.toString().trim()}`);
  });

  pythonProcess.on('close', (code) => {
    safeLog(`[main] Python process exited with code ${code}`);
    pythonProcess = null;
    if (!isAppQuitting) {
      scheduleBackendRestart(`exit code ${code}`);
    }
  });

  pythonProcess.on('error', (err) => {
    safeError(`[main] Failed to start Python: ${err.message}`);
    pythonProcess = null;
    if (!isAppQuitting) {
      scheduleBackendRestart(`spawn error: ${err.message}`);
    }
  });
}

function projectDir() {
  return path.resolve(path.join(__dirname, '..'));
}

function samePath(a, b) {
  if (!a || !b) return false;
  const left = path.resolve(a);
  const right = path.resolve(b);
  return process.platform === 'win32'
    ? left.toLowerCase() === right.toLowerCase()
    : left === right;
}

function parseJsonSafe(text) {
  try { return JSON.parse(text); } catch (_) { return null; }
}

// ── 健康检查：轮询 /health ──
function checkHealth(retries, requireSameProject = true) {
  return new Promise((resolve, reject) => {
    let attempts = 0;

    function tryHealth() {
      attempts++;
      const req = http.get(HEALTH_URL, (res) => {
        let body = '';
        res.on('data', (chunk) => { body += chunk; });
        res.on('end', () => {
          if (res.statusCode === 200) {
            const health = parseJsonSafe(body) || {};
            if (requireSameProject && !samePath(health.project_dir, projectDir())) {
              const actual = health.project_dir || '(unknown)';
              reject(new Error(
                `端口 ${PORT} 已被另一个 Agent 后端占用。\n` +
                `当前桌面目录: ${projectDir()}\n` +
                `端口上的后端: ${actual}\n\n` +
                '请关闭旧桌面端/旧 server.py 后重新启动。'
              ));
              return;
            }
            safeLog(`[main] Health check OK (${health.project_dir || 'legacy health'})`);
            resolve(health);
          } else if (attempts < retries) {
            setTimeout(tryHealth, HEALTH_INTERVAL_MS);
          } else {
            reject(new Error(`Health check failed after ${retries} retries (status ${res.statusCode})`));
          }
        });
      });

      req.on('error', () => {
        if (attempts < retries) {
          setTimeout(tryHealth, HEALTH_INTERVAL_MS);
        } else {
          reject(new Error(`Health check failed after ${retries} retries (connection refused)`));
        }
      });

      req.setTimeout(3000, () => {
        req.destroy();
        if (attempts < retries) {
          setTimeout(tryHealth, HEALTH_INTERVAL_MS);
        } else {
          reject(new Error('Health check timeout'));
        }
      });
    }

    tryHealth();
  });
}

// ── 杀掉 Python 子进程 ──
function killPython() {
  if (restartTimer) {
    clearTimeout(restartTimer);
    restartTimer = null;
  }
  if (pythonProcess) {
    safeLog('[main] Killing Python process...');
    if (process.platform === 'win32') {
      // Windows: taskkill 确保子进程树全清
      spawn('taskkill', ['/pid', pythonProcess.pid.toString(), '/f', '/t']);
    } else {
      pythonProcess.kill('SIGTERM');
    }
    pythonProcess = null;
  }
}

function focusMainWindow() {
  if (!mainWindow) return;
  if (mainWindow.isMinimized()) {
    mainWindow.restore();
  }
  mainWindow.show();
  mainWindow.focus();
}

function scheduleBackendRestart(reason) {
  if (isAppQuitting || restartTimer) return;
  safeLog(`[main] Scheduling backend restart: ${reason}`);
  restartTimer = setTimeout(async () => {
    restartTimer = null;
    if (isAppQuitting || pythonProcess) return;
    try {
      const portFree = await checkPort(PORT);
      if (portFree) {
        safeLog('[main] Restarting Python backend after unexpected exit...');
        startPythonServer();
      } else {
        safeLog('[main] Backend restart skipped because port 8000 is already in use');
      }
      await checkHealth(HEALTH_RETRIES, true);
      safeLog('[main] Backend restart health check OK');
    } catch (err) {
      safeError(`[main] Backend restart failed: ${err.message}`);
      scheduleBackendRestart(`retry after failed restart: ${err.message}`);
    }
  }, BACKEND_RESTART_DELAY_MS);
}

async function ensureBackendAvailable(reason = 'health monitor') {
  if (isAppQuitting || restartTimer) return;
  try {
    await checkHealth(1, true);
    return;
  } catch (err) {
    safeError(`[main] ${reason}: backend unavailable (${err.message})`);
  }

  if (pythonProcess) {
    safeLog(`[main] ${reason}: waiting for owned Python process to exit cleanly`);
    return;
  }

  try {
    const portFree = await checkPort(PORT);
    if (!portFree) {
      safeLog(`[main] ${reason}: port ${PORT} still occupied, waiting for recovery`);
      return;
    }
    safeLog(`[main] ${reason}: port ${PORT} is free, starting backend`);
    startPythonServer();
    await checkHealth(HEALTH_RETRIES, true);
    safeLog(`[main] ${reason}: backend recovered`);
  } catch (err) {
    safeError(`[main] ${reason}: backend recovery failed: ${err.message}`);
  }
}

function startHealthMonitor() {
  if (healthMonitorTimer) return;
  healthMonitorTimer = setInterval(() => {
    ensureBackendAvailable('health monitor').catch((err) => {
      safeError(`[main] Health monitor fatal error: ${err.message}`);
    });
  }, HEALTH_MONITOR_INTERVAL_MS);
}

function stopHealthMonitor() {
  if (!healthMonitorTimer) return;
  clearInterval(healthMonitorTimer);
  healthMonitorTimer = null;
}

// ── 创建窗口 ──
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1024,
    minHeight: 700,
    title: 'Agent Desktop Workbench',
    frame: false,
    titleBarStyle: 'hidden',
    autoHideMenuBar: true,
    backgroundColor: '#0f141b',
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js'),
    },
    show: false,
  });

  mainWindow.loadFile(path.join(__dirname, 'renderer.html'));

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// ── 应用生命周期 ──
app.whenReady().then(async () => {
  try {
    Menu.setApplicationMenu(null);

    // 1. 检测端口
    const portFree = await checkPort(PORT);

    if (portFree) {
      safeLog(`[main] Port ${PORT} is free, starting Python backend...`);
      startPythonServer();
      await checkHealth(HEALTH_RETRIES, true);
    } else {
      safeLog(`[main] Port ${PORT} is occupied, reusing existing server`);
      await checkHealth(1, true);
    }

    // 2. 创建窗口
    createWindow();
    startHealthMonitor();
  } catch (err) {
    dialog.showErrorBox(
      '启动失败',
      `无法连接到 Agent 后端 (${HEALTH_URL}):\n\n${err.message}\n\n请确认: python server.py 可正常启动`
    );
    app.quit();
  }
});

app.on('second-instance', () => {
  safeLog('[main] Second desktop instance requested, focusing existing window');
  focusMainWindow();
});

app.on('window-all-closed', () => {
  isAppQuitting = true;
  stopHealthMonitor();
  killPython();
  app.quit();
});

app.on('before-quit', () => {
  isAppQuitting = true;
  stopHealthMonitor();
  killPython();
});

app.on('activate', () => {
  // macOS: 点 dock 图标重新创建窗口
  if (mainWindow === null) {
    createWindow();
  }
});

ipcMain.handle('workspace:select-directory', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: '选择工作区目录',
    properties: ['openDirectory'],
  });
  if (result.canceled || !result.filePaths || result.filePaths.length === 0) {
    return null;
  }
  return result.filePaths[0];
});

ipcMain.handle('window:minimize', () => {
  if (mainWindow) {
    mainWindow.minimize();
  }
});

ipcMain.handle('window:toggle-maximize', () => {
  if (!mainWindow) return false;
  if (mainWindow.isMaximized()) {
    mainWindow.unmaximize();
    return false;
  }
  mainWindow.maximize();
  return true;
});

ipcMain.handle('window:close', () => {
  if (mainWindow) {
    mainWindow.close();
  }
});
