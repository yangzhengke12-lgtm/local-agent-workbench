"""v4 Data Contracts — Agentic Workflow Runtime。

包含 TaskNode 状态机、Worker/Verifier 结构化合约、Budget、WorkflowRun。
这些 dataclass 是 runtime 包的数据基石，放在最底层，不依赖任何其他模块。
"""
from dataclasses import dataclass, field


class TaskNodeStatus:
    """TaskNode 状态机常量。"""
    TODO = "todo"
    READY = "ready"
    RUNNING = "running"
    VERIFYING = "verifying"
    DONE = "done"
    RETRYING = "retrying"
    FAILED = "failed"
    BLOCKED = "blocked"
    NEEDS_REPLAN = "needs_replan"


# 合法状态转移表（source → set of valid targets）
VALID_TRANSITIONS: dict[str, set[str]] = {
    TaskNodeStatus.TODO:          {TaskNodeStatus.READY, TaskNodeStatus.RUNNING},
    TaskNodeStatus.READY:         {TaskNodeStatus.RUNNING, TaskNodeStatus.BLOCKED},
    TaskNodeStatus.RUNNING:       {TaskNodeStatus.VERIFYING, TaskNodeStatus.RETRYING, TaskNodeStatus.FAILED},
    TaskNodeStatus.VERIFYING:     {TaskNodeStatus.DONE, TaskNodeStatus.RETRYING, TaskNodeStatus.FAILED, TaskNodeStatus.NEEDS_REPLAN},
    TaskNodeStatus.RETRYING:      {TaskNodeStatus.RUNNING, TaskNodeStatus.FAILED},
    TaskNodeStatus.DONE:          set(),
    TaskNodeStatus.FAILED:        {TaskNodeStatus.RUNNING},       # manual retry only
    TaskNodeStatus.BLOCKED:       {TaskNodeStatus.RUNNING},       # after upstream resolved
    TaskNodeStatus.NEEDS_REPLAN:  {TaskNodeStatus.RUNNING},       # after replan
}


@dataclass
class WorkerResult:
    """Worker 执行产出的结构化合约。parse 失败时 status="needs_review"。"""
    status: str          # "success" | "partial" | "failed" | "needs_review"
    summary: str
    artifacts: list = field(default_factory=list)   # [{"path": str, "type": str, "summary": str}]
    issues: list = field(default_factory=list)      # [{"severity": str, "description": str, "suggestion": str}]
    needs_replan: bool = False
    retryable: bool = True
    confidence: float = 0.8
    raw_text: str = ""


@dataclass
class VerificationResult:
    """Verifier 产出的结构化合约。"""
    verdict: str = "needs_retry"  # "pass" | "reject" | "needs_retry" | "needs_replan"
    score: float = 0.0            # 1-5
    blocking_issues: list = field(default_factory=list)
    retry_instruction: str = ""
    raw_text: str = ""


@dataclass
class TaskNode:
    """Pipeline 中单个任务节点的完整状态。"""
    id: str
    name: str
    description: str = ""
    depends_on: list = field(default_factory=list)
    assigned_worker: str = ""
    status: str = TaskNodeStatus.TODO
    attempts: int = 0
    max_retries: int = 3
    artifacts: list = field(default_factory=list)
    verification: dict | None = None
    budget: dict | None = None
    error: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Budget:
    """节点/工作流预算约束。"""
    max_attempts: int = 3
    max_rounds: int = 5
    max_tool_calls: int = 50
    max_runtime_seconds: int = 600
    max_model_calls: int = 20


@dataclass
class WorkflowRun:
    """一次项目工作流的完整运行时状态。可持久化，可恢复。"""
    run_id: str
    project_name: str
    status: str = "pending"  # "pending" | "running" | "paused" | "completed" | "failed"
    nodes: dict = field(default_factory=dict)   # node_id → TaskNode (as dict)
    budget: Budget = field(default_factory=Budget)
    execution_log: list = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    version: int = 4
