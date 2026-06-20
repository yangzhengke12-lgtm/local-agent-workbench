# 面试 Demo 脚本 —— 2 分钟展示 Multi-Agent 异步任务服务

## 预备（开始前 30 秒）

```bash
# 终端 1: 启动服务
cd local-agent-workbench
python server.py
# 看到 "Uvicorn running on http://0.0.0.0:8000" 即可
```

---

## Demo 流程（2 分钟）

### 0:00 — 打开架构图（15 秒）

> "这是我独立设计开发的多 Agent 异步执行后端。它不是对话机器人，而是一个可以被 Web / App / 小程序接入的标准异步任务服务。"

展示 README.md 的架构图，手指三层：
1. **接入层**：App/小程序通过 REST API + WebSocket 接入
2. **执行层**：TaskExecutor 线程池 + 7 智能体 Runtime
3. **模型层**：DeepSeek / MiniMax / GPT 多厂商自动路由

> "核心代码 14 个模块，严格单向依赖，零循环引用。206 个测试，5.9 秒全部通过。"

### 0:15 — 创建任务（20 秒）

> "一个真实的使用场景：前端 App 用户提交了一个代码审查需求。"

```bash
curl -s -X POST http://localhost:8000/agent/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "type": "verified_task",
    "description": "审查 runtime/agent_task.py 的代码质量和安全隐患",
    "worker_name": "Alex"
  }' | python -m json.tool
```

> "注意看——API 立即返回了 task_id，没有阻塞。后端已经在后台执行了。"

### 0:35 — 查状态 + 日志（25 秒）

```bash
TASK_ID="<上一步的 task_id>"

# 查询状态
curl -s http://localhost:8000/agent/tasks/$TASK_ID | python -m json.tool
```

> "这是任务状态：type、status、progress、log_count。前端可以用这个做进度条。"

```bash
# 查看执行日志
curl -s http://localhost:8000/agent/tasks/$TASK_ID/logs | python -m json.tool
```

> "执行日志记录了每一步——任务开始、Worker 执行、工具调用。方便排查问题。"

### 1:00 — 获取结果（15 秒）

```bash
# 获取最终结果
curl -s http://localhost:8000/agent/tasks/$TASK_ID/result | python -m json.tool
```

> "最终结果包含 Worker 的结构化输出和 artifact 文件列表。整个流程从前端提交到拿到结果，完全异步。"

### 1:15 — WebSocket 实时推送（15 秒）

> "如果你的 App 需要实时更新，我们还有 WebSocket。用 wscat 演示一下："

```bash
# 终端 3: 监听 WebSocket
wscat -c ws://localhost:8000/ws
# 然后回到终端 1 创建新任务，实时看到 agent_task_update 推送
```

> "任务状态每次变化——pending → running → completed——都会通过 WebSocket 推送到前端。不需要轮询。"

### 1:30 — 安全校验（15 秒）

> "安全校验也做完了，不会让外部 App 随便执行任意命令。"

```bash
# 非法 type → 422
curl -s -X POST http://localhost:8000/agent/tasks \
  -H "Content-Type: application/json" \
  -d '{"type":"shell_exec","description":"rm -rf /"}' | python -m json.tool

# 非法 worker → 400
curl -s -X POST http://localhost:8000/agent/tasks \
  -H "Content-Type: application/json" \
  -d '{"type":"worker_task","description":"test","worker_name":"Hacker"}' | python -m json.tool
```

> "type 白名单、worker_name 校验、描述非空——三层输入验证，在生产代码里实现的。"

### 1:45 — 测试覆盖率（15 秒）

```bash
python -m pytest tests/ -q
```

> "206 个测试，包括 32 个 Task API 测试。所有 Runtime 入口都被 mock，测试环境用同步执行器，零后台线程泄漏。生产路径不受影响。"

---

## Demo 结束语（备用）

> "总结一下：我做的不是一个 Agent 脚本，而是一个完整的异步 Agent 执行后端。它有标准 REST API、任务持久化、后台线程池、WebSocket 推送、三层输入验证、完整测试覆盖。可以被任何前端业务系统接入。"

---

## 面试官可能会问的跟进问题

| 问题 | 回答要点 |
|------|----------|
| "为什么不用 Celery/Redis？" | 最小可用闭环。14 模块 + JSON 文件，30 秒启动，零运维成本。需要时可以替换 TaskStore 的持久化后端 |
| "任务失败了怎么处理？" | verified_task 有验证闭环（Sophia + Nathaniel 并行审查 → 自动重试）。失败后有 error 字段和完整日志 |
| "怎么保证 Worker 不会搞破坏？" | 双层权限：API 层 tools 白名单 + 运行时 worker_execute_tool 二次校验。不是 prompt 软约束 |
| "测试为什么用同步模式？" | 避免后台线程逃逸调用真实 LLM。`_sync_mode` 默认 False，测试时设为 True。shutdown(wait=True) 在 patcher.stop() 之前执行 |
| "怎么扩展到 100 个用户？" | TaskExecutor 已是线程池（可调 max_workers）。TaskStore 目前是 JSON 文件，可以替换为 SQLite 或 Redis，接口不变 |

---

## 演示版本的文件清单

```
AI-Agent管理系统/
├── README.md           ← 架构图 + 快速启动（面试官先看这个）
├── agent_api.md        ← 完整 API 文档 + curl 示例
├── server.py           ← FastAPI 后端
├── runtime/            ← 14 个深模块
├── tests/              ← 206 个测试
└── examples/
    └── demo_task_flow.md  ← 本文件（2 分钟面试脚本）
```

---

## Desktop Workbench Demo（30 秒）

```bash
# 终端 1: 启动桌面应用（自动启动 Python 后端）
cd local-agent-workbench/desktop
npm install  # 仅首次
npm start
# 等待 Electron 窗口出现，状态栏显示 "connected"
```

> "这是 Agent Desktop Workbench —— 不是网页套壳，而是一个真正管理 workspace、任务、日志和结果的本地执行平台。"

### 演示流程

1. **0:00** — 在 Workspace 输入框中输入项目路径（如 `D:\projects\my-service`），点击 **Set**
   - 右侧状态立即反馈 `{"ok": true, "workspace": "..."}`

2. **0:05** — 底部输入框输入任务描述："审查当前项目的代码质量"，选择 `verified_task` + `Sophia`，点击 **Create**
   - 左侧任务列表出现新任务，蓝色 running 状态点

3. **0:10** — 点击新任务，中央面板显示：
   - 任务详情（ID、type、worker、workspace_path）
   - 实时日志流（深色终端风格，自动滚动）

4. **0:20** — 任务完成后：
   - 状态点变绿（completed）
   - 右侧显示结果和 artifacts 文件列表

5. **0:25** — 关闭窗口 → Python 后端自动退出

> "4 面板布局 + WebSocket 实时推送 + Electron 管理 Python 子进程生命周期。它不是聊天机器人，而是一个面向本地项目任务的执行平台。"

### 演示版本文件清单

```
AI-Agent管理系统/
├── desktop/
│   ├── main.js              ← Electron 主进程
│   ├── renderer.html        ← 4 面板 UI
│   ├── renderer.js          ← 全部 UI 逻辑
│   └── style.css            ← 样式
├── server.py                ← FastAPI（含 /health, /agent/workspace）
├── agent_api.md             ← API 文档 + curl 示例
└── README.md                ← 架构图 + Desktop Workbench 章节
```
