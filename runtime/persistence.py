"""持久化层 —— Worker 会话、任务看板、知识库、项目状态、WorkflowRun。

所有文件 I/O 统一经过这里。不调用外部 API，不依赖 LLM。
"""
import json
import os
from datetime import datetime

from runtime.config import APP_VERSION, APP_RUNTIME_NAME, PROVIDERS, _init_providers
from runtime.contracts import WorkflowRun, Budget

# ── 全局 Worker 配置（供工具间访问） ────────────────────────
_workers_config: dict = {}
_deputy_config: dict = {}

# ── Worker 会话记忆 ───────────────────────────────────────
worker_sessions: dict[str, list] = {}
SESSION_FILE = "worker_sessions.json"
SCORE_FILE = "worker_scores.json"
TASK_BOARD_FILE = "task_board.json"
KNOWLEDGE_FILE = "team_knowledge.json"
PROJECT_STATE_DIR = "project_states"


def save_sessions():
    """持久化 Worker 对话到磁盘。"""
    serializable = {}
    for key, msgs in worker_sessions.items():
        serializable[key] = msgs
    try:
        with open(SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2)
    except Exception:
        pass  # 保存失败不打断流程


def load_sessions():
    """从磁盘恢复 Worker 对话。"""
    if not os.path.exists(SESSION_FILE):
        return
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key, msgs in data.items():
            worker_sessions[key] = msgs
    except Exception:
        pass


# ── JSON 读写辅助 ──────────────────────────────────────────

def _load_json(filepath: str, default: list) -> list:
    if not os.path.exists(filepath):
        return default
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(filepath: str, data):
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ── 任务看板 ──────────────────────────────────────────────

def create_task(description: str, priority: str = "medium",
                assigned_worker: str = "") -> str:
    """在任务看板中创建一个任务。"""
    tasks = _load_json(TASK_BOARD_FILE, [])
    task = {
        "id": len(tasks) + 1,
        "description": description,
        "priority": priority,
        "status": "todo",
        "assigned_worker": assigned_worker,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "completed_at": None,
    }
    tasks.append(task)
    _save_json(TASK_BOARD_FILE, tasks)
    return f"任务 #{task['id']} 已创建: {description[:80]}（优先级: {priority}）"


def list_tasks(status_filter: str = "") -> str:
    """列出任务看板中的所有任务。"""
    tasks = _load_json(TASK_BOARD_FILE, [])
    if not tasks:
        return "任务看板为空。"

    if status_filter:
        tasks = [t for t in tasks if t["status"] == status_filter]

    counts = {"todo": 0, "in_progress": 0, "done": 0, "failed": 0}
    for t in tasks:
        counts[t["status"]] = counts.get(t["status"], 0) + 1

    lines = [f"📋 任务看板（待办:{counts.get('todo',0)} 进行中:{counts.get('in_progress',0)} 完成:{counts.get('done',0)} 失败:{counts.get('failed',0)}）"]
    for t in tasks[-20:]:  # 最近 20 条
        icon = {"todo": "⬜", "in_progress": "🔄", "done": "✅", "failed": "❌"}.get(t["status"], "❓")
        worker = f" @{t['assigned_worker']}" if t.get("assigned_worker") else ""
        lines.append(f"  {icon} #{t['id']} [{t['priority']}]{worker}: {t['description'][:80]}")
    return "\n".join(lines)


def update_task(task_id: int, status: str = "", assigned_worker: str = "") -> str:
    """更新任务状态或指派人。"""
    tasks = _load_json(TASK_BOARD_FILE, [])
    for t in tasks:
        if t["id"] == task_id:
            if status:
                t["status"] = status
                if status == "done":
                    t["completed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if assigned_worker:
                t["assigned_worker"] = assigned_worker
            _save_json(TASK_BOARD_FILE, tasks)
            return f"任务 #{task_id} 已更新。"
    return f"未找到任务 #{task_id}。"


# ── 共享知识库 ────────────────────────────────────────────

def record_knowledge(topic: str, content: str, author: str = "") -> str:
    """向团队知识库添加一条记录。"""
    entries = _load_json(KNOWLEDGE_FILE, [])
    entry = {
        "id": len(entries) + 1,
        "topic": topic,
        "content": content[:3000],
        "author": author,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    entries.append(entry)
    _save_json(KNOWLEDGE_FILE, entries)
    return f"知识条目 #{entry['id']} 已记录: {topic}"


def search_knowledge(query: str) -> str:
    """搜索团队知识库。"""
    entries = _load_json(KNOWLEDGE_FILE, [])
    if not entries:
        return "知识库为空。"
    query_lower = query.lower()
    matches = []
    for e in entries:
        if query_lower in e["topic"].lower() or query_lower in e["content"].lower():
            matches.append(e)
    if not matches:
        return f"未找到与「{query}」相关的知识条目。"
    lines = [f"📚 找到 {len(matches)} 条相关知识:"]
    for e in matches[-10:]:
        lines.append(f"  #{e['id']} [{e['topic']}] {e['content'][:150]}...")
    return "\n".join(lines)


def get_system_metadata() -> str:
    """返回系统版本、运行目录和持久化文件位置，避免 Manager 靠提示词猜。"""
    _init_providers()
    metadata = {
        "app": "Multi-Agent 层级管理系统",
        "version": APP_VERSION,
        "runtime": APP_RUNTIME_NAME,
        "cwd": os.getcwd(),
        "providers_configured": sorted(PROVIDERS.keys()),
        "storage": {
            "worker_sessions": os.path.abspath(SESSION_FILE),
            "worker_scores": os.path.abspath(SCORE_FILE),
            "task_board": os.path.abspath(TASK_BOARD_FILE),
            "team_knowledge": os.path.abspath(KNOWLEDGE_FILE),
            "project_states_dir": os.path.abspath(PROJECT_STATE_DIR),
        },
        "rules": [
            "版本号以 get_system_metadata 返回值为准，不从启动横幅或系统提示推断。",
            "如果项目包文件未声明 version，应明确说明未找到项目语义版本。",
            "对话上下文由客户端负责；本程序只持久化 worker/tool 运行状态文件。",
        ],
    }
    return json.dumps(metadata, ensure_ascii=False, indent=2)


# ── 项目状态管理 ──────────────────────────────────────────

def ensure_project_state_dir():
    os.makedirs(PROJECT_STATE_DIR, exist_ok=True)


def load_project_state(project_name: str) -> dict:
    """加载项目状态文件，不存在则返回空模板。"""
    ensure_project_state_dir()
    filepath = os.path.join(PROJECT_STATE_DIR, f"{project_name}_state.json")
    if not os.path.exists(filepath):
        return {
            "project": project_name,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "pipeline_steps": [],
            "assignments": {},
            "model_usage": [],
            "quality_reviews": [],
        }
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_project_state(project_name: str, state: dict):
    """持久化项目状态。"""
    ensure_project_state_dir()
    filepath = os.path.join(PROJECT_STATE_DIR, f"{project_name}_state.json")
    state["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def log_model_usage(project_name: str, worker_name: str, task: str,
                    complexity: str, model_used: str, reason: str = ""):
    """记录一次模型使用/升级。"""
    state = load_project_state(project_name)
    state["model_usage"].append({
        "worker": worker_name,
        "task": task[:120],
        "complexity": complexity,
        "model": model_used,
        "reason": reason,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    save_project_state(project_name, state)


def update_project_step(project_name: str, step_name: str, status: str,
                         output_path: str = "", worker: str = ""):
    """更新项目 pipeline 步骤状态。"""
    state = load_project_state(project_name)
    for step in state["pipeline_steps"]:
        if step["name"] == step_name:
            step["status"] = status
            if output_path:
                step["output"] = output_path
            if worker:
                step["worker"] = worker
            step["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            break
    else:
        state["pipeline_steps"].append({
            "name": step_name,
            "status": status,
            "output": output_path,
            "worker": worker,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
    save_project_state(project_name, state)


# ── WorkflowRun 持久化 ────────────────────────────────────

def save_workflow_run(run: WorkflowRun):
    """保存 WorkflowRun 到磁盘。"""
    _save_workflow_run(run)


def _save_workflow_run(run: WorkflowRun):
    """内部：WorkflowRun → JSON 文件。"""
    from dataclasses import asdict
    try:
        ensure_project_state_dir()
        filepath = os.path.join(PROJECT_STATE_DIR, f"{run.project_name}_workflow.json")
        run.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(asdict(run), f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_workflow_run(project_name: str) -> WorkflowRun | None:
    """从磁盘恢复 WorkflowRun。"""
    try:
        filepath = os.path.join(PROJECT_STATE_DIR, f"{project_name}_workflow.json")
        if not os.path.exists(filepath):
            return None
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return WorkflowRun(
            run_id=data.get("run_id", ""),
            project_name=data.get("project_name", project_name),
            status=data.get("status", "pending"),
            nodes=data.get("nodes", {}),
            budget=Budget(**data["budget"]) if isinstance(data.get("budget"), dict) else Budget(),
            execution_log=data.get("execution_log", []),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            version=data.get("version", 4),
        )
    except Exception:
        return None
