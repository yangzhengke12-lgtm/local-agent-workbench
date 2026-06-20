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
const { app, BrowserWindow, dialog } = require('electron');
const { spawn } = require('child_process');
const http = require('http');
const path = require('path');
const net = require('net');

// ── 配置 ──
const PORT = 8000;
const HEALTH_URL = `http://localhost:${PORT}/health`;
const HEALTH_RETRIES = 30;
const HEALTH_INTERVAL_MS = 1000;

let mainWindow = null;
let pythonProcess = null;

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

  console.log(`[main] Starting Python: python "${serverPy}" (cwd: ${cwd})`);

  // 尝试 python，失败则 python3
  const pythonCmd = process.platform === 'win32' ? 'python' : 'python3';

  pythonProcess = spawn(pythonCmd, [serverPy], {
    cwd: cwd,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });

  pythonProcess.stdout.on('data', (data) => {
    console.log(`[python] ${data.toString().trim()}`);
  });

  pythonProcess.stderr.on('data', (data) => {
    console.error(`[python:err] ${data.toString().trim()}`);
  });

  pythonProcess.on('close', (code) => {
    console.log(`[main] Python process exited with code ${code}`);
    pythonProcess = null;
  });

  pythonProcess.on('error', (err) => {
    console.error(`[main] Failed to start Python: ${err.message}`);
    pythonProcess = null;
  });
}

// ── 健康检查：轮询 /health ──
function checkHealth(retries) {
  return new Promise((resolve, reject) => {
    let attempts = 0;

    function tryHealth() {
      attempts++;
      const req = http.get(HEALTH_URL, (res) => {
        let body = '';
        res.on('data', (chunk) => { body += chunk; });
        res.on('end', () => {
          if (res.statusCode === 200) {
            console.log('[main] Health check OK');
            resolve();
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
  if (pythonProcess) {
    console.log('[main] Killing Python process...');
    if (process.platform === 'win32') {
      // Windows: taskkill 确保子进程树全清
      spawn('taskkill', ['/pid', pythonProcess.pid.toString(), '/f', '/t']);
    } else {
      pythonProcess.kill('SIGTERM');
    }
    pythonProcess = null;
  }
}

// ── 创建窗口 ──
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1024,
    minHeight: 700,
    title: 'Agent Desktop Workbench',
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
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
    // 1. 检测端口
    const portFree = await checkPort(PORT);

    if (portFree) {
      console.log(`[main] Port ${PORT} is free, starting Python backend...`);
      startPythonServer();
    } else {
      console.log(`[main] Port ${PORT} is occupied, reusing existing server`);
    }

    // 2. 等待健康检查
    await checkHealth(HEALTH_RETRIES);

    // 3. 创建窗口
    createWindow();
  } catch (err) {
    dialog.showErrorBox(
      '启动失败',
      `无法连接到 Agent 后端 (${HEALTH_URL}):\n\n${err.message}\n\n请确认: python server.py 可正常启动`
    );
    app.quit();
  }
});

app.on('window-all-closed', () => {
  killPython();
  app.quit();
});

app.on('before-quit', () => {
  killPython();
});

app.on('activate', () => {
  // macOS: 点 dock 图标重新创建窗口
  if (mainWindow === null) {
    createWindow();
  }
});
