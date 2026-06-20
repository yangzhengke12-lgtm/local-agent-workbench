# Local Agent Workbench

一个 local-first 的 Agent 桌面工作台。它把多 Agent Runtime 封装成异步任务服务，并提供 Electron 桌面端来选择本地工作区、提交任务、查看执行日志和结果。

这个项目不是通用联网搜索 Agent。当前版本默认处理本地项目、私有上下文和后端任务流；联网搜索、企业知识库、飞书、数据库等能力可以通过 tools/provider adapter 后续接入。

[![Tests](https://img.shields.io/badge/tests-207%20passed-green)](.)
[![Python](https://img.shields.io/badge/python-3.12-blue)](.)
[![Desktop](https://img.shields.io/badge/desktop-Electron-lightgrey)](.)

## What It Does

- 多 Agent 任务调度：Manager、Deputy、5 个 Worker 通过 `workers.json` 配置。
- 异步任务 API：`POST /agent/tasks` 立即返回 `task_id`，后台线程池执行。
- 实时状态更新：WebSocket 推送 `agent_task_update`。
- 任务持久化：任务状态、日志、结果写入本地 JSON 文件。
- 本地桌面工作台：Electron UI 支持 workspace、任务列表、日志和结果面板。
- 工程化 Runtime：`manager.py` 作为入口，核心逻辑拆到 `runtime/` 模块。
- 测试覆盖：Task API、DAG、状态机、持久化、验证闭环等共 207 个测试。

## Project Structure

```text
local-agent-workbench/
├── manager.py              # Runtime facade and CLI entry
├── server.py               # FastAPI backend + WebSocket + task API
├── workers.json            # Agent/team configuration
├── runtime/                # Agent runtime modules
│   ├── agent_task.py       # Task model, store, executor
│   ├── pipeline.py         # DAG pipeline execution
│   ├── tools.py            # Tool schemas and execution
│   ├── workers.py          # Worker execution
│   ├── verification.py     # Verification loop
│   └── ...
├── desktop/                # Electron desktop workbench
├── tests/                  # Automated tests
├── agent_api.md            # API integration guide
└── requirements.txt
```

## Requirements

- Python 3.12+
- Node.js 18+
- npm
- LLM API key, such as `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`

The desktop app starts the FastAPI backend automatically. You can also run the backend alone for API testing.

## Setup

```bash
git clone https://github.com/<your-name>/local-agent-workbench.git
cd local-agent-workbench

python -m venv .venv
.venv\Scripts\activate

pip install -r requirements.txt
```

Create a `.env` file in the project root:

```env
ANTHROPIC_API_KEY=your-anthropic-api-key
ANTHROPIC_BASE_URL=
ANTHROPIC_MODEL=deepseek-v4-pro

OPENAI_API_KEY=
OPENAI_BASE_URL=
```

Only fill the provider you actually use. `.env` is ignored by git.

## Run Backend Only

```bash
python server.py
```

Open:

```text
http://localhost:8000
```

Useful endpoints:

```text
GET    /health
GET    /agent/workers
POST   /agent/workspace
POST   /agent/tasks
GET    /agent/tasks
GET    /agent/tasks/{task_id}
GET    /agent/tasks/{task_id}/logs
GET    /agent/tasks/{task_id}/result
POST   /agent/tasks/{task_id}/cancel
WS     /ws
```

## Run Desktop Workbench

```bash
cd desktop
npm install
npm start
```

Desktop flow:

1. Choose a local workspace folder.
2. Select task type and worker.
3. Submit a task.
4. Watch logs update in real time.
5. Inspect final result and artifacts.

The current repository does not include a packaged installer or standalone exe. The desktop app runs in development mode through Electron.

## Example API Request

```bash
curl -X POST http://localhost:8000/agent/tasks ^
  -H "Content-Type: application/json" ^
  -d "{\"type\":\"worker_task\",\"worker_name\":\"Sophia\",\"description\":\"Review runtime/agent_task.py for API safety issues\"}"
```

Example response:

```json
{
  "task_id": "abc123",
  "status": "pending"
}
```

Then poll:

```bash
curl http://localhost:8000/agent/tasks/abc123
curl http://localhost:8000/agent/tasks/abc123/logs
curl http://localhost:8000/agent/tasks/abc123/result
```

For a complete API walkthrough, see [agent_api.md](agent_api.md).

## Task Types

```text
worker_task
verified_task
project_pipeline_task
```

Input validation includes:

- task type whitelist
- worker name whitelist from `workers.json`
- non-empty task description
- no arbitrary shell command endpoint exposed through the task API

## Tests

```bash
python -m pytest -q
```

Expected result:

```text
207 passed
```

Desktop JavaScript syntax check:

```bash
cd desktop
node --check main.js
node --check preload.js
node --check renderer.js
node --check i18n.js
```

## Product Boundary

Current version:

- local-first
- desktop + API driven
- suitable for local project analysis, task delegation, logs, results, and workflow demos

Not included by default:

- public web search
- enterprise SSO
- production database backend
- packaged installer
- external systems such as Feishu, Jira, GitLab, or SQL databases

Those are intended to be added as controlled tool adapters, not hardcoded into the Agent Runtime.

## License

MIT
