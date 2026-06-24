"""Agent Task API 层 —— 任务数据模型、持久化、后台执行。

纯数据 + 执行逻辑，不依赖 FastAPI。server.py 通过此模块接入 Agent Runtime。
"""
import json
import os
import re
import shutil
import sys
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, Callable

# ── 任务数据模型 ──

VALID_TASK_TYPES = {"manager_task", "worker_task", "verified_task", "project_pipeline_task"}
VALID_TASK_STATUSES = {"pending", "running", "completed", "failed", "cancelled"}


@dataclass
class AgentTask:
    """标准任务记录。JSON 可序列化，无需数据库。"""
    task_id: str
    type: str                        # manager_task | worker_task | verified_task | project_pipeline_task
    status: str = "pending"          # pending | running | completed | failed | cancelled
    description: str = ""
    worker_name: Optional[str] = None
    project_name: Optional[str] = None
    workspace_path: Optional[str] = None
    progress: Optional[str] = None
    logs: list = field(default_factory=list)
    result: Optional[str] = None
    artifacts: list = field(default_factory=list)
    error: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
    cancel_requested: bool = False

    def __post_init__(self):
        now = _now()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _generate_task_id() -> str:
    """生成唯一 task_id：task_<时间戳>_<短uuid>"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    short = uuid.uuid4().hex[:6]
    return f"task_{ts}_{short}"


def _extract_project_name_from_setup_result(text: str, fallback: str = "default_project") -> str:
    """从 project_setup 的人类可读报告中提取项目名。"""
    if not text:
        return fallback
    patterns = [
        r"项目分工表:\s*([^\r\n]+)",
        r"状态文件:\s*.*/([^/\r\n]+)_state\.json",
        r"状态文件:\s*.*\\([^\\\r\n]+)_state\.json",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            name = match.group(1).strip()
            if name:
                return name
    return fallback


# ── 持久化 ──

_TASKS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent_tasks.json")
_TASKS_BACKUP_FILE = f"{_TASKS_FILE}.bak"
_task_lock = threading.Lock()
_tasks_cache: dict[str, AgentTask] = {}


def _get_tasks_file() -> str:
    return _TASKS_FILE


class TaskStore:
    """JSON 文件持久化 store。线程安全。"""

    @staticmethod
    def _load_from_file(path: str) -> list[dict]:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _write_all_locked():
        data = [asdict(task) for task in _tasks_cache.values()]
        tmp_path = f"{_TASKS_FILE}.{os.getpid()}.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            if os.path.exists(_TASKS_FILE):
                try:
                    shutil.copyfile(_TASKS_FILE, _TASKS_BACKUP_FILE)
                except OSError:
                    pass
            os.replace(tmp_path, _TASKS_FILE)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    @staticmethod
    def load() -> dict[str, AgentTask]:
        global _tasks_cache
        with _task_lock:
            if not os.path.exists(_TASKS_FILE):
                return {}
            data = None
            last_error = None
            for candidate in (_TASKS_FILE, _TASKS_BACKUP_FILE):
                if not os.path.exists(candidate):
                    continue
                try:
                    data = TaskStore._load_from_file(candidate)
                    break
                except (json.JSONDecodeError, OSError) as e:
                    last_error = e
            if data is None:
                print(f"[TaskStore] 警告: 无法读取任务持久化文件: {last_error}")
                return {}
            _tasks_cache = {}
            for item in data:
                task = AgentTask(
                    task_id=item.get("task_id", ""),
                    type=item.get("type", ""),
                    status=item.get("status", "pending"),
                    description=item.get("description", ""),
                    worker_name=item.get("worker_name"),
                    project_name=item.get("project_name"),
                    workspace_path=item.get("workspace_path"),
                    progress=item.get("progress"),
                    logs=item.get("logs", []),
                    result=item.get("result"),
                    artifacts=item.get("artifacts", []),
                    error=item.get("error"),
                    created_at=item.get("created_at", ""),
                    updated_at=item.get("updated_at", ""),
                    cancel_requested=item.get("cancel_requested", False),
                )
                if task.task_id:
                    _tasks_cache[task.task_id] = task
            return dict(_tasks_cache)

    @staticmethod
    def save(task: AgentTask):
        global _tasks_cache
        task.updated_at = _now()
        with _task_lock:
            _tasks_cache[task.task_id] = task
            TaskStore._write_all_locked()

    @staticmethod
    def get(task_id: str) -> Optional[AgentTask]:
        global _tasks_cache
        with _task_lock:
            return _tasks_cache.get(task_id)

    @staticmethod
    def list_all() -> list[AgentTask]:
        global _tasks_cache
        with _task_lock:
            return list(_tasks_cache.values())

    @staticmethod
    def recover_incomplete(reason: str = "后端重启，未完成任务已中断") -> int:
        global _tasks_cache
        recovered = 0
        with _task_lock:
            for task in _tasks_cache.values():
                if task.status not in {"pending", "running"}:
                    continue
                task.status = "failed"
                task.progress = "执行中断"
                task.error = reason if not task.error else f"{task.error} | {reason}"
                task.logs.append(f"[{_now()}] 系统恢复: {reason}")
                task.updated_at = _now()
                recovered += 1
            if recovered:
                TaskStore._write_all_locked()
        return recovered


# 启动时加载缓存
TaskStore.load()
TaskStore.recover_incomplete()


# ── 输入验证 ──

class TaskValidationError(Exception):
    """任务参数校验失败。"""
    pass


def validate_create_task(
    task_type: str,
    description: str,
    worker_name: Optional[str],
    workers: dict,
    workspace_path: Optional[str] = None,
) -> None:
    """校验任务创建参数。不通过抛 TaskValidationError。"""
    if not task_type or task_type not in VALID_TASK_TYPES:
        raise TaskValidationError(
            f"无效的 task type: '{task_type}'。有效值: {', '.join(sorted(VALID_TASK_TYPES))}"
        )
    if not description or not description.strip():
        raise TaskValidationError("description 不能为空")
    if task_type in ("worker_task", "verified_task"):
        if not worker_name:
            raise TaskValidationError(f"{task_type} 必须指定 worker_name")
        if worker_name not in workers:
            available = ", ".join(workers.keys())
            raise TaskValidationError(
                f"未知 Worker: '{worker_name}'。可用: {available}"
            )
    if task_type in ("manager_task", "project_pipeline_task") and worker_name and worker_name not in workers:
        available = ", ".join(workers.keys())
        raise TaskValidationError(
            f"未知 Worker: '{worker_name}'。可用: {available}"
        )
    if workspace_path is not None:
        p = os.path.abspath(os.path.expanduser(workspace_path))
        if not os.path.isdir(p):
            raise TaskValidationError(
                f"workspace_path 不存在或不是目录: {workspace_path}"
            )


# ── 后台任务执行器 ──

class TaskExecutor:
    """后台线程池执行 Agent 任务。通知回调用于 WebSocket 推送。"""

    def __init__(self, workers: dict, max_workers: int = 4):
        self.workers = workers
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self._notify_callback: Optional[Callable] = None
        self._sync_mode = False  # 测试模式：submit 同步执行

    def set_notify_callback(self, cb: Callable):
        """设置任务状态变更通知回调。cb(task: AgentTask) -> None"""
        self._notify_callback = cb

    def _notify(self, task: AgentTask):
        if self._notify_callback:
            try:
                self._notify_callback(task)
            except Exception:
                pass  # 通知失败不影响任务执行

    def _append_log(self, task: AgentTask, message: str):
        task.logs.append(f"[{_now()}] {message}")
        task.updated_at = _now()

    def submit(self, task: AgentTask):
        """提交任务到后台执行。立即返回，不阻塞。"""
        if self._sync_mode:
            # 测试模式：在当前线程同步执行，避免后台线程泄漏
            self._run(task)
        else:
            self.executor.submit(self._run, task)

    def shutdown(self, wait: bool = True):
        """关闭线程池。wait=True 时等待所有已提交任务完成。"""
        self.executor.shutdown(wait=wait)

    def _run(self, task: AgentTask):
        """后台执行入口。"""
        # 检查是否已经被取消
        if task.cancel_requested or task.status == "cancelled":
            return

        task.status = "running"
        task.progress = "开始执行..."
        self._append_log(task, f"任务开始: type={task.type}, worker={task.worker_name}")
        TaskStore.save(task)
        self._notify(task)

        try:
            if task.type == "manager_task":
                self._run_manager_task(task)
            elif task.type == "worker_task":
                self._run_worker_task(task)
            elif task.type == "verified_task":
                self._run_verified_task(task)
            elif task.type == "project_pipeline_task":
                self._run_pipeline_task(task)
        except Exception as e:
            task.status = "failed"
            task.error = f"{type(e).__name__}: {e}"
            task.progress = "执行失败"
            self._append_log(task, f"异常: {task.error}")
            self._append_log(task, traceback.format_exc())
            TaskStore.save(task)
            self._notify(task)

    def _run_manager_task(self, task: AgentTask):
        """执行 manager_task 类型，让 Manager 决策是否派发给 Worker。"""
        from manager import run_manager_task

        task.progress = "Manager 调度中..."
        TaskStore.save(task)
        self._notify(task)

        try:
            result = run_manager_task(self.workers, task.description)
        except Exception as e:
            task.status = "failed"
            task.error = f"{type(e).__name__}: {e}"
            task.progress = "Manager 执行异常"
            self._append_log(task, f"执行异常: {e}")
            TaskStore.save(task)
            self._notify(task)
            return

        if task.cancel_requested:
            task.status = "cancelled"
            task.progress = "已取消"
            self._append_log(task, "任务在运行中被取消")
            TaskStore.save(task)
            self._notify(task)
            return

        task.result = result.get("result", "")
        task.artifacts = [{"type": "manager_tool", **item} for item in result.get("tool_calls", [])]
        task.status = "completed"
        task.progress = "完成"
        task.logs.extend(result.get("log", []))
        self._append_log(task, f"完成: {task.result[:200] if task.result else '(无输出)'}")
        TaskStore.save(task)
        self._notify(task)

    def _run_worker_task(self, task: AgentTask):
        """执行 worker_task 类型。"""
        # 延迟 import 避免循环依赖（server.py → manager → 本模块）
        from runtime.workers import run_worker

        worker_cfg = self.workers[task.worker_name]
        task.progress = f"Worker-{task.worker_name} 执行中..."
        TaskStore.save(task)
        self._notify(task)

        capture_buffer = []
        try:
            result = run_worker(
                worker_cfg,
                task.description,
                use_memory=True,
                fresh_session=True,
                session_scope=f"api:{task.task_id}",
            )
        except Exception as e:
            task.status = "failed"
            task.error = f"{type(e).__name__}: {e}"
            task.progress = "Worker 执行异常"
            self._append_log(task, f"执行异常: {e}")
            TaskStore.save(task)
            self._notify(task)
            return

        # 检查取消请求
        if task.cancel_requested:
            task.status = "cancelled"
            task.progress = "已取消"
            self._append_log(task, "任务在运行中被取消")
            TaskStore.save(task)
            self._notify(task)
            return

        task.result = result.get("result", "")
        task.artifacts = result.get("structured_result", {}).get("artifacts", [])
        task.status = "completed"
        task.progress = "完成"
        task.logs.extend(result.get("log", []))
        self._append_log(task, f"完成: {task.result[:200] if task.result else '(无输出)'}")
        TaskStore.save(task)
        self._notify(task)

    def _run_verified_task(self, task: AgentTask):
        """执行 verified_task 类型（verify-retry 闭环）。"""
        from runtime.verification import delegate_with_verification

        task.progress = f"Worker-{task.worker_name} + 验证中..."
        TaskStore.save(task)
        self._notify(task)

        try:
            result = delegate_with_verification(
                self.workers,
                task.worker_name,
                task.description,
            )
        except Exception as e:
            task.status = "failed"
            task.error = f"{type(e).__name__}: {e}"
            task.progress = "验证闭环执行异常"
            self._append_log(task, f"执行异常: {e}")
            TaskStore.save(task)
            self._notify(task)
            return

        if task.cancel_requested:
            task.status = "cancelled"
            task.progress = "已取消"
            self._append_log(task, "任务在运行中被取消")
            TaskStore.save(task)
            self._notify(task)
            return

        task.result = json.dumps(result, ensure_ascii=False, default=str) if isinstance(result, dict) else str(result)
        task.status = "completed"
        task.progress = "完成（含验证）"
        self._append_log(task, f"验证完毕: {task.result[:200]}")
        TaskStore.save(task)
        self._notify(task)

    def _run_pipeline_task(self, task: AgentTask):
        """执行 project_pipeline_task 类型。"""
        from runtime.pipeline import project_setup, run_project_pipeline

        task.progress = "正在 project_setup..."
        TaskStore.save(task)
        self._notify(task)

        try:
            # 1. project_setup
            ps_result = project_setup(self.workers, task.description)
            project_name = _extract_project_name_from_setup_result(
                ps_result,
                task.project_name or "default_project",
            )
            task.project_name = project_name
            self._append_log(task, f"project_setup 完成: {project_name}")

            if task.cancel_requested:
                task.status = "cancelled"
                task.progress = "已取消"
                self._append_log(task, "任务在 project_setup 后被取消")
                TaskStore.save(task)
                self._notify(task)
                return

            # 2. run_project_pipeline
            task.progress = f"Pipeline 执行中: {project_name}"
            TaskStore.save(task)
            self._notify(task)

            pp_result = run_project_pipeline(project_name, self.workers)

        except Exception as e:
            task.status = "failed"
            task.error = f"{type(e).__name__}: {e}"
            task.progress = "Pipeline 执行异常"
            self._append_log(task, f"执行异常: {e}")
            TaskStore.save(task)
            self._notify(task)
            return

        if task.cancel_requested:
            task.status = "cancelled"
            task.progress = "已取消"
            self._append_log(task, "任务在 pipeline 执行后被取消")
            TaskStore.save(task)
            self._notify(task)
            return

        task.result = json.dumps(pp_result, ensure_ascii=False, default=str) if isinstance(pp_result, dict) else str(pp_result)
        task.status = "completed"
        task.progress = "Pipeline 完成"
        self._append_log(task, f"Pipeline 完成: {project_name}")
        TaskStore.save(task)
        self._notify(task)


# 全局 executor（server.py 初始化时设置）
_executor: Optional[TaskExecutor] = None


def get_executor() -> Optional[TaskExecutor]:
    return _executor


def init_executor(workers: dict, max_workers: int = 4) -> TaskExecutor:
    global _executor
    _executor = TaskExecutor(workers, max_workers=max_workers)
    return _executor
