# 团队聊天室 — 架构设计方案

## 最终推荐：方案 A — 轻量 Web 版

### 技术栈

| 层 | 技术 | 原因 |
|---|------|------|
| 后端 | Python FastAPI + WebSocket | 和 manager.py 同语言，直接 import 复用 |
| 前端 | 单页 HTML + Vanilla JS + CSS | 零依赖、零构建、一个文件 |
| 实时推送 | WebSocket | 员工干活的过程实时推送到聊天框 |
| 持久化 | JSON 文件 | 消息历史存 `chat_history.json` |

### 架构

```
浏览器 (聊天 UI)
    │ WebSocket
    ▼
FastAPI 后端 (server.py, 约 300 行)
    │ import manager
    ▼
manager.py (现有的 run_worker / delegate_task)
    │ API 调用
    ▼
DeepSeek API (Worker 执行)
```

### 消息流

```
1. Boss 在聊天框输入 "@亚历克斯 写一个 calculator.py"
2. 前端 WebSocket.send({type: "task", worker: "亚历克斯", content: "..."})
3. 后端收到 → 开线程调 run_worker("亚历克斯", task)
4. Worker 每输出一行 → 后端 WebSocket.push({type: "worker_msg", worker: "亚历克斯", text: "..."})
5. Worker 完成 → 后端 push({type: "task_done", worker: "亚历克斯", result: "..."})
6. Manager 审核 → 后端 push({type: "manager_review", text: "..."})
7. 前端实时渲染聊天气泡
```

### UI 设计

```
┌──────────────────────────────────────────────┐
│  🏢 AI 团队工作群                    7 人在线  │
├──────────────────────────────────────────────┤
│                                              │
│  👤 Boss: @亚历克斯 写一个 calculator.py       │
│                                              │
│  🔧 亚历克斯: 收到！                           │
│  🔧 亚历克斯: [读取 calculator.py...]          │
│  🔧 亚历克斯: [写入文件...] ✅                  │
│  🔧 亚历克斯: 完成，文件已创建                   │
│                                              │
│  🤖 Manager: 审核通过 ✅                      │
│  🤖 Manager: 老板，calculator.py 已完成        │
│                                              │
├──────────────────────────────────────────────┤
│  [@亚历克斯] [@索菲亚] [输入消息...]     [发送] │
└──────────────────────────────────────────────┘
```

### 头像映射

| 成员 | 头像 | 颜色 |
|------|------|------|
| Boss | 👤 | 金色 |
| Manager | 🤖 | 蓝色 |
| 维克托 | 🎩 | 紫色 |
| 亚历克斯 | 💻 | 绿色 |
| 索菲亚 | 🔍 | 红色 |
| 马库斯 | ⚙️ | 橙色 |
| 埃琳娜 | 📝 | 粉色 |
| 纳撒尼尔 | 🧪 | 青色 |

### 启动方式

```bash
pip install fastapi uvicorn websockets
python server.py
# 浏览器打开 http://localhost:8000
```

### 和现有系统的关系

- 100% 复用 manager.py（不改一行）
- server.py 是薄封装层（~300 行）
- chat.html 是独立前端文件
- CLI 模式 (`python manager.py`) 不受影响

## 方案 B — 桌面客户端（不推荐）

Electron/Tauri 打包，开发周期 3-5 天，打包体积 150MB+。只比方案 A 多了系统托盘和开机自启——但这可以用 Windows 任务计划程序 + 浏览器书签替代。

## 建议

先用方案 A 快速上线（半天开发），跑通后再考虑包装成桌面客户端。
