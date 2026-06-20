"""
团队聊天室后端 — FastAPI + WebSocket + Agent Task API
启动: python server.py
"""
import asyncio
import io, json, os, sys, threading
from contextlib import redirect_stdout
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
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

app = FastAPI()
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

def _now_ws():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)),"chat.html")
    return FileResponse(p)


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
            {"name": k, "role": v.get("role", ""), "description": v.get("description", "")}
            for k, v in workers.items()
        ]
    }


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
    uvicorn.run(app,host="0.0.0.0",port=8000,log_level="info")
