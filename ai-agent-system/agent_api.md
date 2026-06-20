# Agent Task API — App/小程序接入指南

## 概述

Agent Task API 将 Multi-Agent Runtime 暴露为标准 HTTP + WebSocket 接口，
让 Web App、小程序、移动端等任何前端都能异步驱动 AI Worker 团队。

**不需要引入 Celery、Redis、数据库**，基于 ThreadPoolExecutor + JSON 文件即可运行。

## 快速开始

```bash
cd ai-agent-system
python server.py
# → http://localhost:8000
# → WebSocket: ws://localhost:8000/ws
```

## curl 完整演示

以下示例可在**另一个终端**中运行（需要服务已启动）。

### Step 1: 创建一个 worker_task

```bash
curl -s -X POST http://localhost:8000/agent/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "type": "worker_task",
    "description": "列出当前项目中的所有 Python 文件并分析它们的职责",
    "worker_name": "Alex"
  }' | python -m json.tool
```

响应：
```json
{
  "ok": true,
  "task_id": "task_20260620_173045_a1b2c3",
  "status": "pending",
  "message": "任务已创建，类型=worker_task"
}
```

### Step 2: 轮询任务状态

```bash
# 把 TASK_ID 替换为上一步返回的值
TASK_ID="task_20260620_173045_a1b2c3"

curl -s http://localhost:8000/agent/tasks/$TASK_ID | python -m json.tool
```

```json
{
  "task_id": "task_20260620_173045_a1b2c3",
  "type": "worker_task",
  "status": "running",
  "description": "列出当前项目中的所有 Python 文件并分析它们的职责",
  "worker_name": "Alex",
  "progress": "Worker-Alex 执行中...",
  "created_at": "2026-06-20 17:30:45",
  "updated_at": "2026-06-20 17:30:46",
  "result_preview": null,
  "error": null,
  "log_count": 2
}
```

### Step 3: 查看执行日志

```bash
curl -s http://localhost:8000/agent/tasks/$TASK_ID/logs | python -m json.tool
```

### Step 4: 获取最终结果

```bash
curl -s http://localhost:8000/agent/tasks/$TASK_ID/result | python -m json.tool
```

```json
{
  "task_id": "task_20260620_173045_a1b2c3",
  "status": "completed",
  "result": "{\"status\":\"success\",\"summary\":\"项目包含 14 个 Python 模块...\",\"artifacts\":[\"analysis.md\"]}",
  "error": null,
  "artifacts": [{"path": "analysis.md", "type": "markdown"}]
}
```

### 一行轮询脚本

```bash
TASK_ID="your-task-id"
while true; do
  status=$(curl -s http://localhost:8000/agent/tasks/$TASK_ID | python -c "import sys,json; print(json.load(sys.stdin)['status'])")
  echo "[$(date +%H:%M:%S)] 状态: $status"
  case $status in
    completed|failed|cancelled) break ;;
    *) sleep 2 ;;
  esac
done
echo "任务结束"
```

### 测试验证（不需要真实 LLM 密钥）

```bash
# 这些请求会立即返回 400/422，验证校验逻辑
# 无效 type
curl -s -X POST http://localhost:8000/agent/tasks \
  -H "Content-Type: application/json" \
  -d '{"type":"hack","description":"test","worker_name":"Alex"}' | python -m json.tool
# → 422: 无效的 task type

# 无效 worker_name
curl -s -X POST http://localhost:8000/agent/tasks \
  -H "Content-Type: application/json" \
  -d '{"type":"worker_task","description":"test","worker_name":"Ghost"}' | python -m json.tool
# → 400: 未知 Worker

# 空描述
curl -s -X POST http://localhost:8000/agent/tasks \
  -H "Content-Type: application/json" \
  -d '{"type":"worker_task","description":"","worker_name":"Alex"}' | python -m json.tool
# → 422: description 不能为空
```

## API 接口

### 1. 创建任务 `POST /agent/tasks`

异步创建任务，**立即返回 task_id**，后台执行。

**Request Body:**
```json
{
  "type": "worker_task",
  "description": "检查当前项目的代码质量问题",
  "worker_name": "Alex"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| type | string | ✅ | `worker_task` / `verified_task` / `project_pipeline_task` |
| description | string | ✅ | 任务描述，越具体越好 |
| worker_name | string | 条件必填 | `worker_task` 和 `verified_task` 必须指定 |
| project_name | string | 否 | 项目名称（project_pipeline_task 可选） |

**Response (200):**
```json
{
  "ok": true,
  "task_id": "task_20260620_173045_a1b2c3",
  "status": "pending",
  "message": "任务已创建，类型=worker_task"
}
```

**错误码:**

| 状态码 | 原因 |
|--------|------|
| 422 | type/description 非法或缺失 |
| 400 | worker_name 不存在或必填字段缺失 |

### 2. 查询任务 `GET /agent/tasks/{task_id}`

返回任务状态、进度、结果摘要（不含完整 logs）。

```json
{
  "task_id": "task_20260620_173045_a1b2c3",
  "type": "worker_task",
  "status": "completed",
  "description": "检查代码质量",
  "worker_name": "Alex",
  "progress": "完成",
  "created_at": "2026-06-20 17:30:45",
  "updated_at": "2026-06-20 17:31:12",
  "result_preview": "{\"status\":\"success\",...",
  "error": null,
  "log_count": 8
}
```

### 3. 任务详情 `GET /agent/tasks/{task_id}/detail`

返回完整信息，包含全部 logs、result、artifacts。

### 4. 任务日志 `GET /agent/tasks/{task_id}/logs`

```json
{
  "task_id": "task_...",
  "status": "completed",
  "log_count": 8,
  "logs": [
    "[2026-06-20 17:30:45] 任务开始: type=worker_task, worker=Alex",
    "[2026-06-20 17:31:12] 完成: {\"status\":\"success\"...}"
  ]
}
```

### 5. 任务结果 `GET /agent/tasks/{task_id}/result`

```json
{
  "task_id": "task_...",
  "status": "completed",
  "result": "{\"status\":\"success\",\"summary\":\"...\",\"artifacts\":[]}",
  "artifacts": [{"path": "output/report.md", "type": "markdown"}]
}
```

- status=pending → 400 "任务尚未开始执行"
- status=running → 200 "任务仍在执行中，请稍后重试"

### 6. 取消任务 `POST /agent/tasks/{task_id}/cancel`

- **pending** 任务 → 立即标记为 `cancelled`
- **running** 任务 → 标记 `cancel_requested=true`，当前操作完成后停止
- **已完成/失败/已取消** → 返回 "无需取消"

### 7. 任务列表 `GET /agent/tasks?limit=20&offset=0`

按创建时间倒序，分页返回。

## WebSocket 实时推送

连接 `ws://localhost:8000/ws`，任务状态变化时自动推送：

```json
{
  "type": "agent_task_update",
  "task_id": "task_20260620_173045_a1b2c3",
  "status": "running",
  "progress": "Worker-Alex 执行中...",
  "message": ""
}
```

**推送时机：** pending→running、进度更新、completed、failed、cancelled。

## 前端集成模式

### 模式 A：轮询（最简单）

```javascript
// 1. 创建任务
const { task_id } = await fetch('/agent/tasks', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    type: 'worker_task',
    description: '分析项目结构',
    worker_name: 'Alex',
  })
}).then(r => r.json());

// 2. 轮询直到完成
while (true) {
  const { status, result_preview } = await fetch(`/agent/tasks/${task_id}`).then(r => r.json());
  if (status === 'completed' || status === 'failed' || status === 'cancelled') break;
  await new Promise(r => setTimeout(r, 2000)); // 每 2 秒查询一次
}

// 3. 获取结果
const { result } = await fetch(`/agent/tasks/${task_id}/result`).then(r => r.json());
```

### 模式 B：WebSocket（实时推送，推荐）

```javascript
const ws = new WebSocket('ws://localhost:8000/ws');

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  if (msg.type === 'agent_task_update') {
    console.log(`任务 ${msg.task_id}: ${msg.status} (${msg.progress})`);
    if (msg.status === 'completed') {
      // 通过 REST API 获取完整结果
      fetch(`/agent/tasks/${msg.task_id}/result`).then(r => r.json()).then(console.log);
    }
  }
};

// 创建任务后 WebSocket 自动收到状态推送
```

### 模式 C：轮询 + WebSocket 混合（小程序推荐）

- 创建任务后先通过 WebSocket 监听 `agent_task_update`
- 同时设置 5 秒超时轮询兜底（兼容 WebSocket 断连）
- 任务完成后清理轮询 timer

## 任务类型说明

| type | 说明 | 后台操作 |
|------|------|----------|
| `worker_task` | 指派单个 Worker 执行任务 | `run_worker(cfg, description, fresh_session=True)` |
| `verified_task` | Worker 执行 + Sophia∥Nathaniel 验证闭环 | `delegate_with_verification(workers, worker_name, description)` |
| `project_pipeline_task` | 全项目 Pipeline（DAG 多步骤） | `project_setup()` → `run_project_pipeline()` |

## 安全边界

- ✅ task type 白名单校验（仅接受 3 种类型）
- ✅ worker_name 必须在 workers.json 中存在
- ✅ description 不能为空
- ✅ 接口不直接执行任意 shell 命令
- ✅ 不上传文件自动执行
- ✅ 不引入外部数据库、Celery、Redis

## 与现有聊天室的关系

- 聊天室 WebSocket（`/ws`）继续工作，新增 `agent_task_update` 消息类型
- 聊天室的任务派发（`type: "task"`）不变
- Task API 是**互补通道**，用于程序化接入；聊天室用于人工交互
