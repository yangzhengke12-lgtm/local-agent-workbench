# Multi-Agent 层级协作系统 v4.3

**异步 Agent 执行后端 —— 可被 Web / App / 小程序接入**

[![Tests](https://img.shields.io/badge/tests-206%20passed-green)](.)
[![Python](https://img.shields.io/badge/python-3.12-blue)](.)
[![Arch](https://img.shields.io/badge/arch-14%20modules%20zero%20circular%20deps-success)](.)

## 一句话

不是"一个 Agent 对话机器人"，而是**把多 Agent  Runtime 封装成了标准异步任务服务**：
前端通过 REST API 提交需求 → 后端线程池异步执行 Agent 工作流 → 轮询/WebSocket 返回进度、日志和结果。

## 架构图

```
┌─────────────────────────────────────────────────────────┐
│                   App / 小程序 / Web                      │
│              POST /agent/tasks  ·  WebSocket              │
└──────────────┬──────────────────────────┬────────────────┘
               │                          │
               ▼                          ▼
┌──────────────────────────────────────────────────────────┐
│                   server.py (FastAPI)                     │
│   ┌─────────────┐  ┌──────────────┐  ┌────────────────┐ │
│   │ 聊天室 /ws   │  │ Agent Task   │  │ 文件上传 /upload│ │
│   │ (WebSocket)  │  │ API (REST)   │  │                │ │
│   └─────────────┘  └──────┬───────┘  └────────────────┘ │
│                           │                              │
│                    ┌──────▼────────┐                     │
│                    │  TaskExecutor  │                     │
│                    │ (线程池, 4workers)│                  │
│                    └──────┬────────┘                     │
└───────────────────────────┼──────────────────────────────┘
                            │
┌───────────────────────────┼──────────────────────────────┐
│                   manager.py (732 行)                     │
│   ┌───────────────────────▼──────────────────────────┐   │
│   │                runtime/ (13 modules)               │   │
│   │                                                   │   │
│   │  contracts.py  →  sanitize.py  →  pure_functions  │   │
│   │       ↓               ↓               ↓           │   │
│   │  config.py  →  routing.py  →  persistence.py     │   │
│   │       ↓               ↓               ↓           │   │
│   │  llm.py  →  tools.py  →  workers.py  →  verif... │   │
│   │       ↓               ↓               ↓           │   │
│   │  pipeline.py  →  manager_tools.py                 │   │
│   │       ↓               ↓                           │   │
│   │  agent_task.py (Task API 层)                       │   │
│   └───────────────────┬───────────────────────────────┘   │
└───────────────────────┼───────────────────────────────────┘
                        │
         ┌──────────────┼──────────────┐
         ▼              ▼              ▼
   ┌──────────┐  ┌──────────┐  ┌──────────┐
   │ DeepSeek │  │ MiniMax  │  │ GPT 中转  │
   │ v4-pro   │  │  M2.7    │  │  5.4/5.5  │
   └──────────┘  └──────────┘  └──────────┘
```

**依赖方向严格单向**：`manager.py → runtime/*`，`runtime/*` 零条反向引用。

## 快速启动

```bash
# 1. 安装依赖
pip install fastapi uvicorn anthropic openai python-docx

# 2. 配置 API 密钥（.env 或环境变量）
#    至少需要 ANTHROPIC_API_KEY（DeepSeek via Anthropic SDK）
export ANTHROPIC_API_KEY=sk-your-key-here

# 3. 启动
cd ai-agent-system
python server.py
# → http://localhost:8000
# → 聊天室: http://localhost:8000
# → WebSocket: ws://localhost:8000/ws
# → API 文档: agent_api.md
```

## Desktop Workbench (本地 Agent 工作台) <sup>new</sup>

Electron 桌面应用，提供 4 面板任务管理界面。桌面端负责 workspace、任务管理和交互，后端负责异步 Agent 执行。

### 启动

```bash
# 1. 安装 Electron（首次）
cd ai-agent-system/desktop
npm install

# 2. 启动桌面应用
npm start
# Electron 自动启动 Python 后端（server.py），无需手动运行
# 若后端已在 8000 端口运行，Electron 自动复用
```

### 4 面板布局

| 面板 | 位置 | 功能 |
|------|------|------|
| **Workspace + Task List** | 左侧 (280px) | 选择/切换本地项目目录；任务列表带状态色点指示 |
| **Task Detail + Log** | 中央 (flex) | 任务详情头 + 实时执行日志流（深色终端风格） |
| **Result + Artifacts** | 右侧 (300px) | 结果展示 + 产物文件列表 |
| **Input Bar** | 底部 (64px) | 任务描述 + type 选择 + worker 选择 + Create 按钮 |

UI 与 WebSocket 实时联动：任务状态变化 → `agent_task_update` 推送 → 4 面板即时刷新。

### 工作台架构

```
┌──────────────────────────────────────────┐
│          Electron Main Process            │
│  ┌────────────────────────────────────┐  │
│  │  python server.py (child_process)   │  │
│  │  FastAPI :8000 + WebSocket          │  │
│  └────────────────────────────────────┘  │
│  ┌────────────────────────────────────┐  │
│  │  BrowserWindow                      │  │
│  │  renderer.html + renderer.js        │  │
│  │  (vanilla HTML/CSS/JS, 零框架)      │  │
│  └────────────────────────────────────┘  │
└──────────────────────────────────────────┘
```

## 核心能力

| 层 | 能力 | 说明 |
|----|------|------|
| **Agent Runtime** | 7 智能体团队 | Manager + Deputy + 5 Workers（各司其职） |
| | 16 工具 + 24 管理工具 | 文件读写、代码搜索、GitHub、文档转换 |
| | 4 厂商 LLM 路由 | DeepSeek → MiniMax → GPT 自动回退 |
| | DAG Pipeline | Kahn 拓扑排序 + 并行执行 + 阻断传播 |
| | 验证闭环 | Sophia∥Nathaniel → merge verdict → auto retry |
| | 持久化恢复 | WorkflowRun JSON，中断可恢复 |
| **工程架构** | 深模块化 | 14 文件，零循环依赖 |
| | 懒加载 Provider | `import manager` 不需要 API 密钥 |
| | 兼容性极强 | 重构 4000 行，174 测试一行不改全部通过 |
| **产品接入** | 异步任务 API | `POST /agent/tasks` → task_id → 轮询 / WebSocket |
| | 3 种任务类型 | worker_task / verified_task / project_pipeline_task |
| | 任务生命周期 | pending → running → completed / failed / cancelled |
| | 实时推送 | WebSocket `agent_task_update` |
| | 零外部依赖 | 无数据库、无 Redis、无 Celery |

## 项目结构

```
ai-agent-system/
├── manager.py              # 入口（732 行，re-export runtime 模块）
├── server.py               # FastAPI + WebSocket + Task API（~400 行）
├── workers.json            # Worker 配置（零代码改团队）
├── chat.html               # 聊天室前端
│
├── runtime/                # 深模块层（13 文件，~3500 行）
│   ├── contracts.py        #   数据合约（TaskNodeStatus + 5 dataclass）
│   ├── sanitize.py         #   消息净化（跨 provider thinking 块过滤）
│   ├── pure_functions.py   #   纯函数（归一化/合并/预算/产物）
│   ├── config.py           #   懒加载 provider 配置
│   ├── routing.py          #   复杂度路由
│   ├── persistence.py      #   持久化（会话/看板/知识库/WorkflowRun）
│   ├── llm.py              #   多厂商 LLM 调用层
│   ├── tools.py            #   16 工具 + ask_coworker 回调注入
│   ├── workers.py          #   Worker 执行层
│   ├── verification.py     #   验证闭环（Sophia∥Nathaniel）
│   ├── pipeline.py         #   DAG 引擎（Kahn/并行/阻断/恢复）
│   ├── manager_tools.py    #   24 Manager 工具 schema
│   └── agent_task.py       #   Task API 层（数据模型/持久化/执行器）
│
├── tests/                  # 206 测试（10 文件）
│   ├── test_agent_api.py   #   Task API 测试（32 个）
│   ├── test_schema.py      #   归一化函数测试
│   ├── test_state_machine.py # 状态机转移测试
│   ├── test_dag_engine.py  #   DAG 拓扑/阻断测试
│   ├── test_budget.py      #   预算熔断测试
│   ├── test_persistence.py #   持久化往返测试
│   ├── test_integration.py #   验证闭环集成测试
│   └── ...
│
├── agent_api.md            # App/小程序接入指南
├── examples/               # Demo 示例
├── desktop/                # Electron 桌面工作台
│   ├── main.js             #   主进程（启动后端 + 窗口管理）
│   ├── renderer.html       #   4 面板 UI 结构
│   ├── renderer.js         #   UI 逻辑（~300 行 vanilla JS）
│   ├── style.css           #   样式（对齐 chat.html 色板）
│   └── package.json        #   Electron 项目配置
└── agent_tasks.json        # 任务持久化（自动生成）
```

## 团队

| 成员 | 角色 | 工具数 |
|------|------|--------|
| **Manager** | 技术总监 · 任务调度 | 24 |
| **Victor** | 副经理 · 决策复核 | — |
| **Alex** | 高级开发 · 架构/PR | 11 |
| **Sophia** | 代码审查 · 安全审计 | 8 |
| **Marcus** | DevOps · CI/CD | 11 |
| **Elena** | 技术写作 · 文档 | 8 |
| **Nathaniel** | 测试工程 · 覆盖率 | 8 |

## 版本演进

| 版本 | 核心变化 |
|------|----------|
| v1 | 单 Agent，跑通 DeepSeek API |
| v2 | Manager + 5 Worker 分层，workers.json 配置驱动 |
| v3 | 4 厂商 LLM 路由，成本优化，项目动态分工 |
| v4 | 状态机 + DAG Pipeline + 验证闭环 + 持久化恢复 |
| v4.3 | **深模块架构重构**（14 文件零循环依赖）+ **Task API**（异步任务服务） |
| v4.4 | **Desktop Workbench**（Electron 桌面工作台 + 4 面板 UI + workspace 管理） |

## License

MIT
