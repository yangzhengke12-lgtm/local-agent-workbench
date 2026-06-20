"""
团队聊天室后端 — FastAPI + WebSocket + Agent Task API
启动: python server.py
"""
import asyncio
import io, json, os, sys, threading
from contextlib import asynccontextmanager, redirect_stdout
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
import uvicorn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from manager import load_workers, run_worker, default_client, DEFAULT_MODEL

from runtime.agent_task import (
    AgentTask,
    TaskStore,
    TaskExecutor,
    TaskValidationError,
    VALID_TASK_TYPES,
    validate_create_task,
    init_executor,
    get_executor,
    _generate_task_id,
)
from runtime.persistence import (
    KNOWLEDGE_FILE,
    PROJECT_STATE_DIR,
    SESSION_FILE,
    TASK_BOARD_FILE,
    SCORE_FILE,
    _load_json,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动/关闭生命周期管理。"""
    yield
    # shutdown: 优雅关闭 TaskExecutor
    if task_executor:
        task_executor.shutdown(wait=True)


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
workers = load_workers()
HISTORY = os.path.join(os.path.dirname(__file__), "chat_history.json")

# ── 初始化 Agent Task 后台执行器 ──
task_executor = init_executor(workers, max_workers=4)

# ── 桌面工作台 workspace 状态 ──
_current_workspace = {"path": None, "set_at": None}
_WORKSPACE_EXCLUDED_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".pytest_cache", ".codegraph"}
_TEXT_FILE_EXTENSIONS = {
    ".bat", ".c", ".cfg", ".conf", ".cpp", ".cs", ".css", ".csv", ".env", ".go",
    ".h", ".html", ".ini", ".java", ".js", ".json", ".jsx", ".log", ".md", ".mjs",
    ".py", ".rs", ".sh", ".sql", ".toml", ".ts", ".tsx", ".txt", ".xml", ".yaml",
    ".yml",
}

def _now_ws():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _repo_path(*parts):
    return os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), *parts))


def _workspace_root() -> str:
    root = _current_workspace.get("path")
    if not root:
        raise HTTPException(status_code=400, detail="工作区未设置")
    root = os.path.abspath(os.path.expanduser(root))
    if not os.path.isdir(root):
        raise HTTPException(status_code=400, detail=f"工作区不存在: {root}")
    return root


def _resolve_workspace_path(path: str | None = None) -> str:
    root = _workspace_root()
    target = root if not path else os.path.abspath(os.path.join(root, path) if not os.path.isabs(path) else path)
    try:
        common = os.path.commonpath([root, target])
    except ValueError:
        raise HTTPException(status_code=403, detail="路径不在工作区内")
    if common != root:
        raise HTTPException(status_code=403, detail="路径不在工作区内")
    return target


def _rel_to_workspace(path: str) -> str:
    root = _workspace_root()
    rel = os.path.relpath(path, root)
    return "" if rel == "." else rel.replace("\\", "/")


def _safe_load_json_file(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _is_probably_text(path: str, sample: bytes) -> bool:
    suffix = Path(path).suffix.lower()
    if suffix in _TEXT_FILE_EXTENSIONS:
        return True
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _parse_task_events(task: AgentTask) -> list[dict]:
    events = []
    for idx, raw in enumerate(task.logs or []):
        text = str(raw)
        timestamp = ""
        body = text
        if text.startswith("[") and "] " in text:
            timestamp, body = text[1:].split("] ", 1)

        event_type = "log"
        tool_name = None
        args = None
        if "[工具:" in body or body.startswith("工具:"):
            event_type = "tool_call"
            marker = body.split("工具:", 1)[1].strip().rstrip("]")
            if "(" in marker and marker.endswith(")"):
                tool_name = marker.split("(", 1)[0].strip()
                raw_args = marker.split("(", 1)[1][:-1]
                try:
                    args = json.loads(raw_args)
                except Exception:
                    args = raw_args
            else:
                tool_name = marker
        elif "[工具返回:" in body or body.startswith("工具返回:") or "返回:" in body:
            event_type = "tool_result"
        elif "异常" in body or "Error" in body or "failed" in body:
            event_type = "error"
        elif "警告" in body or "Warning" in body:
            event_type = "warn"

        events.append({
            "index": idx,
            "type": event_type,
            "timestamp": timestamp,
            "tool_name": tool_name,
            "args": args,
            "text": body,
        })
    return events

MEMBERS = [
    {"name":"Boss","role":"老板","color":"#F5A623"},
    {"name":"Manager","role":"技术总监","color":"#4A90D9"},
    {"name":"维克托","role":"副经理","color":"#7B68EE"},
]
WC = {"亚历克斯":"#27AE60","索菲亚":"#E74C3C","马库斯":"#E67E22","埃琳娜":"#E91E90","纳撒尼尔":"#00BCD4"}
for n, c in workers.items():
    MEMBERS.append({"name":n,"role":c["role"],"color":WC.get(n,"#999")})

conns = []
_main_loop = None  # set in ws_handler

def ld():
    if not os.path.exists(HISTORY): return []
    try:
        with open(HISTORY,"r",encoding="utf-8") as f: return json.load(f)
    except: return []

def sv():
    try:
        with open(HISTORY,"w",encoding="utf-8") as f: json.dump(msgs[-200:],f,ensure_ascii=False,indent=2)
    except: pass

msgs = ld()[-500:]

def am(msg): msgs.append(msg); sv()

async def bc(msg):
    dead = []
    for w in conns:
        try: await w.send_json(msg)
        except: dead.append(w)
    for w in dead: conns.remove(w)

# ── Task 状态变更 → WebSocket 推送 ──
def _on_task_update(task: AgentTask):
    """任务状态变更时通过 WebSocket 广播。在线程中调用。"""
    global _main_loop
    if _main_loop is None:
        return
    payload = {
        "type": "agent_task_update",
        "task_id": task.task_id,
        "status": task.status,
        "progress": task.progress,
        "message": task.result[:200] if task.result else (task.error or ""),
    }
    try:
        asyncio.run_coroutine_threadsafe(bc(payload), _main_loop)
    except Exception:
        pass

# 注入通知回调
task_executor.set_notify_callback(_on_task_update)

async def run_worker_task(wname, task):
    if wname not in workers: return
    c = workers[wname]
    await bc({"type":"typing","worker":wname,"status":True})

    q = asyncio.Queue()
    rh = {"r":""}
    def _run():
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                r = run_worker(c, task, use_memory=True)
            rh["r"] = r.get("result","")
            o = buf.getvalue()
        except Exception as e:
            o = f"Error: {e}"; rh["r"] = o
        for line in o.strip().split("\n"):
            if line.strip():
                asyncio.run_coroutine_threadsafe(q.put(line.strip()), loop)
        asyncio.run_coroutine_threadsafe(q.put(None), loop)

    loop = asyncio.get_event_loop()
    threading.Thread(target=_run,daemon=True).start()
    while True:
        line = await q.get()
        if line is None: break
        cl = line.lstrip()
        if "工具:" in cl: await bc({"type":"tool_call","worker":wname,"role":c["role"],"text":cl})
        elif "返回:" in cl: await bc({"type":"tool_result","worker":wname,"role":c["role"],"text":cl})
        else: await bc({"type":"worker_msg","worker":wname,"role":c["role"],"text":cl})

    await bc({"type":"typing","worker":wname,"status":False})
    await bc({"type":"task_done","worker":wname,"role":c["role"],"text":rh["r"][:500]})
    am({"type":"task_done","worker":wname,"role":c["role"],"result":rh["r"][:500]})

async def chat_reply(txt):
    await bc({"type":"typing","worker":"Manager","status":True})
    rt = ""
    def _chat():
        nonlocal rt
        try:
            r = default_client.messages.create(model=DEFAULT_MODEL,max_tokens=256,
                system="你是Manager,在群聊中回复。中文,友好,1-2句话。",
                messages=[{"role":"user","content":txt}])
            for b in r.content:
                if b.type == "text": rt = b.text
        except: rt = "..."

    await asyncio.get_event_loop().run_in_executor(None,_chat)
    await bc({"type":"typing","worker":"Manager","status":False})
    if rt:
        await bc({"type":"worker_msg","worker":"Manager","role":"技术总监","text":rt})
        am({"type":"worker_msg","worker":"Manager","role":"技术总监","text":rt})

UPDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),"uploads")

# ═══════════════════════════════════════════════════════════════
# 原有接口
# ═══════════════════════════════════════════════════════════════

@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    os.makedirs(UPDIR,exist_ok=True)
    fp = os.path.join(UPDIR,file.filename)
    ct = await file.read()
    with open(fp,"wb") as f: f.write(ct)
    await bc({"type":"system","text":f"文件: {file.filename} ({len(ct)} bytes)"})
    return JSONResponse({"ok":True,"file":file.filename,"size":len(ct)})

@app.websocket("/ws")
async def ws_handler(ws: WebSocket):
    global _main_loop
    await ws.accept()
    conns.append(ws)
    _main_loop = asyncio.get_event_loop()
    await ws.send_json({"type":"member_list","members":MEMBERS})
    for m in msgs[-50:]: await ws.send_json(m)
    await bc({"type":"system","text":"Boss 上线"})
    try:
        while True:
            d = await ws.receive_json()
            t = d.get("type","")
            if t == "task":
                w = d.get("worker",""); c = d.get("content","")
                if w in workers:
                    am({"type":"task","worker":w,"content":c})
                    await bc({"type":"system","text":f"派给 {w}: {c[:80]}"})
                    asyncio.create_task(run_worker_task(w,c))
            elif t == "chat":
                txt = d.get("text","")
                am({"type":"chat","text":txt})
                asyncio.create_task(chat_reply(txt))
            elif t == "broadcast":
                txt = d.get("text","")
                am({"type":"broadcast","text":txt})
                await bc({"type":"broadcast","text":txt})
    except WebSocketDisconnect:
        conns.remove(ws)
        await bc({"type":"system","text":"Boss 离线"})

@app.get("/")
async def root():
    return {
        "name": "local-agent-workbench",
        "status": "ok",
        "docs": {
            "health": "/health",
            "workers": "/agent/workers",
            "tasks": "/agent/tasks",
            "workspace": "/agent/workspace",
            "websocket": "/ws",
        },
    }


# ═══════════════════════════════════════════════════════════════
# Desktop Workbench 接口 — Health / Workspace / Workers
# ═══════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    return {"status": "ok"}


class WorkspaceRequest(BaseModel):
    path: str


@app.post("/agent/workspace")
async def set_workspace(req: WorkspaceRequest):
    p = os.path.abspath(os.path.expanduser(req.path))
    if not os.path.isdir(p):
        raise HTTPException(status_code=400, detail=f"目录不存在: {p}")
    _current_workspace["path"] = p
    _current_workspace["set_at"] = _now_ws()
    return {"ok": True, "workspace": p, "set_at": _current_workspace["set_at"]}


@app.get("/agent/workspace")
async def get_workspace():
    return {
        "workspace": _current_workspace["path"],
        "set_at": _current_workspace["set_at"],
    }


@app.get("/agent/workers")
async def get_workers():
    return {
        "workers": [
            {
                "name": k,
                "role": v.get("role", ""),
                "description": v.get("description", ""),
                "tools": v.get("tool_names", []),
                "model": v.get("model", ""),
            }
            for k, v in workers.items()
        ]
    }


@app.get("/agent/workspace/files")
async def list_workspace_files(path: str = ""):
    """列出工作区内某目录的一层文件。只读、不可越界。"""
    root = _workspace_root()
    target = _resolve_workspace_path(path)
    if not os.path.isdir(target):
        raise HTTPException(status_code=400, detail=f"不是目录: {target}")

    entries = []
    truncated = False
    try:
        names = sorted(os.listdir(target), key=lambda n: (not os.path.isdir(os.path.join(target, n)), n.lower()))
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"无法读取目录: {e}")

    for name in names:
        full = os.path.join(target, name)
        is_dir = os.path.isdir(full)
        if is_dir and name in _WORKSPACE_EXCLUDED_DIRS:
            continue
        try:
            stat = os.stat(full)
        except OSError:
            continue
        entries.append({
            "name": name,
            "path": full,
            "relative_path": _rel_to_workspace(full),
            "type": "directory" if is_dir else "file",
            "size": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        })
        if len(entries) >= 200:
            truncated = True
            break

    return JSONResponse({
        "workspace": root,
        "path": target,
        "relative_path": _rel_to_workspace(target),
        "parent": _rel_to_workspace(os.path.dirname(target)) if os.path.abspath(target) != root else None,
        "entries": entries,
        "truncated": truncated,
    })


@app.get("/agent/workspace/file")
async def preview_workspace_file(path: str, max_chars: int = 20000):
    """预览工作区内文本文件。二进制或超大文件返回明确原因。"""
    target = _resolve_workspace_path(path)
    if not os.path.isfile(target):
        raise HTTPException(status_code=400, detail=f"不是文件: {target}")
    size = os.path.getsize(target)
    with open(target, "rb") as f:
        sample = f.read(4096)
    if not _is_probably_text(target, sample):
        return JSONResponse({
            "path": target,
            "relative_path": _rel_to_workspace(target),
            "previewable": False,
            "content": "",
            "size": size,
            "truncated": False,
            "reason": "二进制文件不可预览",
        })

    max_chars = max(1000, min(max_chars, 80000))
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(max_chars + 1)
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"无法读取文件: {e}")
    truncated = len(content) > max_chars
    if truncated:
        content = content[:max_chars]
    return JSONResponse({
        "path": target,
        "relative_path": _rel_to_workspace(target),
        "previewable": True,
        "content": content,
        "size": size,
        "truncated": truncated,
        "reason": "",
    })


@app.get("/agent/tasks/{task_id}/events")
async def get_agent_task_events(task_id: str):
    """把原始日志整理为工具调用/工具返回/错误等事件。"""
    task = TaskStore.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    events = _parse_task_events(task)
    return JSONResponse({
        "task_id": task.task_id,
        "status": task.status,
        "event_count": len(events),
        "events": events,
    })


@app.get("/agent/memory")
async def get_agent_memory():
    """返回本地记忆/知识库/任务板状态，不触发任何 LLM。"""
    sessions = _safe_load_json_file(_repo_path(SESSION_FILE), {})
    knowledge = _load_json(_repo_path(KNOWLEDGE_FILE), [])
    task_board = _load_json(_repo_path(TASK_BOARD_FILE), [])
    scores = _load_json(_repo_path(SCORE_FILE), [])
    project_dir = _repo_path(PROJECT_STATE_DIR)
    project_files = []
    if os.path.isdir(project_dir):
        project_files = sorted([
            name for name in os.listdir(project_dir)
            if name.endswith(".json")
        ])[:50]

    recent_knowledge = list(reversed(knowledge[-8:])) if isinstance(knowledge, list) else []
    return JSONResponse({
        "sessions": {
            "count": len(sessions) if isinstance(sessions, dict) else 0,
            "keys": sorted(list(sessions.keys()))[:20] if isinstance(sessions, dict) else [],
        },
        "knowledge": {
            "count": len(knowledge) if isinstance(knowledge, list) else 0,
            "recent": recent_knowledge,
            "file": _repo_path(KNOWLEDGE_FILE),
        },
        "task_board": {
            "count": len(task_board) if isinstance(task_board, list) else 0,
            "file": _repo_path(TASK_BOARD_FILE),
        },
        "scores": {
            "count": len(scores) if isinstance(scores, list) else 0,
            "file": _repo_path(SCORE_FILE),
        },
        "project_states": {
            "count": len(project_files),
            "files": project_files,
            "directory": project_dir,
        },
    })


@app.get("/agent/rules")
async def get_agent_rules():
    """返回桌面端可展示的任务规则和工具权限。"""
    dangerous_tools = ["write_file", "run_command", "github_create_pr", "save_template"]
    return JSONResponse({
        "task_types": [
            {
                "type": "worker_task",
                "label": "Worker 任务",
                "requires_worker": True,
                "description": "交给一个指定 Worker 直接执行，适合明确的小任务。",
            },
            {
                "type": "verified_task",
                "label": "验证任务",
                "requires_worker": True,
                "description": "Worker 执行后进入验证闭环，适合代码修改、测试和需要质量把关的任务。",
            },
            {
                "type": "project_pipeline_task",
                "label": "Pipeline",
                "requires_worker": False,
                "description": "由 project_setup 拆分 DAG 并运行流水线，适合多步骤项目任务。",
            },
        ],
        "workspace_policy": {
            "current_workspace": _current_workspace.get("path"),
            "set_at": _current_workspace.get("set_at"),
            "file_preview": "只允许读取当前 workspace 内文件；排除依赖、缓存和 .git 目录。",
            "command_execution": "桌面接口不执行任意命令；命令只能由具备 run_command 工具的 Worker 在任务中调用。",
        },
        "dangerous_tools": dangerous_tools,
        "workers": [
            {
                "name": k,
                "role": v.get("role", ""),
                "description": v.get("description", ""),
                "tools": v.get("tool_names", []),
                "dangerous_tools": [tool for tool in v.get("tool_names", []) if tool in dangerous_tools],
                "model": v.get("model", ""),
            }
            for k, v in workers.items()
        ],
    })


# ═══════════════════════════════════════════════════════════════
# Agent Task API — 标准异步任务接口
# ═══════════════════════════════════════════════════════════════

# ── Pydantic 模型 ──

class CreateTaskRequest(BaseModel):
    type: str
    description: str
    worker_name: str | None = None
    project_name: str | None = None
    workspace_path: str | None = None

    @field_validator("type")
    @classmethod
    def check_type(cls, v: str) -> str:
        if v not in VALID_TASK_TYPES:
            raise ValueError(f"无效的 task type: '{v}'。有效值: {', '.join(sorted(VALID_TASK_TYPES))}")
        return v

    @field_validator("description")
    @classmethod
    def check_description(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("description 不能为空")
        return v.strip()


class TaskSummaryResponse(BaseModel):
    task_id: str
    type: str
    status: str
    description: str
    worker_name: str | None
    project_name: str | None
    workspace_path: str | None = None
    progress: str | None
    created_at: str
    updated_at: str
    result_preview: str | None
    error: str | None
    log_count: int


class TaskDetailResponse(BaseModel):
    task_id: str
    type: str
    status: str
    description: str
    worker_name: str | None
    project_name: str | None
    workspace_path: str | None = None
    progress: str | None
    logs: list[str]
    result: str | None
    artifacts: list
    error: str | None
    created_at: str
    updated_at: str
    cancel_requested: bool


# ── 路由 ──

@app.post("/agent/tasks")
async def create_agent_task(req: CreateTaskRequest):
    """创建异步 Agent 任务。立即返回 task_id，后台执行。"""
    # 额外校验 worker_name（Pydantic 已校验 type 和 description)
    try:
        validate_create_task(req.type, req.description, req.worker_name, workers, req.workspace_path)
    except TaskValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    task = AgentTask(
        task_id=_generate_task_id(),
        type=req.type,
        description=req.description,
        worker_name=req.worker_name,
        project_name=req.project_name,
        workspace_path=req.workspace_path or _current_workspace.get("path"),
    )
    TaskStore.save(task)

    # 提交到后台线程池执行
    task_executor.submit(task)

    return JSONResponse({
        "ok": True,
        "task_id": task.task_id,
        "status": task.status,
        "message": f"任务已创建，类型={task.type}",
    })


@app.get("/agent/tasks/{task_id}")
async def get_agent_task(task_id: str):
    """查询任务状态、进度、结果摘要。"""
    task = TaskStore.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    return TaskSummaryResponse(
        task_id=task.task_id,
        type=task.type,
        status=task.status,
        description=task.description,
        worker_name=task.worker_name,
        project_name=task.project_name,
        workspace_path=task.workspace_path,
        progress=task.progress,
        created_at=task.created_at,
        updated_at=task.updated_at,
        result_preview=task.result[:500] if task.result else None,
        error=task.error,
        log_count=len(task.logs),
    )


@app.get("/agent/tasks/{task_id}/detail")
async def get_agent_task_detail(task_id: str):
    """查询任务完整详情，包含全部 logs 和 result。"""
    task = TaskStore.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    return TaskDetailResponse(
        task_id=task.task_id,
        type=task.type,
        status=task.status,
        description=task.description,
        worker_name=task.worker_name,
        project_name=task.project_name,
        workspace_path=task.workspace_path,
        progress=task.progress,
        logs=task.logs,
        result=task.result,
        artifacts=task.artifacts,
        error=task.error,
        created_at=task.created_at,
        updated_at=task.updated_at,
        cancel_requested=task.cancel_requested,
    )


@app.get("/agent/tasks/{task_id}/logs")
async def get_agent_task_logs(task_id: str):
    """查询任务执行日志。"""
    task = TaskStore.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    return JSONResponse({
        "task_id": task.task_id,
        "status": task.status,
        "log_count": len(task.logs),
        "logs": task.logs,
    })


@app.get("/agent/tasks/{task_id}/result")
async def get_agent_task_result(task_id: str):
    """查询任务最终结果。"""
    task = TaskStore.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    if task.status == "pending":
        raise HTTPException(status_code=400, detail="任务尚未开始执行")
    if task.status == "running":
        return JSONResponse({
            "task_id": task.task_id,
            "status": "running",
            "progress": task.progress,
            "message": "任务仍在执行中，请稍后重试",
        })

    return JSONResponse({
        "task_id": task.task_id,
        "status": task.status,
        "result": task.result,
        "error": task.error,
        "artifacts": task.artifacts,
    })


@app.post("/agent/tasks/{task_id}/cancel")
async def cancel_agent_task(task_id: str):
    """取消任务。pending 直接取消；running 标记取消（执行完成后不再继续下游）。"""
    task = TaskStore.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    if task.status in ("completed", "failed", "cancelled"):
        return JSONResponse({
            "ok": True,
            "task_id": task_id,
            "status": task.status,
            "message": f"任务已处于终态 {task.status}，无需取消",
        })

    if task.status == "pending":
        task.status = "cancelled"
        task.progress = "已取消（创建前）"
        task.error = "任务在开始前被取消"
        TaskStore.save(task)
        return JSONResponse({
            "ok": True,
            "task_id": task_id,
            "status": "cancelled",
            "message": "任务已取消（尚未开始执行）",
        })

    # running → 软取消标记
    task.cancel_requested = True
    TaskStore.save(task)
    return JSONResponse({
        "ok": True,
        "task_id": task_id,
        "status": task.status,
        "message": "已发送取消请求，任务将在当前操作完成后停止",
    })


@app.get("/agent/tasks")
async def list_agent_tasks(limit: int = 20, offset: int = 0):
    """列出最近的 Agent 任务。"""
    all_tasks = TaskStore.list_all()
    # 按创建时间倒序
    all_tasks.sort(key=lambda t: t.created_at, reverse=True)
    page = all_tasks[offset:offset + limit]

    items = []
    for task in page:
        items.append({
            "task_id": task.task_id,
            "type": task.type,
            "status": task.status,
            "description": task.description[:120],
            "worker_name": task.worker_name,
            "workspace_path": task.workspace_path,
            "progress": task.progress,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
        })

    return JSONResponse({
        "total": len(all_tasks),
        "offset": offset,
        "limit": limit,
        "items": items,
    })


if __name__ == "__main__":
    print(f"http://localhost:8000 | {len(MEMBERS)} members | Agent Task API ready")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
