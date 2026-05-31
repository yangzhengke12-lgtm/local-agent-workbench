"""
Multi-Agent 层级管理系统 v3
老板（用户）→ 管理者（Manager Agent）→ 员工（Worker Agent）
- 并行 Worker 执行
- Worker 多轮对话记忆
- 联网搜索能力
- 多厂商模型自适应（DeepSeek / 千问 / MiniMax / GPT）

员工角色和权限由 workers.json 配置
"""
import json
import os
import shlex
import subprocess
import sys
import io
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict

# Windows UTF-8 控制台修复（Pitfall #4: GBK 编码无法输出 emoji）
# 仅在真实终端下启用，跳过 pytest/重定向等场景
if sys.platform == "win32" and "pytest" not in sys.modules:
    try:
        if hasattr(sys.stdout, "buffer") and sys.stdout.encoding != "utf-8":
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        if hasattr(sys.stderr, "buffer") and sys.stderr.encoding != "utf-8":
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
    except (AttributeError, OSError, ValueError):
        pass
from datetime import datetime
from enum import Enum

from anthropic import Anthropic
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# ── 多厂商 API 配置 ─────────────────────────────────────────
# 每个厂商有 type（anthropic/openai）、client、可用模型列表

DEEPSEEK_API_KEY = os.environ["ANTHROPIC_API_KEY"]
DEEPSEEK_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL")

# 千问 DashScope（OpenAI 兼容接口）
DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "sk-b0f92582f331485f86593020e920a401")
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# MiniMax（OpenAI 兼容接口）
MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "sk-api-em1kXboGEmAnuLyHcsPcm_vW6KNxZMzREfzzqstKaJFlPQE-TgbpA2Qfco0nAPdysMPjlceRTYrtRULWGll4tTC9OiMc4ZTAuzstt7WkdwUCQWcYwPQrga8")
MINIMAX_BASE_URL = "https://api.minimax.chat/v1"

# GPT 中转（OpenAI 兼容接口）
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "")

# ── 厂商注册表 ──────────────────────────────────────────────
# { provider_key: {"type": "anthropic"|"openai", "client": ..., "base_url": ...} }
PROVIDERS: dict[str, dict] = {}

# DeepSeek（Anthropic 兼容）
PROVIDERS["deepseek"] = {
    "type": "anthropic",
    "client": Anthropic(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL),
    "base_url": DEEPSEEK_BASE_URL or "",
}

# 千问 DashScope（OpenAI 兼容）
PROVIDERS["dashscope"] = {
    "type": "openai",
    "client": OpenAI(api_key=DASHSCOPE_API_KEY, base_url=DASHSCOPE_BASE_URL),
    "base_url": DASHSCOPE_BASE_URL,
}

# MiniMax（OpenAI 兼容）
PROVIDERS["minimax"] = {
    "type": "openai",
    "client": OpenAI(api_key=MINIMAX_API_KEY, base_url=MINIMAX_BASE_URL),
    "base_url": MINIMAX_BASE_URL,
}

# GPT 中转（OpenAI 兼容）
if OPENAI_API_KEY and OPENAI_BASE_URL:
    PROVIDERS["gpt"] = {
        "type": "openai",
        "client": OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL),
        "base_url": OPENAI_BASE_URL,
    }

# ── 模型路由表 ──────────────────────────────────────────────
# GPT 极贵，优先 DeepSeek。只有 DeepSeek 搞不定才升级。
MODEL_TIERS = {
    "simple":   ("deepseek", "deepseek-v4-flash"),       # 琐碎任务，极低成本
    "normal":   ("deepseek", "deepseek-v4-pro[1M]"),     # 主力干活，1M 上下文
    "complex":  ("deepseek", "deepseek-v4-pro[1M]"),     # 复杂也先用 DeepSeek，失败再升级
    "major":    ("gpt", "gpt-5.5"),                      # 重大决策才直接用 GPT-5.5
}

# 失败 2 次后的升级目标（DeepSeek → GPT）
UPGRADE_TARGET = ("gpt", "gpt-5.4")

# 备用模型（GPT 不可用时回退）
FALLBACK_COMPLEX = ("dashscope", "qwen-plus")
FALLBACK_MAJOR = ("minimax", "MiniMax-M2.7")

# Manager 模型路由
MANAGER_DEFAULT_MODEL = ("deepseek", "deepseek-v4-pro[1M]")
MANAGER_COMPLEX_MODEL = ("deepseek", "deepseek-v4-pro[1M]")  # 复杂调度也用 DeepSeek
MANAGER_MAJOR_MODEL = ("gpt", "gpt-5.5")                     # 只有重大决策用 GPT

# 默认 Anthropic client（向后兼容）
default_client = PROVIDERS["deepseek"]["client"]
DEFAULT_MODEL = "deepseek-v4-pro[1M]"
DEFAULT_API_KEY = DEEPSEEK_API_KEY
DEFAULT_BASE_URL = DEEPSEEK_BASE_URL

# ═══════════════════════════════════════════════════════════════
# v4 Data Contracts — Agentic Workflow Runtime
# ═══════════════════════════════════════════════════════════════


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


# ── v4 纯函数 ─────────────────────────────────────────────────


def _transition_node(node: TaskNode, new_status: str, reason: str = "") -> TaskNode:
    """状态转移核心。合法转移放行，非法抛 ValueError。"""
    valid = VALID_TRANSITIONS.get(node.status, set())
    if new_status not in valid:
        raise ValueError(
            f"Illegal transition: {node.id} {node.status} -> {new_status}. "
            f"Allowed: {valid}. Reason: {reason}"
        )
    node.status = new_status
    node.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if new_status == TaskNodeStatus.RETRYING:
        node.attempts += 1
    return node


def _extract_json_from_text(raw: str) -> str | None:
    """从自由文本中提取 JSON：优先 ```json 围栏，其次裸 {。"""
    import re
    # ```json ... ```
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw)
    if m:
        return m.group(1).strip()
    # 裸 { ... }
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        return m.group(0).strip()
    return None


def _normalize_worker_result(raw_text: str, artifacts: list | None = None) -> WorkerResult:
    """自由文本 → WorkerResult。永不抛异常。"""
    try:
        json_str = _extract_json_from_text(raw_text)
        if json_str:
            data = json.loads(json_str)
            return WorkerResult(
                status=data.get("status", "needs_review"),
                summary=data.get("summary", raw_text[:200]),
                artifacts=data.get("artifacts", artifacts or []),
                issues=data.get("issues", []),
                needs_replan=data.get("needs_replan", False),
                retryable=data.get("retryable", True),
                confidence=data.get("confidence", 0.8),
                raw_text=raw_text,
            )
    except (json.JSONDecodeError, TypeError, KeyError):
        pass
    # fallback: wrap everything into raw_text
    return WorkerResult(
        status="needs_review",
        summary=raw_text[:300],
        artifacts=artifacts or [],
        issues=[],
        raw_text=raw_text,
    )


def _normalize_verification_result(raw_text: str) -> VerificationResult:
    """自由文本 → VerificationResult。永不抛异常。"""
    try:
        json_str = _extract_json_from_text(raw_text)
        if json_str:
            data = json.loads(json_str)
            return VerificationResult(
                verdict=data.get("verdict", "needs_retry"),
                score=float(data.get("score", 0)),
                blocking_issues=data.get("blocking_issues", []),
                retry_instruction=data.get("retry_instruction", raw_text[:500]),
                raw_text=raw_text,
            )
    except (json.JSONDecodeError, TypeError, KeyError):
        pass
    return VerificationResult(
        verdict="needs_retry",
        retry_instruction=raw_text[:500],
        raw_text=raw_text,
    )


def _merge_verdicts(verdicts: list[VerificationResult]) -> VerificationResult:
    """合并多个验证结果。最坏优先：reject > needs_replan > needs_retry > pass。"""
    if not verdicts:
        return VerificationResult(verdict="needs_retry")

    priority = {"reject": 3, "needs_replan": 2, "needs_retry": 1, "pass": 0}
    worst = max(verdicts, key=lambda v: priority.get(v.verdict, 0))

    all_issues: list[dict] = []
    all_instructions: list[str] = []
    total_score = 0.0
    for v in verdicts:
        all_issues.extend(v.blocking_issues)
        if v.retry_instruction:
            all_instructions.append(v.retry_instruction)
        total_score += v.score

    return VerificationResult(
        verdict=worst.verdict,
        score=total_score / max(len(verdicts), 1),
        blocking_issues=all_issues,
        retry_instruction="; ".join(all_instructions),
    )


def _check_budget(stats: dict, budget: Budget) -> dict:
    """检查预算是否超限。返回标准化结构（v4.1）。

    Returns:
        {"allowed": bool, "reason": str, "budget_type": str, "current": int, "limit": int}
    """
    checks = [
        ("max_attempts", stats.get("attempts", 0), budget.max_attempts),
        ("max_model_calls", stats.get("model_calls", 0), budget.max_model_calls),
        ("max_tool_calls", stats.get("tool_calls", 0), budget.max_tool_calls),
        ("max_rounds", stats.get("rounds", 0), budget.max_rounds),
        ("max_runtime_seconds", stats.get("runtime_seconds", 0), budget.max_runtime_seconds),
    ]
    for budget_type, current, limit in checks:
        if current > limit:
            return {
                "allowed": False,
                "reason": f"{budget_type}: {current} > {limit}",
                "budget_type": budget_type,
                "current": current,
                "limit": limit,
            }
    return {
        "allowed": True,
        "reason": "",
        "budget_type": "",
        "current": 0,
        "limit": 0,
    }


def _discover_artifacts(log: list) -> list[dict]:
    """从 Worker 执行日志中提取文件产物。兼容字符串和字典两种 log 格式。"""
    artifacts: list[dict] = []
    for entry in log:
        if isinstance(entry, str):
            continue  # skip plain text log entries
        if not isinstance(entry, dict):
            continue
        tool_name = entry.get("tool", "")
        if tool_name in ("write_file", "save_template"):
            try:
                args = entry.get("args", {})
                if isinstance(args, str):
                    args = json.loads(args)
                path = args.get("path", args.get("file_path", ""))
                if path:
                    artifacts.append({
                        "path": path,
                        "type": tool_name,
                        "summary": f"{tool_name}: {path}",
                    })
            except (json.JSONDecodeError, TypeError):
                pass
    return artifacts


# ═══════════════════════════════════════════════════════════════
# v4.1 消息净化层 — 防止 thinking block 等多轮污染
# ═══════════════════════════════════════════════════════════════

# 会被 sanitize 移除的字段（非标准、厂商特有、或回传危险）
_UNSAFE_MESSAGE_FIELDS = {
    "thinking", "signature", "cache_control", "id",
    "stop_reason", "stop_sequence", "usage", "model",
    "tool_calls",    # OpenAI 格式的 tool_calls（不同于 Anthropic tool_use）
}


def sanitize_messages_for_provider(messages: list[dict], provider_key: str = "") -> list[dict]:
    """移除厂商不安全字段，返回安全的 messages 副本。

    规则：
    - 顶层保留：role, content
    - content 为 str：保留
    - content 为 list：保留 text block（所有 provider）+ thinking block（仅 DeepSeek）
    - 移除 tool_use, tool_result block（这些不应在历史中裸奔）
    - 消息清理后 content 为空：丢弃该消息
    - 不修改原始 messages，返回新 list
    """
    # DeepSeek 原生支持 thinking block，保留它避免 assistant 消息被清空
    keep_thinking = provider_key == "deepseek"
    cleaned: list[dict] = []

    for msg in messages:
        # 1. 只保留安全顶层字段
        safe_msg: dict = {}
        if "role" in msg:
            safe_msg["role"] = msg["role"]
        else:
            continue  # 没有 role 的消息直接丢弃

        raw_content = msg.get("content")

        # 2. 处理 content
        if isinstance(raw_content, str):
            safe_msg["content"] = raw_content
        elif isinstance(raw_content, list):
            kept_blocks: list[dict] = []
            for block in raw_content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type", "")
                if block_type == "text":
                    kept_blocks.append({
                        "type": "text",
                        "text": block.get("text", ""),
                    })
                elif block_type == "thinking" and keep_thinking:
                    # v4.2: 保留 DeepSeek thinking block，避免 assistant 消息被清空
                    kept_blocks.append({
                        "type": "thinking",
                        "thinking": block.get("thinking", ""),
                        "signature": block.get("signature", ""),
                    })
                # tool_use / tool_result block 始终丢弃
            if kept_blocks:
                safe_msg["content"] = kept_blocks
            else:
                continue  # content list 清空后丢弃整条消息
        else:
            continue  # 无 content 的消息丢弃

        # 3. 移除所有不安全顶层字段
        for field in _UNSAFE_MESSAGE_FIELDS:
            safe_msg.pop(field, None)
        # 移除其他未知字段
        extra_keys = set(safe_msg.keys()) - {"role", "content"}
        for key in extra_keys:
            safe_msg.pop(key, None)

        cleaned.append(safe_msg)

    return cleaned


# ═══════════════════════════════════════════════════════════════
# v4.1 Session 生命周期策略
# ═══════════════════════════════════════════════════════════════


def should_use_fresh_session(task: str, mode: str = "") -> bool:
    """判断是否应使用全新 session（不复用历史记忆）。

    默认策略：
    - delegate_with_verification: 始终 fresh（每次 attempt 独立）
    - run_project_pipeline: 始终 fresh（每个 TaskNode 独立）
    - run_convergence_loop: 保留 loop 内上下文但每轮 sanitize
    - delegate_task / 聊天: 保留记忆（向后兼容）
    """
    if mode in ("verified", "pipeline", "fresh"):
        return True
    if mode == "convergence":
        return False  # keep context within loop, sanitize per round
    # 普通 delegate_task 保留记忆
    return False


# ═══════════════════════════════════════════════════════════════
# v4.1 统一模型调用出口
# ═══════════════════════════════════════════════════════════════


def call_llm_once(prompt: str,
                  system_prompt: str = "",
                  tier: str = "normal",
                  max_tokens: int = 4096,
                  provider_key: str | None = None,
                  model_id: str | None = None,
                  disable_thinking: bool = False) -> str:
    """统一 LLM 调用入口。禁止业务函数绕开此函数直接调底层 client。

    - 自动解析 (provider, model) from tier
    - 自动 sanitize messages
    - 失败返回清晰错误文本，不抛裸异常
    """
    # 1. 解析 provider / model
    if provider_key and model_id:
        pk, mid = provider_key, model_id
    else:
        pk, mid = _resolve_route(tier)

    # 2. 构建 messages
    messages = [{"role": "user", "content": prompt}]
    messages = sanitize_messages_for_provider(messages, pk)

    # 3. 调用
    try:
        result = call_llm_multi_turn(
            provider_key=pk,
            model_id=mid,
            messages=messages,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            disable_thinking=disable_thinking,
        )
        return result
    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"[call_llm_once error] provider={pk} model={mid} tier={tier}: {e}"


# ── Anthropic ↔ OpenAI 格式转换 ──────────────────────────────

def anthropic_tools_to_openai(anthropic_tools: list) -> list:
    """Anthropic tool 定义 → OpenAI function 定义。"""
    openai_tools = []
    for t in anthropic_tools:
        openai_tools.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        })
    return openai_tools


def anthropic_messages_to_openai(anthropic_msgs: list, system_prompt: str = "") -> list:
    """Anthropic 消息格式 → OpenAI 消息格式。

    Anthropic 的 system prompt 是 API 参数，OpenAI 的是消息列表中的 system 角色。
    Anthropic 的 tool_result 是 user 消息中的 content block，
    OpenAI 的是独立的 tool 角色。
    """
    openai_msgs = []
    if system_prompt:
        openai_msgs.append({"role": "system", "content": system_prompt})

    for msg in anthropic_msgs:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if isinstance(content, str):
            openai_msgs.append({"role": role, "content": content})
            continue

        # content 是列表（多 block 消息）
        text_parts = []
        tool_calls = []
        tool_results = []

        for block in content:
            if isinstance(block, dict):
                btype = block.get("type", "")
                if btype == "text":
                    text_parts.append(block.get("text", ""))
                elif btype == "tool_use":
                    tool_calls.append({
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                        },
                    })
                elif btype == "tool_result":
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": block.get("content", ""),
                    })
                elif btype == "thinking":
                    pass  # OpenAI 不支持 thinking 块，跳过

        if tool_calls:
            openai_msgs.append({
                "role": "assistant",
                "content": "\n".join(text_parts) if text_parts else None,
                "tool_calls": tool_calls,
            })
        elif text_parts:
            openai_msgs.append({"role": role, "content": "\n".join(text_parts)})

        # tool results 作为独立消息
        for tr in tool_results:
            openai_msgs.append(tr)

    return openai_msgs


def openai_response_to_anthropic_blocks(response) -> list:
    """将 OpenAI chat.completions 响应转为 Anthropic 风格的 content blocks。

    返回 list of blocks，兼容现有的 Anthropic content 处理逻辑。
    """
    blocks = []
    choice = response.choices[0]
    msg = choice.message

    if msg.content:
        blocks.append({"type": "text", "text": msg.content})

    if hasattr(msg, "tool_calls") and msg.tool_calls:
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, TypeError):
                args = {}
            blocks.append({
                "type": "tool_use",
                "id": tc.id,
                "name": tc.function.name,
                "input": args,
            })

    # 思考内容（如果模型返回 reasoning_tokens）
    if hasattr(msg, "reasoning_content") and msg.reasoning_content:
        blocks.insert(0, {"type": "thinking", "thinking": msg.reasoning_content, "signature": ""})

    return blocks


# ── 统一 LLM 调用 ────────────────────────────────────────────

def call_llm(provider_key: str, model_id: str, messages: list,
             system_prompt: str = "", tools: list = None,
             max_tokens: int = 4096, disable_thinking: bool = False) -> list:
    """统一的 LLM 调用接口：根据 provider 自动选择 Anthropic 或 OpenAI 格式。

    Args:
        provider_key: PROVIDERS 中的键（deepseek/dashscope/minimax/gpt）
        model_id: 模型 ID
        messages: Anthropic 格式的消息列表
        system_prompt: system prompt 文本
        tools: Anthropic 格式的工具定义列表
        max_tokens: 最大输出 token
        disable_thinking: DeepSeek 系禁用 thinking mode，避免 thinking block 多轮污染

    Returns:
        Anthropic 风格的 content blocks 列表
    """
    provider = PROVIDERS.get(provider_key)
    if not provider:
        raise ValueError(f"未知厂商: {provider_key}。可用: {list(PROVIDERS.keys())}")

    if provider["type"] == "anthropic":
        # DeepSeek 走 Anthropic 兼容接口
        extra_params: dict = {}
        if disable_thinking:
            extra_params["thinking"] = {"type": "disabled"}

        response = provider["client"].messages.create(
            model=model_id,
            max_tokens=max_tokens,
            system=system_prompt,
            tools=tools or [],
            messages=messages,
            **extra_params,
        )
        # 直接返回 Anthropic content blocks
        blocks = []
        for block in response.content:
            if block.type == "text":
                blocks.append({"type": "text", "text": block.text})
            elif block.type == "thinking":
                blocks.append({"type": "thinking", "thinking": block.thinking, "signature": block.signature})
            elif block.type == "tool_use":
                blocks.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
        return blocks

    else:
        # OpenAI 兼容接口（千问 / MiniMax / GPT）
        openai_msgs = anthropic_messages_to_openai(messages, system_prompt)
        openai_tools = anthropic_tools_to_openai(tools) if tools else None

        kwargs = {
            "model": model_id,
            "messages": openai_msgs,
            "max_tokens": max_tokens,
        }
        if openai_tools:
            kwargs["tools"] = openai_tools

        response = provider["client"].chat.completions.create(**kwargs)
        return openai_response_to_anthropic_blocks(response)


def call_llm_multi_turn(provider_key: str, model_id: str, messages: list,
                         system_prompt: str = "", tools: list = None,
                         max_turns: int = 10, max_tokens: int = 4096,
                         log_callback=None, execute_tool_fn=None,
                         disable_thinking: bool = False) -> str:
    """多轮 LLM 调用：自动处理工具调用循环，返回最终文本结果。

    Args:
        provider_key, model_id: 厂商和模型
        messages: Anthropic 格式消息列表（会被原地修改！）
        system_prompt: 系统提示
        tools: 工具定义列表（Anthropic 格式）
        max_turns: 最大对话轮数
        max_tokens: 单次最大输出
        log_callback: function(name, args, result) 工具调用回调
        execute_tool_fn: 自定义工具执行 function(name, args) → str，默认用全局 execute_tool
        disable_thinking: DeepSeek 系禁用 thinking mode

    Returns:
        最终的文本回复
    """
    tool_executor = execute_tool_fn or execute_tool
    final_text = ""
    last_tool_sig = ""    # track consecutive identical tool calls
    repeat_count = 0
    total_reads = 0       # track total read_file calls this turn
    total_writes = 0      # track total write_file calls this turn

    for _turn in range(max_turns):
        force_stop = False
        # v4.2: detect repetitive tool calls (model stuck in read loop)
        if repeat_count >= 3:
            messages.append({
                "role": "user",
                "content": (
                    "你已经连续多次执行相同的操作。请立即停止，"
                    "基于已有的信息给出你的结论。不要再调用任何工具。"
                ),
            })
            repeat_count = 0
            force_stop = True
        elif total_reads >= 3:
            messages.append({
                "role": "user",
                "content": (
                    "你已经读取了足够多的文件内容。请立即给出你的分析和结论，"
                    "不要再调用 read_file。直接输出文本回复。"
                ),
            })
            total_reads = 0
            force_stop = True
        elif total_writes >= 2:
            messages.append({
                "role": "user",
                "content": (
                    "你已经写入了足够多次文件。请立即给出你的最终总结，"
                    "不要再调用 write_file。直接输出文本回复。"
                ),
            })
            total_writes = 0
            force_stop = True

        # v4.1: sanitize messages before every API call to prevent thinking block pollution
        safe_messages = sanitize_messages_for_provider(messages, provider_key)
        blocks = call_llm(provider_key, model_id, safe_messages,
                          system_prompt=system_prompt, tools=tools,
                          max_tokens=max_tokens,
                          disable_thinking=disable_thinking)

        assistant_content = []
        has_tool_use = False

        for block in blocks:
            if block["type"] == "text":
                assistant_content.append(block)
                final_text = block["text"]
            elif block["type"] == "thinking":
                assistant_content.append(block)
            elif block["type"] == "tool_use":
                if force_stop:
                    # v4.2: 模型仍在读文件，强制终止本轮
                    continue
                has_tool_use = True
                tool_name = block["name"]
                tool_args = block["input"]
                tool_id = block["id"]

                # v4.2: track repetitive identical tool calls
                tool_sig = f"{tool_name}:{json.dumps(tool_args, sort_keys=True, ensure_ascii=False)}"
                if tool_sig == last_tool_sig:
                    repeat_count += 1
                else:
                    last_tool_sig = tool_sig
                    repeat_count = 1
                if tool_name in ("read_file", "find_files", "search_code", "fetch_url"):
                    total_reads += 1
                if tool_name in ("write_file", "save_template"):
                    total_writes += 1

                if log_callback:
                    log_callback(tool_name, tool_args, "")

                try:
                    result = tool_executor(tool_name, tool_args)
                except Exception as e:
                    result = f"工具执行出错: {e}"

                if log_callback:
                    log_callback(tool_name, tool_args, result)

                assistant_content.append(block)
                messages.append({"role": "assistant", "content": assistant_content})
                assistant_content = []

                messages.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": result}],
                })

        if assistant_content:
            messages.append({"role": "assistant", "content": assistant_content})

        if not has_tool_use:
            break

    return final_text

# 复杂度判断关键词（简单启发式，Worker 用 AI 自主判断 > 关键词匹配）
COMPLEXITY_SIGNALS_SIMPLE = [
    "转换", "格式", "读取", "查看", "列出", "获取", "当前时间",
    "convert", "format", "read", "list", "fetch", "get time",
]
COMPLEXITY_SIGNALS_COMPLEX = [
    "架构", "设计", "重构", "优化", "协调", "多模块", "跨模块", "冲突",
    "architecture", "design", "refactor", "optimize", "coordinate",
    "cross-module", "conflict", "安全审计", "性能分析",
]
COMPLEXITY_SIGNALS_MAJOR = [
    "重大", "关键", "数据模型变更", "pipeline 重构", "破坏性",
    "critical", "breaking", "schema change", "pipeline redesign",
    "安全漏洞", "资金", "合规", "用户数据",
]

print_lock = threading.Lock()

# ── 输出截断保护 ──────────────────────────────────────────
MAX_OUTPUT_CHARS = 6000   # 单次工具返回最大字符数
MAX_SEARCH_RESULTS = 50   # 搜索最多返回条数


def truncate(text: str, max_chars: int = MAX_OUTPUT_CHARS) -> str:
    """截断过长输出，防止 token 爆炸。"""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n... (截断，原输出共 {len(text)} 字符)"


# ── 全局 Worker 配置（供工具间访问） ────────────────────────
_workers_config: dict = {}
_deputy_config: dict = {}

# ── Worker 会话记忆 ───────────────────────────────────────
worker_sessions: dict[str, list] = {}
SESSION_FILE = "worker_sessions.json"
SCORE_FILE = "worker_scores.json"
TASK_BOARD_FILE = "task_board.json"
KNOWLEDGE_FILE = "team_knowledge.json"


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


# ── 任务看板 ──────────────────────────────────────────────
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


# ── 项目状态管理 ──────────────────────────────────────────
PROJECT_STATE_DIR = "project_states"


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


# ── Worker 模型自适应 ──────────────────────────────────────
def _resolve_route(tier_key: str) -> tuple:
    """解析路由，如果 GPT 不可用则自动回退到备用厂商。"""
    route = MODEL_TIERS.get(tier_key, MODEL_TIERS["normal"])
    if route[0] == "gpt" and "gpt" not in PROVIDERS:
        if tier_key == "major":
            return FALLBACK_MAJOR
        elif tier_key == "complex":
            return FALLBACK_COMPLEX
        return ("deepseek", "deepseek-v4-pro[1M]")
    return route


def select_worker_model(task: str, worker_name: str,
                         previous_failures: int = 0) -> tuple:
    """根据任务复杂度自主选择合适的模型。

    返回 (model, complexity_label, reason)。

    判断逻辑（优先级从高到低）：
    1. 前次模型两次尝试失败 → 自动升级
    2. 关键词匹配 → simple / complex / major
    3. 默认 → normal
    """
    task_lower = task.lower()

    # 连续失败 → 自动升级到 GPT（仅当当前不是 GPT 时）
    if previous_failures >= 2:
        # 检查 UPGRADE_TARGET 是否可用，否则用回退
        if UPGRADE_TARGET[0] in PROVIDERS:
            route = UPGRADE_TARGET
        else:
            route = FALLBACK_COMPLEX
        return (route, "complex",
                f"{worker_name} 前次模型连续 {previous_failures} 次失败，自动升级到 [{route[0]}]{route[1]}")

    # 重大信号
    for kw in COMPLEXITY_SIGNALS_MAJOR:
        if kw.lower() in task_lower:
            route = _resolve_route("major")
            return (route, "major",
                    f"任务涉及重大决策（匹配关键词: {kw}），启用 [major] {route[1]}")

    # 复杂信号
    complex_count = sum(1 for kw in COMPLEXITY_SIGNALS_COMPLEX if kw.lower() in task_lower)
    if complex_count >= 1:
        route = _resolve_route("complex")
        return (route, "complex",
                f"任务复杂度高（匹配 {complex_count} 个复杂信号），启用 [complex] {route[1]}")

    # 简单信号（至少 1 个，且无复杂/重大信号）
    simple_count = sum(1 for kw in COMPLEXITY_SIGNALS_SIMPLE if kw.lower() in task_lower)
    if simple_count >= 1 and complex_count == 0:
        route = _resolve_route("simple")
        return (route, "simple",
                f"简单任务（匹配简单信号: {simple_count} 个），用低成本模型")

    # 默认
    route = _resolve_route("normal")
    return (route, "normal", "常规任务，使用默认模型")


def select_manager_model(user_input: str, context_complexity: str = "") -> tuple:
    """Manager 模型路由：常态 DeepSeek，复杂 → 升级，重大 → 最强 + 标记。

    返回 (model, should_confirm_with_user)。
    """
    task_lower = user_input.lower()

    # 重大决策信号 → GPT-5.5 + 需要用户确认
    for kw in COMPLEXITY_SIGNALS_MAJOR:
        if kw.lower() in task_lower:
            route = _resolve_route("major")
            return (route, True)

    # 复杂调度信号 → GPT-5.4
    for kw in COMPLEXITY_SIGNALS_COMPLEX:
        if kw.lower() in task_lower:
            route = _resolve_route("complex")
            return (route, False)

    # 上下文传递的复杂度
    if context_complexity == "complex":
        return (_resolve_route("complex"), False)
    if context_complexity == "major":
        return (_resolve_route("major"), True)

    # 默认 → DeepSeek 1M
    return (_resolve_route("normal"), False)


# ── 项目启动：动态分工 ─────────────────────────────────────
def project_setup(workers: dict, project_description: str) -> str:
    """Manager 接到项目后，用 AI 分析需求并动态分配领域。

    由 Manager 的 AI 生成分工表，写入 project_state.json。"""
    roster = "\n".join(
        f"  - 「{w['name']}」{w['role']}：{w['description']}（工具: {', '.join(w['tool_names'])}）"
        for w in workers.values()
    )

    system_prompt = (
        "你是一个技术项目经理。现在接到一个新项目，你需要：\n"
        "1. 分析项目需求，拆解为 3-6 个可并行/串行的 Pipeline 步骤\n"
        "2. 根据每个成员的职能和工具，分配合适的临时领域和 scope\n"
        "3. 输出 JSON 格式的《项目分工表》\n\n"
        "团队成员：\n"
        f"{roster}\n\n"
        "输出格式（严格 JSON）：\n"
        "{\n"
        '  "project": "项目名称",\n'
        '  "pipeline_steps": [\n'
        '    {"name": "步骤名", "description": "...", "depends_on": [], "status": "todo"}\n'
        '  ],\n'
        '  "assignments": {\n'
        '    "Worker名": {"domain": "临时领域", "scope": "操作范围", "tasks": ["具体任务1", "具体任务2"]}\n'
        '  },\n'
        '  "review_plan": {"reviewer": "Sophia", "validator": "Nathaniel", "final_check": "Victor"}\n'
        "}\n\n"
        "规则：\n"
        "- 不要给成员永久添加领域，这只是本项目内的临时分工\n"
        "- 根据项目实际需求匹配成员，不需要每个成员都参与\n"
        "- Pipeline 步骤要具体，有明确的输出\n"
        "- 确保审核链：Sophia(质量审核) → Nathaniel(数据验证) → Victor(最终复核)"
    )

    final_result = ""
    retry_context = ""

    for attempt in range(3):
        # v4.1: unified call through call_llm_once — no direct client access
        prompt = f"[新项目]\n{project_description}{retry_context}"
        final_result = call_llm_once(
            prompt=prompt,
            system_prompt=system_prompt,
            tier="complex",
            max_tokens=4096,
        )

        # 尝试解析 JSON
        try:
            # 提取 JSON 块
            text = final_result
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]
            plan = json.loads(text.strip())

            project_name = plan.get("project", "unnamed")
            state = load_project_state(project_name)
            state["pipeline_steps"] = plan.get("pipeline_steps", [])
            state["assignments"] = plan.get("assignments", {})
            state["review_plan"] = plan.get("review_plan", {})
            state["project_description"] = project_description
            save_project_state(project_name, state)

            # 格式化输出
            lines = [
                f"\n{'=' * 55}",
                f"  项目分工表: {project_name}",
                f"{'=' * 55}",
            ]
            lines.append("\n[Pipeline 步骤]")
            for step in state["pipeline_steps"]:
                deps = f" (依赖: {', '.join(step.get('depends_on', []))})" if step.get("depends_on") else ""
                lines.append(f"  [ ] {step['name']}: {step.get('description', '')[:60]}{deps}")

            lines.append("\n[成员分工]")
            for name, assign in state["assignments"].items():
                lines.append(f"  「{name}」→ {assign.get('domain', '')}")
                lines.append(f"      scope: {assign.get('scope', '')}")
                tasks = assign.get("tasks", [])
                for t in tasks:
                    lines.append(f"        - {t}")

            lines.append(f"\n[审核链] {state.get('review_plan', {})}")
            lines.append(f"\n状态文件: {PROJECT_STATE_DIR}/{project_name}_state.json")
            lines.append(f"{'=' * 55}")

            return "\n".join(lines)

        except (json.JSONDecodeError, KeyError) as e:
            # JSON 解析失败，让 AI 再试一次
            retry_context = f"\n\n[上一轮 JSON 格式不正确: {e}] 请严格按 JSON 格式重新输出。"
            continue

    return f"项目规划失败：AI 未能输出有效 JSON。原始回复:\n{final_result[:500]}"


# ── 监控面板 ──────────────────────────────────────────────
_worker_api_calls: dict[str, int] = {}
_worker_failures: dict[str, int] = {}


def track_api_call(worker_name: str):
    _worker_api_calls[worker_name] = _worker_api_calls.get(worker_name, 0) + 1


def track_failure(worker_name: str):
    _worker_failures[worker_name] = _worker_failures.get(worker_name, 0) + 1


def get_dashboard() -> str:
    """获取团队状态面板（含趋势和绩效）。"""
    tasks = _load_json(TASK_BOARD_FILE, [])
    scores = _load_json(SCORE_FILE, [])
    knowledge = _load_json(KNOWLEDGE_FILE, [])

    status_counts = {"todo": 0, "in_progress": 0, "done": 0, "failed": 0}
    priority_counts = {}
    for t in tasks:
        status_counts[t["status"]] = status_counts.get(t["status"], 0) + 1
        p = t.get("priority", "unknown")
        priority_counts[p] = priority_counts.get(p, 0) + 1

    # 任务完成率
    total_tasks = len(tasks)
    done_rate = f"{status_counts['done'] / total_tasks * 100:.0f}%" if total_tasks > 0 else "N/A"

    lines = [
        "=" * 55,
        "  📊 团队状态面板",
        "=" * 55,
        f"  任务: {total_tasks} 个 | 完成率: {done_rate}",
        f"  待办 {status_counts['todo']} | 进行中 {status_counts['in_progress']} | 完成 {status_counts['done']} | 失败 {status_counts['failed']}",
        f"  优先级分布: {priority_counts}",
        f"  知识库: {len(knowledge)} 条 | 评分记录: {len(scores)} 条",
    ]

    # 员工绩效趋势
    if scores and _workers_config:
        worker_scores_summary = {}
        for s in scores:
            name = s.get("worker_name", "unknown")
            if name not in worker_scores_summary:
                worker_scores_summary[name] = {"count": 0, "total": 0, "scores": []}
            worker_scores_summary[name]["count"] += 1
            worker_scores_summary[name]["total"] += s.get("total", 0)
            worker_scores_summary[name]["scores"].append(s.get("total", 0))

        lines.append("  --- 员工绩效 ---")
        for name in _workers_config:
            if name in worker_scores_summary:
                ws = worker_scores_summary[name]
                avg = ws["total"] / ws["count"]
                recent = ws["scores"][-3:] if len(ws["scores"]) >= 3 else ws["scores"]
                trend = "📈" if len(recent) >= 2 and recent[-1] > recent[0] else ("📉" if len(recent) >= 2 and recent[-1] < recent[0] else "➡️")
                lines.append(f"  {name}: {ws['count']}次评分 | 均分 {avg:.1f}/15 | 趋势 {trend}")
            else:
                lines.append(f"  {name}: 暂无评分")

    # Worker 用量
    if _workers_config:
        lines.append("  --- 用量统计 ---")
        for name in _workers_config:
            calls = _worker_api_calls.get(name, 0)
            fails = _worker_failures.get(name, 0)
            rate = f"{(1 - fails/max(calls,1))*100:.0f}%" if calls > 0 else "N/A"
            lines.append(f"  {name}: {calls} 次调用 | {fails} 次失败 | 成功率 {rate}")

    return "\n".join(lines)


# ── 全部工具定义 ──────────────────────────────────────────
ALL_TOOLS = {
    "get_current_time": {
        "name": "get_current_time",
        "description": "获取当前日期和时间",
        "input_schema": {
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": "时区，如 Asia/Shanghai，默认为 Asia/Shanghai",
                }
            },
        },
    },
    "read_file": {
        "name": "read_file",
        "description": "读取本地文件的内容",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "要读取的文件路径",
                }
            },
            "required": ["file_path"],
        },
    },
    "write_file": {
        "name": "write_file",
        "description": "将内容写入本地文件（会覆盖已有文件）",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "要写入的文件路径",
                },
                "content": {
                    "type": "string",
                    "description": "要写入的文件内容",
                },
            },
            "required": ["file_path", "content"],
        },
    },
    "run_command": {
        "name": "run_command",
        "description": "执行一条 shell 命令并返回输出",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令"}
            },
            "required": ["command"],
        },
    },
    "fetch_url": {
        "name": "fetch_url",
        "description": "获取一个 URL 的内容（HTTP GET），可用于查阅在线文档、API 参考等",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要获取的 URL（https://...）"},
            },
            "required": ["url"],
        },
    },
    "search_code": {
        "name": "search_code",
        "description": (
            "在项目文件中搜索指定的文本或正则表达式，返回匹配的文件路径和行内容。"
            "用于快速定位函数定义、变量引用、TODO 标记、潜在 bug 等。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "搜索的文本或正则表达式，如 'def divide'、'eval('、'TODO'",
                },
                "path": {
                    "type": "string",
                    "description": "搜索目录，默认为当前目录。如 'src/'、'.'",
                },
                "file_types": {
                    "type": "string",
                    "description": "文件类型过滤，如 '*.py'、'*.js'、'*.{py,js}'，留空则搜所有文本文件",
                },
            },
            "required": ["pattern"],
        },
    },
    "ask_coworker": {
        "name": "ask_coworker",
        "description": (
            "向另一位员工求助——把你的问题发给他，他会用他的工具和专长帮你解决，"
            "然后把答案返回给你。用于遇到困难时找更专业的同事帮忙，或者需要分工协作。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "worker_name": {
                    "type": "string",
                    "description": "求助的员工名称，如「亚历克斯」「索菲亚」",
                },
                "question": {
                    "type": "string",
                    "description": "你的问题或请求，越具体越好。可以附上当前遇到的上下文。",
                },
            },
            "required": ["worker_name", "question"],
        },
    },
    "search_knowledge": {
        "name": "search_knowledge",
        "description": "搜索团队共享知识库，查找之前记录的经验、决策和最佳实践",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词，如 'calculator bug'、'deployment'"},
            },
            "required": ["query"],
        },
    },
    "convert_document": {
        "name": "convert_document",
        "description": "将 PDF/Word/PPT/Excel 等文件转换为 Markdown 文本，方便阅读和分析",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "要转换的文件路径"},
            },
            "required": ["file_path"],
        },
    },
    "github_create_pr": {
        "name": "github_create_pr",
        "description": "在 GitHub 上创建 Pull Request",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "PR 标题"},
                "body": {"type": "string", "description": "PR 描述"},
                "base_branch": {"type": "string", "description": "目标分支，默认 main"},
            },
            "required": ["title", "body"],
        },
    },
    "github_list_issues": {
        "name": "github_list_issues",
        "description": "列出 GitHub Issues",
        "input_schema": {
            "type": "object",
            "properties": {
                "state": {"type": "string", "enum": ["open", "closed", "all"], "description": "状态过滤，默认 open"},
            },
        },
    },
    "save_template": {
        "name": "save_template",
        "description": "将当前的工作流程或经验沉淀为可复用的模板文件，供团队后续参考",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "模板名称"},
                "content": {"type": "string", "description": "模板内容（Markdown 格式）"},
            },
            "required": ["name", "content"],
        },
    },
    "find_files": {
        "name": "find_files",
        "description": (
            "按文件名模式查找文件，返回匹配的文件路径列表。"
            "用于快速找到特定名称的文件，如 'test_*.py'、'*.json'。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "文件名匹配模式，如 'test_*.py'、'*.json'、'*.md'",
                },
                "path": {
                    "type": "string",
                    "description": "搜索目录，默认为当前目录。如 'src/'、'.'",
                },
            },
            "required": ["pattern"],
        },
    },
}


# ── 工具执行 ──────────────────────────────────────────────
def execute_tool(name: str, args: dict) -> str:
    if args is None:
        args = {}
    if name == "get_current_time":
        tz = args.get("timezone", "Asia/Shanghai")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return f"当前时间 ({tz}): {now}"

    if name == "read_file":
        path = args["file_path"]
        try:
            with open(path, encoding="utf-8") as f:
                return truncate(f.read())
        except FileNotFoundError:
            return f"文件不存在: {path}"
        except Exception as e:
            return f"读取失败: {e}"

    if name == "write_file":
        path = args["file_path"]
        content = args["content"]
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"文件写入成功: {path} ({len(content)} 字符)"
        except Exception as e:
            return f"写入失败: {e}"

    if name == "run_command":
        cmd = args["command"]
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, encoding="utf-8", errors="replace",
                timeout=30, cwd=os.getcwd(),
            )
            stdout = (result.stdout or "").strip()
            stderr = (result.stderr or "").strip()
            output = stdout or stderr
            return truncate(output) if output else "(无输出)"
        except subprocess.TimeoutExpired:
            return "命令超时（30秒）"
        except Exception as e:
            return f"命令执行失败: {e}"

    if name == "fetch_url":
        url = args["url"]
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "MultiAgent/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                status = resp.status
                return truncate(f"HTTP {status}\n{body}", 10000)
        except urllib.error.URLError as e:
            return f"请求失败: {e}"
        except Exception as e:
            return f"获取失败: {e}"

    if name == "search_code":
        pattern = args["pattern"]
        search_path = args.get("path", ".")
        file_types = args.get("file_types", "")

        cmd_parts = ["grep", "-rn", "--include=" + (file_types or "*")]
        # 排除常见二进制和无关目录
        excludes = [".git", "__pycache__", "node_modules", ".venv", "venv", "*.pyc"]
        for ex in excludes:
            cmd_parts.append(f"--exclude-dir={ex}" if "/" not in ex and "\\" not in ex else f"--exclude={ex}")
        cmd_parts.append(pattern)
        cmd_parts.append(search_path)

        try:
            cmd_str = " ".join(cmd_parts)
            result = subprocess.run(
                cmd_str, shell=True, capture_output=True,
                encoding="utf-8", errors="replace", timeout=15,
                cwd=os.getcwd(),
            )
            output = (result.stdout or "").strip()
            if not output:
                return f"未找到匹配「{pattern}」的结果"
            lines = output.split("\n")
            total = len(lines)
            if total > MAX_SEARCH_RESULTS:
                output = "\n".join(lines[:MAX_SEARCH_RESULTS])
                output += f"\n\n... (共 {total} 条匹配，仅显示前 {MAX_SEARCH_RESULTS} 条。请缩小搜索范围)"
            return truncate(output)
        except subprocess.TimeoutExpired:
            return "搜索超时（15秒），请缩小搜索范围"
        except Exception as e:
            return f"搜索失败: {e}"

    if name == "find_files":
        pattern = args["pattern"]
        search_path = args.get("path", ".")

        try:
            find_cmd = (
                f"find {shlex.quote(search_path)} -name {shlex.quote(pattern)} "
                f"-not -path '*/.git/*' -not -path '*/__pycache__/*' "
                f"-not -path '*/node_modules/*' -not -path '*/.venv/*' "
                f"-not -path '*/venv/*' -type f"
            )
            result = subprocess.run(
                find_cmd, shell=True, capture_output=True,
                encoding="utf-8", errors="replace", timeout=10,
                cwd=os.getcwd(),
            )
            output = (result.stdout or "").strip()
            if not output:
                return f"未找到匹配「{pattern}」的文件"
            lines = output.split("\n")
            total = len(lines)
            if total > MAX_SEARCH_RESULTS:
                output = "\n".join(lines[:MAX_SEARCH_RESULTS])
                output += f"\n\n... (共 {total} 个文件，仅显示前 {MAX_SEARCH_RESULTS} 个。请缩小搜索范围)"
            return truncate(output, 3000)
        except subprocess.TimeoutExpired:
            return "查找超时（10秒）"
        except Exception as e:
            return f"查找失败: {e}"

    if name == "convert_document":
        path = args["file_path"]
        try:
            from markitdown import MarkItDown
            md = MarkItDown()
            result = md.convert(path)
            return truncate(result.text_content)
        except ImportError:
            return "markitdown 未安装，请运行: pip install markitdown"
        except FileNotFoundError:
            return f"文件不存在: {path}"
        except Exception as e:
            return f"文档转换失败: {e}"

    if name == "github_create_pr":
        title = args["title"]
        body = args["body"]
        base = args.get("base_branch", "main")
        try:
            result = subprocess.run(
                f'gh pr create --title "{title}" --body "{body}" --base {base}',
                shell=True, capture_output=True,
                encoding="utf-8", errors="replace", timeout=30,
                cwd=os.getcwd(),
            )
            output = (result.stdout or "").strip() or (result.stderr or "").strip()
            return output if output else "PR 创建失败（请确认 gh CLI 已登录且当前在 git 仓库中）"
        except Exception as e:
            return f"GitHub 操作失败: {e}"

    if name == "github_list_issues":
        state = args.get("state", "open")
        try:
            result = subprocess.run(
                f"gh issue list --state {state} --limit 20",
                shell=True, capture_output=True,
                encoding="utf-8", errors="replace", timeout=20,
                cwd=os.getcwd(),
            )
            output = (result.stdout or "").strip()
            return output if output else "没有找到 Issues。"
        except Exception as e:
            return f"GitHub 操作失败: {e}"

    if name == "save_template":
        name = args["name"]
        content = args["content"]
        filepath = f"templates/{name}.md"
        try:
            os.makedirs("templates", exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            return f"模板已保存: {filepath}"
        except Exception as e:
            return f"保存模板失败: {e}"

    if name == "search_knowledge":
        return search_knowledge(args["query"])

    if name == "ask_coworker":
        coworker_name = args["worker_name"]
        question = args["question"]

        if not _workers_config:
            return "错误：员工配置未加载，无法求助"

        if coworker_name not in _workers_config:
            available = ", ".join(_workers_config.keys())
            return f"找不到员工「{coworker_name}」。可选: {available}"

        cfg = _workers_config[coworker_name]
        with print_lock:
            print(f"\n      🤝 向 Worker-{coworker_name}（{cfg['role']}）求助...\n")

        # 运行同事的 one-shot 查询（保留记忆，让他有上下文）
        coworker_result = run_worker(cfg, question, use_memory=True)

        answer = coworker_result["result"]
        with print_lock:
            print(f"      🤝 Worker-{coworker_name} 已回复\n")
        return f"[Worker-{coworker_name}（{cfg['role']}）的回复]\n{answer}"

    return f"未知工具: {name}"


# ── 加载员工配置 ──────────────────────────────────────────
def load_workers(config_path: str = "workers.json") -> dict:
    """加载 workers.json，返回 { name: config } 映射。"""
    if not os.path.exists(config_path):
        print(f"[警告] 未找到 {config_path}，使用默认配置")
        return {}

    with open(config_path, encoding="utf-8") as f:
        data = json.load(f)

    workers = {}
    deputy = {}

    for w in data["workers"]:
        if w.get("is_deputy"):
            deputy = {
                "name": w["name"],
                "role": w["role"],
                "description": w["description"],
                "client": default_client,  # 副经理仍用 Anthropic client
                "model": w.get("model") or DEFAULT_MODEL,
            }
            continue

        tools = [ALL_TOOLS[t] for t in w["tools"] if t in ALL_TOOLS]
        model = w.get("model") or DEFAULT_MODEL

        workers[w["name"]] = {
            "name": w["name"],
            "role": w["role"],
            "description": w["description"],
            "tools": tools,
            "tool_names": w["tools"],
            "model": model,
            # client 不再需要：run_worker() 通过 call_llm_multi_turn() 动态路由厂商
        }

    global _workers_config, _deputy_config
    _workers_config.clear()
    _workers_config.update(workers)
    _deputy_config.clear()
    _deputy_config.update(deputy)
    return workers


# ── Worker 执行循环 ────────────────────────────────────────
def run_worker(worker_cfg: dict, task: str, use_memory: bool = True,
               project_name: str = "",
               fresh_session: bool = False,
               session_scope: str = "") -> dict:
    """启动一个 Worker，使用其专属工具和配置执行任务。支持多轮对话记忆。

    v3 升级：多厂商模型自适应 —— Worker 自主判断任务复杂度，
    选择最佳厂商和模型（DeepSeek/千问/MiniMax/GPT）。

    v4.1 升级：fresh_session=True 时不读取历史记忆，
    session_scope 可将本次会话写入隔离的 session key。"""
    name = worker_cfg["name"]
    role = worker_cfg["role"]
    tools = worker_cfg["tools"]
    tool_names = worker_cfg["tool_names"]
    base_model = worker_cfg["model"]

    # ── 模型自适应：自主判断复杂度，选择厂商+模型 ──
    (worker_provider_key, worker_model_id), complexity, model_reason = select_worker_model(task, name)
    base_provider = "deepseek"  # 默认厂商

    if (worker_provider_key, worker_model_id) != (base_provider, base_model):
        with print_lock:
            print(f"\n      [*] Worker-{name} 模型路由: [{base_provider}]{base_model}")
            print(f"         → [{worker_provider_key}] {worker_model_id} ({complexity})")
            print(f"         原因: {model_reason}")

    # 记录模型使用
    if project_name:
        log_model_usage(project_name, name, task, worker_provider_key, worker_model_id)

    indent = " " * 4
    provider_label = f"[{worker_provider_key}]" if worker_provider_key != "deepseek" else ""
    prefix = f"[Worker-{name}|{role}]{provider_label}"
    log = []

    def log_print(text: str):
        log.append(text)
        with print_lock:
            print(f"{indent}{prefix} {text}")

    system_prompt = (
        f"你是「{name}」，职位是「{role}」。管理者给你指派了一项任务。\n"
        f"你的能力范围：{', '.join(tool_names)}。\n"
        "请在自己的能力范围内尽力完成任务。如果任务超出你的能力范围，"
        "请如实说明，不要强行操作。完成后请清晰汇报。\n"
        "重要行为守则：\n"
        "- 【关键】每个文件最多读取一次。读取后立即进行分析，不要重复读取同一文件。\n"
        "- 【关键】读完所有必要文件后，立即给出结论和输出，不要再调用工具。\n"
        "- 完成后必须用 JSON 格式总结：{\"status\": \"success|partial|failed\", \"summary\": \"...\", \"artifacts\": [...]}\n"
        "- 如果发现了值得团队记住的经验教训或 bug 模式，用 save_template 或 search_knowledge 记录下来\n"
        "- 遇到问题优先用 ask_coworker 找合适的同事求助，不要独自硬撑"
    )

    # 多轮记忆：复用之前的对话历史
    base_key = f"{name}|{role}"
    if session_scope:
        session_key = f"{base_key}|{session_scope}"
    else:
        session_key = base_key

    if use_memory and not fresh_session and session_key in worker_sessions:
        messages = worker_sessions[session_key]
        messages.append({"role": "user", "content": f"[新任务] {task}"})
        # v4.1: sanitize loaded session to strip stale thinking blocks
        messages = sanitize_messages_for_provider(messages, worker_provider_key)
        log_print("(记得之前的对话上下文)")
    else:
        if fresh_session:
            log_print("(全新会话)")
        messages = [{"role": "user", "content": task}]

    # 工具回调：权限检查 + 日志
    def tool_callback(tool_name: str, tool_args: dict, tool_result: str):
        nonlocal log
        if tool_result:
            log_print(f"[工具返回: {tool_result[:300]}]")
        else:
            log_print(f"[工具: {tool_name}({json.dumps(tool_args, ensure_ascii=False)})]")

    # 工具执行包装：加权限检查
    allowed_names = {t["name"] for t in tools}

    def worker_execute_tool(name: str, args: dict) -> str:
        if name not in allowed_names:
            return f"权限不足：你没有使用「{name}」的权限"
        try:
            return execute_tool(name, args)
        except Exception as e:
            return f"工具执行出错: {e}"

    track_api_call(name)

    final_result = call_llm_multi_turn(
        provider_key=worker_provider_key,
        model_id=worker_model_id,
        messages=messages,
        system_prompt=system_prompt,
        tools=tools,
        max_turns=6,  # v4.2: 6 turns with read-detection at turn 3+
        log_callback=tool_callback,
        execute_tool_fn=worker_execute_tool,
        disable_thinking=False,  # v4.2: sanitize 层已处理 thinking block，不禁用避免模型行为异常
    )

    # ── 失败重试：DeepSeek 搞不定时升级到 GPT ──
    failure_signals = ["无法完成", "超出能力", "无法处理", "unable to", "cannot", "权限不足"]
    is_failure = any(kw in final_result for kw in failure_signals)

    if is_failure and worker_provider_key == "deepseek" and "gpt" in PROVIDERS:
        retry_provider, retry_model = UPGRADE_TARGET
        log_print(f"DeepSeek 未能完成任务，自动升级到 [{retry_provider}]{retry_model} 重试...")

        # 重置消息（去掉失败的尝试）
        messages.clear()
        messages.append({"role": "user", "content": task})

        final_result = call_llm_multi_turn(
            provider_key=retry_provider,
            model_id=retry_model,
            messages=messages,
            system_prompt=system_prompt,
            tools=tools,
            max_turns=5,  # v4.2: 5 turns for upgrade retry too
            log_callback=tool_callback,
            execute_tool_fn=worker_execute_tool,
            disable_thinking=False,  # v4.2: sanitize 层已处理 thinking block，不禁用避免模型行为异常
        )
        worker_provider_key = retry_provider
        worker_model_id = retry_model

    # 保存会话到记忆
    worker_sessions[session_key] = messages

    # v4: 附加结构化结果（向后兼容——只增不减）
    structured = _normalize_worker_result(final_result)
    structured.artifacts = _discover_artifacts(log)

    return {
        "log": log,
        "result": final_result,
        "messages": messages,
        "model_used": f"[{worker_provider_key}]{worker_model_id}",
        "complexity": worker_provider_key,
        "structured_result": asdict(structured),
    }


# ── 副经理执行循环 ────────────────────────────────────────
def run_deputy(task: str, manager_tools: list) -> dict:
    """运行副经理对话，使用 Manager 工具集。"""
    if not _deputy_config:
        return {"result": "错误：未配置副经理", "log": []}

    name = _deputy_config["name"]
    role = _deputy_config["role"]
    client = _deputy_config["client"]
    model = _deputy_config["model"]

    prefix = f"[Deputy-{name}|{role}]"
    log = []

    def log_print(text: str):
        log.append(text)
        with print_lock:
            print(f"\n  {prefix} {text}")

    system_prompt = (
        f"你是「{name}」，职位是「{role}」。正经理正在向你咨询或请求复核。\n"
        "你的职责是：\n"
        "1. 独立分析问题，给出不受正经理影响的独立判断\n"
        "2. 如果发现正经理的决策有漏洞或风险，明确指出\n"
        "3. 提供具体的改进建议，不只是同意或反对\n"
        "4. 你可以使用管理工具（delegate_task 等）来验证你的判断\n"
        "保持专业、客观，你就是团队的第二道防线。"
    )

    session_key = f"{name}|{role}"
    messages = [{"role": "user", "content": task}]
    final_result = ""

    for turn in range(5):
        track_api_call(name)
        # v4.1: sanitize before deputy API call
        safe_messages = sanitize_messages_for_provider(messages)
        response = client.messages.create(
            model=model, max_tokens=4096,
            system=system_prompt, tools=manager_tools,
            messages=safe_messages,
        )

        assistant_content = []
        has_tool_use = False

        for block in response.content:
            if block.type == "text":
                log_print(block.text)
                assistant_content.append({"type": "text", "text": block.text})
                final_result = block.text

            elif block.type == "thinking":
                assistant_content.append({
                    "type": "thinking", "thinking": block.thinking,
                    "signature": block.signature,
                })

            elif block.type == "tool_use":
                has_tool_use = True
                tool_name = block.name
                tool_args = block.input
                log_print(f"[管理工具: {tool_name}({json.dumps(tool_args, ensure_ascii=False)})]")

                try:
                    result = execute_manager_tool(tool_name, tool_args, _workers_config)
                except Exception as e:
                    result = f"工具执行出错: {e}"

                log_print(f"[工具返回: {result[:300]}]")

                assistant_content.append({
                    "type": "tool_use", "id": block.id,
                    "name": block.name, "input": block.input,
                })

                if assistant_content:
                    messages.append({"role": "assistant", "content": assistant_content})
                assistant_content = []

                messages.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": block.id, "content": result}],
                })

                follow_up = client.messages.create(
                    model=model, max_tokens=4096,
                    system=system_prompt, tools=manager_tools,
                    messages=messages,
                )
                for fb in follow_up.content:
                    if fb.type == "text":
                        log_print(fb.text)
                        final_result = fb.text
                        assistant_content.append({"type": "text", "text": fb.text})
                    elif fb.type == "thinking":
                        assistant_content.append({
                            "type": "thinking", "thinking": fb.thinking,
                            "signature": fb.signature,
                        })

        if assistant_content:
            messages.append({"role": "assistant", "content": assistant_content})

        if not has_tool_use:
            break

    return {"log": log, "result": final_result, "messages": messages}


# ═══════════════════════════════════════════════════════════════
# v4 验证闭环
# ═══════════════════════════════════════════════════════════════


def _run_verifier(verifier_cfg: dict, worker_result: WorkerResult,
                  original_task: str) -> VerificationResult:
    """运行单个验证者（如 Sophia/Nathaniel）对 Worker 产出做质量验证。"""
    prompt = (
        f"请验证以下工作产出的质量。\n\n"
        f"原始任务: {original_task}\n"
        f"Worker 状态: {worker_result.status}\n"
        f"Worker 摘要: {worker_result.summary}\n"
        f"产物列表: {json.dumps(worker_result.artifacts, ensure_ascii=False)}\n"
        f"发现的问题: {json.dumps(worker_result.issues, ensure_ascii=False)}\n\n"
        "请做到以下至少一项：\n"
        "  - 如果产物是文件，用 read_file 读取并检查\n"
        "  - 如果产物是代码，考虑是否能用 run_command 跑测试或 lint\n"
        "  - 如果有安全/设计问题，明确指出\n\n"
        "最终输出 JSON 格式的验证结果：\n"
        '{"verdict": "pass|reject|needs_retry|needs_replan", '
        '"score": 1-5, '
        '"blocking_issues": [{"severity": "critical|high|medium|low", '
        '"description": "...", "suggestion": "..."}], '
        '"retry_instruction": "如果不通过，给 Worker 的具体改进指令"}'
    )
    raw = run_worker(verifier_cfg, prompt, use_memory=False, fresh_session=True)
    return _normalize_verification_result(raw["result"])


def delegate_with_verification(workers: dict, worker_name: str, task: str,
                                verifier_names: list | None = None,
                                max_retries: int = 3,
                                project_name: str = "",
                                budget: Budget | None = None) -> dict:
    """【v4 P0】委托任务 + 质量验证 + 不通过自动重试。

    这是 delegate_task 的增强版本，增加了 verify-reject-retry 闭环。
    delegate_task 保持不变用于简单/探索性任务。

    Returns:
        dict with keys: worker_result, verification, attempts, final_status
    """
    if verifier_names is None:
        verifier_names = []
        for name in ("Sophia", "Nathaniel"):
            if name in workers:
                verifier_names.append(name)

    budget = budget or Budget()
    attempt_log: list[dict] = []
    current_task = task

    for attempt in range(1, max_retries + 2):  # initial + retries
        try:
            # 1. 执行 Worker
            raw = run_worker(workers[worker_name], current_task,
                             project_name=project_name,
                             fresh_session=True,
                             session_scope=f"verified_{attempt}")
            worker_result = _normalize_worker_result(raw["result"])
        except Exception as e:
            import traceback as _tb
            _tb.print_exc()
            return {
                "worker_result": None,
                "verification": None,
                "attempts": attempt_log,
                "final_status": "failed",
                "reason": f"Worker execution failed: {e}",
            }

        # 2. 预算检查
        budget_status = _check_budget(
            {"attempts": attempt, "model_calls": attempt * 3},  # rough estimate
            budget,
        )
        if not budget_status["allowed"]:
            worker_result.status = "failed"
            return {
                "worker_result": asdict(worker_result),
                "verification": None,
                "attempts": attempt_log,
                "final_status": "failed",
                "reason": budget_status["reason"],
            }

        # 3. 如无验证者，直接返回
        if not verifier_names:
            return {
                "worker_result": asdict(worker_result),
                "verification": None,
                "attempts": attempt_log,
                "final_status": worker_result.status,
            }

        # 4. 并行运行所有验证者
        verdicts: list[VerificationResult] = []
        with ThreadPoolExecutor(max_workers=len(verifier_names)) as executor:
            futures = {}
            for vname in verifier_names:
                if vname in workers:
                    future = executor.submit(
                        _run_verifier, workers[vname], worker_result, task
                    )
                    futures[future] = vname

            for future in as_completed(futures):
                vname = futures[future]
                try:
                    vresult = future.result()
                    verdicts.append(vresult)
                    attempt_log.append({
                        "attempt": attempt, "verifier": vname,
                        "verdict": vresult.verdict,
                    })
                except Exception as e:
                    attempt_log.append({
                        "attempt": attempt, "verifier": vname,
                        "verdict": "error", "error": str(e),
                    })

        merged = _merge_verdicts(verdicts)

        # 5. 根据 merged verdict 行动
        match merged.verdict:
            case "pass":
                return {
                    "worker_result": asdict(worker_result),
                    "verification": asdict(merged),
                    "attempts": attempt_log,
                    "final_status": "done",
                }
            case "needs_retry":
                if attempt <= max_retries:
                    current_task = (
                        f"{task}\n\n[验证反馈 第{attempt}轮]\n{merged.retry_instruction}\n"
                        "请根据以上反馈改进你的产出。"
                    )
                    continue
                else:
                    return {
                        "worker_result": asdict(worker_result),
                        "verification": asdict(merged),
                        "attempts": attempt_log,
                        "final_status": "failed",
                        "reason": f"超过最大重试次数 {max_retries}",
                    }
            case "reject":
                if worker_result.retryable and attempt <= max_retries:
                    current_task = (
                        f"{task}\n\n[被否决 第{attempt}轮]\n{merged.retry_instruction}\n"
                        "请完全重新思考并重新产出。"
                    )
                    continue
                else:
                    return {
                        "worker_result": asdict(worker_result),
                        "verification": asdict(merged),
                        "attempts": attempt_log,
                        "final_status": "failed",
                        "reason": "不可重试或被否决超过次数上限",
                    }
            case "needs_replan":
                return {
                    "worker_result": asdict(worker_result),
                    "verification": asdict(merged),
                    "attempts": attempt_log,
                    "final_status": "needs_replan",
                    "reason": "验证者判定需要重新规划",
                }

    return {
        "worker_result": asdict(worker_result),
        "verification": None,
        "attempts": attempt_log,
        "final_status": "failed",
    }


# ═══════════════════════════════════════════════════════════════
# v4 DAG Pipeline Engine
# ═══════════════════════════════════════════════════════════════


def _topological_sort(nodes: dict[str, dict]) -> list[str]:
    """Kahn 算法拓扑排序。检测循环，有环抛 ValueError。"""
    in_degree: dict[str, int] = {}
    adjacency: dict[str, set[str]] = {}

    for nid in nodes:
        in_degree[nid] = in_degree.get(nid, 0)
        adjacency[nid] = adjacency.get(nid, set())

    for nid, ndata in nodes.items():
        deps = ndata.get("depends_on", []) if isinstance(ndata, dict) else getattr(ndata, "depends_on", [])
        for dep in deps:
            adjacency.setdefault(dep, set()).add(nid)
            in_degree[nid] = in_degree.get(nid, 0) + 1

    queue = [nid for nid, deg in in_degree.items() if deg == 0]
    result: list[str] = []

    while queue:
        current = queue.pop(0)
        result.append(current)
        for neighbor in adjacency.get(current, set()):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(result) != len(nodes):
        raise ValueError(
            f"Cycle detected in DAG. Sorted {len(result)} of {len(nodes)} nodes. "
            f"Unsorted: {set(nodes.keys()) - set(result)}"
        )
    return result


def _find_ready_nodes(nodes: dict[str, dict]) -> list[str]:
    """找出所有就绪节点：上游全部 done，且自身为 todo。"""
    ready: list[str] = []
    for nid, ndata in nodes.items():
        status = ndata.get("status", "todo") if isinstance(ndata, dict) else getattr(ndata, "status", "todo")
        if status != "todo":
            continue
        deps = ndata.get("depends_on", []) if isinstance(ndata, dict) else getattr(ndata, "depends_on", [])
        all_deps_done = True
        for dep in deps:
            dep_status = nodes[dep].get("status", "todo") if isinstance(nodes[dep], dict) else getattr(nodes[dep], "status", "todo")
            if dep_status != "done":
                all_deps_done = False
                break
        if all_deps_done:
            ready.append(nid)
    return ready


def _propagate_blocks(nodes: dict[str, dict]) -> int:
    """迭代标记：上游 failed/blocked/needs_replan → 下游 blocked。返回标记数量。"""
    changed = 0
    for nid, ndata in nodes.items():
        status = ndata.get("status", "todo") if isinstance(ndata, dict) else getattr(ndata, "status", "todo")
        if status in ("done", "running", "retrying", "verifying"):
            continue
        deps = ndata.get("depends_on", []) if isinstance(ndata, dict) else getattr(ndata, "depends_on", [])
        for dep in deps:
            dep_node = nodes[dep]
            dep_status = dep_node.get("status", "todo") if isinstance(dep_node, dict) else getattr(dep_node, "status", "todo")
            if dep_status in ("failed", "blocked", "needs_replan"):
                if status != "blocked":
                    if isinstance(ndata, dict):
                        ndata["status"] = "blocked"
                    else:
                        ndata.status = "blocked"
                    changed += 1
            break  # one blocking dep is enough
    return changed


def _resolve_worker_for_step(step: dict, assignments: dict, workers: dict) -> str:
    """从 project_setup 的分配表匹配 Worker。

    优先级：关键词匹配 > 任务描述匹配 > 默认回退。
    """
    step_name = step.get("name", "")
    step_desc = step.get("description", "")

    # 1. 关键词匹配优先（最可靠，不会被跨任务污染）
    combined = step_name + step_desc
    keyword_map = {
        "Sophia": ("审查", "review", "检查", "审计"),
        "Alex": ("修复", "实现", "开发", "编写", "code", "develop", "fix"),
        "Nathaniel": ("测试", "test", "验证", "validate", "pytest"),
        "Elena": ("文档", "document", "readme", "说明"),
        "Marcus": ("部署", "deploy", "ci", "cd", "运维", "环境"),
    }
    for wname, keywords in keyword_map.items():
        for kw in keywords:
            if kw.lower() in combined.lower():
                if wname in workers:
                    return wname

    # 2. 精确匹配：step_name 出现在 worker task 描述中
    for wname, winfo in assignments.items():
        worker_tasks = winfo.get("tasks", [])
        for t in worker_tasks:
            if step_name in t:
                if wname in workers:
                    return wname

    # 3. 模糊匹配：step_desc 前 40 个字符匹配 worker task
    desc_prefix = step_desc[:40]
    for wname, winfo in assignments.items():
        worker_tasks = winfo.get("tasks", [])
        for t in worker_tasks:
            if desc_prefix in t:
                if wname in workers:
                    return wname

    # 最终回退：有写权限的 Alex
    if "Alex" in workers:
        return "Alex"
    return next(iter(workers.keys()), "")


def _build_node_task(ndata: dict, state: dict) -> str:
    """根据节点信息构建 Worker 任务描述。v4.2: 预加载项目文件内容。"""
    name = ndata.get("name", "")
    desc = ndata.get("description", "")
    deps = ndata.get("depends_on", [])

    # 从 project_description 提取项目目录并预读关键文件
    project_desc = state.get("project_description", "")
    project_dir_hint = ""
    file_context = ""
    if "C:\\" in project_desc or "/" in project_desc:
        import re as _re2
        paths = _re2.findall(r'[A-Z]:\\[^\s\n]+', project_desc)
        if paths:
            project_dir = paths[0]
            project_dir_hint = (
                f"\n⚠️ 项目目录：{project_dir}"
                f"\n所有文件操作使用绝对路径。"
            )
            # v4.2: 预读 main.py 和 test_main.py，直接提供给 Worker
            for fname in ("main.py", "test_main.py"):
                fpath = os.path.join(project_dir, fname)
                if os.path.isfile(fpath):
                    try:
                        with open(fpath, "r", encoding="utf-8") as _pf:
                            content = _pf.read()
                        file_context += f"\n\n── {fname} 内容 ──\n{content}\n── {fname} 结束 ──"
                    except Exception:
                        pass

    dep_context = ""
    if deps:
        dep_context = (
            f"\n依赖的上游节点（已完成）: {', '.join(deps)}\n"
            "上游产出已保存在项目目录中，如需确认请用 read_file。"
        )

    review_plan = state.get("review_plan", {})
    review_hint = ""
    if review_plan:
        review_hint = (
            f"\n注意：你的产出将由 {review_plan.get('reviewer', 'Sophia')} 审查、"
            f"{review_plan.get('validator', 'Nathaniel')} 验证。请确保质量。"
        )

    return (
        f"[项目节点: {name}]\n"
        f"{desc}\n"
        f"{project_dir_hint}"
        f"{file_context}"
        f"{dep_context}"
        f"{review_hint}"
        f"\n\n⚠️ 重要：以上已提供了所有需要的文件内容，请直接完成任务，不要再用 read_file 读取！"
        f"\n完成后用 write_file 保存修改，并用 JSON 格式总结："
        f'{{"status": "success|partial|failed|needs_review", '
        f'"summary": "...", '
        f'"artifacts": [{{"path": "...", "type": "...", "summary": "..."}}], '
        f'"issues": [], "retryable": true, "confidence": 0.8}}'
    )


def _summarize_run(nodes: dict[str, dict], status: str = "") -> dict:
    """生成可审计的 pipeline 执行摘要（v4.1 增强版）。

    每个节点包含：status, worker, attempts, verifier verdict,
    score, blocked_by, artifacts, error.
    """
    node_summaries = {}
    counts = {
        "todo": 0, "ready": 0, "running": 0, "verifying": 0,
        "done": 0, "retrying": 0, "failed": 0, "blocked": 0,
        "needs_replan": 0,
    }

    for nid, ndata in nodes.items():
        s = ndata.get("status", "todo") if isinstance(ndata, dict) else getattr(ndata, "status", "todo")
        if s in counts:
            counts[s] += 1

        # blocked_by: find upstream nodes that are blocking this one
        deps = ndata.get("depends_on", []) if isinstance(ndata, dict) else getattr(ndata, "depends_on", [])
        blocked_by = []
        for dep in deps:
            if dep in nodes:
                dep_s = nodes[dep].get("status", "todo") if isinstance(nodes[dep], dict) else getattr(nodes[dep], "status", "todo")
                if dep_s in ("failed", "blocked", "needs_replan"):
                    blocked_by.append(dep)

        # verification info
        verification = ndata.get("verification") if isinstance(ndata, dict) else getattr(ndata, "verification", None)
        verifier_verdict = verification.get("verdict", "") if isinstance(verification, dict) else ""
        verifier_score = verification.get("score", 0) if isinstance(verification, dict) else 0

        node_summaries[nid] = {
            "name": ndata.get("name", nid) if isinstance(ndata, dict) else getattr(ndata, "name", nid),
            "status": s,
            "worker": ndata.get("assigned_worker", "") if isinstance(ndata, dict) else getattr(ndata, "assigned_worker", ""),
            "attempts": ndata.get("attempts", 0) if isinstance(ndata, dict) else getattr(ndata, "attempts", 0),
            "verifier_verdict": verifier_verdict,
            "score": verifier_score,
            "blocked_by": blocked_by,
            "artifacts": ndata.get("artifacts", []) if isinstance(ndata, dict) else getattr(ndata, "artifacts", []),
            "error": ndata.get("error", "") if isinstance(ndata, dict) else getattr(ndata, "error", ""),
        }

    # 确定 overall status
    if status:
        overall = status
    elif counts["failed"] > 0 or counts["blocked"] > 0 or counts["needs_replan"] > 0:
        overall = "failed" if counts["failed"] > 0 else "incomplete"
    elif counts["done"] == len(nodes):
        overall = "completed"
    elif counts["running"] > 0 or counts["todo"] > 0:
        overall = "in_progress"
    else:
        overall = "unknown"

    # 下一步建议
    next_actions: list[str] = []
    if counts["needs_replan"] > 0:
        next_actions.append(f"{counts['needs_replan']} node(s) need replan — run request_replan")
    if counts["failed"] > 0:
        next_actions.append(f"{counts['failed']} node(s) failed — check error details and retry or replan")
    if counts["blocked"] > 0:
        next_actions.append(f"{counts['blocked']} node(s) blocked — resolve upstream failures first")
    if overall == "completed":
        next_actions.append("All nodes completed successfully. Run is done.")
    elif overall == "in_progress":
        next_actions.append("Run is still in progress. Use --resume to continue.")

    return {
        "overall_status": overall,
        "counts": counts,
        "nodes": node_summaries,
        "next_actions": next_actions,
    }


def build_workflow_run_report(workflow_run: dict | WorkflowRun) -> str:
    """生成标准化的 WorkflowRun 人类可读报告（v4.1）。

    Accepts both raw dict and WorkflowRun dataclass.
    """
    nodes = workflow_run.nodes if isinstance(workflow_run, WorkflowRun) else workflow_run.get("nodes", {})
    status = workflow_run.status if isinstance(workflow_run, WorkflowRun) else workflow_run.get("status", "")
    run_id = workflow_run.run_id if isinstance(workflow_run, WorkflowRun) else workflow_run.get("run_id", "")
    project = workflow_run.project_name if isinstance(workflow_run, WorkflowRun) else workflow_run.get("project_name", "")
    created = workflow_run.created_at if isinstance(workflow_run, WorkflowRun) else workflow_run.get("created_at", "")
    updated = workflow_run.updated_at if isinstance(workflow_run, WorkflowRun) else workflow_run.get("updated_at", "")

    summary = _summarize_run(nodes, status)

    lines = [
        "=" * 65,
        f"  Workflow Run Report",
        "=" * 65,
        f"  Run ID:      {run_id}",
        f"  Project:     {project}",
        f"  Status:      {status}",
        f"  Started:     {created}",
        f"  Updated:     {updated}",
        f"  Total Nodes: {len(nodes)}",
        "  ── Counts ──",
    ]
    for state, count in summary["counts"].items():
        if count > 0:
            lines.append(f"    {state}: {count}")

    lines.append("  ── Nodes ──")
    for nid, ndata in summary["nodes"].items():
        marker = {"done": "✅", "failed": "❌", "blocked": "🚫", "todo": "⏳",
                  "running": "🔄", "retrying": "🔁", "needs_replan": "🔧"}.get(ndata["status"], "❓")
        lines.append(
            f"    {marker} {nid}: {ndata['status']} "
            f"(worker={ndata['worker']}, attempts={ndata['attempts']}, "
            f"verdict={ndata['verifier_verdict'] or 'N/A'}, score={ndata['score']})"
        )
        if ndata.get("blocked_by"):
            lines.append(f"       blocked_by: {ndata['blocked_by']}")
        if ndata.get("error"):
            error_text = str(ndata["error"])[:120]
            lines.append(f"       error: {error_text}")
        if ndata.get("artifacts"):
            lines.append(f"       artifacts: {len(ndata['artifacts'])} file(s)")

    lines.append("  ── Next Actions ──")
    for action in summary.get("next_actions", []):
        lines.append(f"    → {action}")
    lines.append("=" * 65)

    return "\n".join(lines)


def run_project_pipeline(project_name: str, workers: dict,
                         auto_resume: bool = True) -> dict:
    """【v4 P1】DAG 感知的项目 Pipeline 执行引擎。

    - 加载 project_setup 生成的 pipeline
    - 拓扑排序 + 依赖检查
    - 每轮找出所有 ready 节点，ThreadPoolExecutor 并行执行
    - 每个节点走 delegate_with_verification
    - 失败自动阻断下游
    - 每节点完成后持久化（中断可 recover）
    - 安全上限：20 轮
    """
    state = load_project_state(project_name)
    steps = state.get("pipeline_steps", [])
    if not steps:
        return {"error": "没有 Pipeline 步骤。请先运行 project_setup。"}

    # 加载或创建 WorkflowRun
    run = load_workflow_run(project_name)
    if run is None:
        run_id = f"{project_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        run = WorkflowRun(
            run_id=run_id,
            project_name=project_name,
            status="pending",
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

    # auto-resume: 重置残留 running 节点
    if run.status == "running" and auto_resume:
        for nid, ndata in run.nodes.items():
            if isinstance(ndata, dict) and ndata.get("status") == "running":
                ndata["status"] = "todo"

    # 从 pipeline steps 初始化 nodes（跳过已有的）
    assignments = state.get("assignments", {})
    for step in steps:
        node_id = step["name"]
        if node_id not in run.nodes:
            run.nodes[node_id] = asdict(TaskNode(
                id=node_id,
                name=node_id,
                description=step.get("description", ""),
                depends_on=step.get("depends_on", []),
                assigned_worker=_resolve_worker_for_step(step, assignments, workers),
                status=step.get("status", "todo"),
                max_retries=3,
                created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ))

    # 拓扑验证（告警不回阻断执行）
    try:
        _topological_sort(run.nodes)
    except ValueError as e:
        run.execution_log.append({"level": "warn", "msg": str(e)})

    run.status = "running"
    _save_workflow_run(run)

    MAX_ROUNDS = 20

    for round_num in range(MAX_ROUNDS):
        _propagate_blocks(run.nodes)

        ready_ids = _find_ready_nodes(run.nodes)

        if not ready_ids:
            # 检查是否还有未完成的
            pending = sum(
                1 for n in run.nodes.values()
                if n.get("status", "todo") not in ("done", "failed", "blocked", "needs_replan")
            )
            if pending == 0:
                all_done = all(n["status"] == "done" for n in run.nodes.values())
                run.status = "completed" if all_done else "failed"
            _save_workflow_run(run)
            break

        print(f"\n  [Pipeline R{round_num + 1}] 就绪节点: {len(ready_ids)} → {ready_ids}")

        # 并行执行就绪节点
        with ThreadPoolExecutor(max_workers=min(len(ready_ids), 6)) as executor:
            futures = {}
            for nid in ready_ids:
                ndata = run.nodes[nid]
                ndata["status"] = "running"
                _save_workflow_run(run)

                node_task = _build_node_task(ndata, state)
                future = executor.submit(
                    delegate_with_verification,
                    workers, ndata["assigned_worker"], node_task,
                    verifier_names=["Sophia", "Nathaniel"],
                    max_retries=ndata.get("max_retries", 3),
                    project_name=project_name,
                )
                futures[future] = nid

            for future in as_completed(futures):
                nid = futures[future]
                ndata = run.nodes[nid]
                try:
                    result = future.result()
                    fs = result.get("final_status", "failed")
                    ndata["status"] = {
                        "done": "done", "success": "done",
                    }.get(fs, fs)  # pass through "failed", "needs_replan", etc.
                    ndata["verification"] = result.get("verification")
                    ndata["attempts"] = len(result.get("attempts", []))

                    update_project_step(project_name, ndata["name"], ndata["status"],
                                        worker=ndata["assigned_worker"])
                    run.execution_log.append({
                        "node": nid, "round": round_num + 1,
                        "status": ndata["status"],
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    })
                except Exception as e:
                    ndata["status"] = "failed"
                    ndata["error"] = str(e)[:500]
                    run.execution_log.append({
                        "node": nid, "round": round_num + 1,
                        "status": "failed", "error": str(e)[:200],
                    })

                _save_workflow_run(run)

    _propagate_blocks(run.nodes)
    _save_workflow_run(run)

    report = build_workflow_run_report(run)
    print(report)
    return _summarize_run(run.nodes, run.status)


# ═══════════════════════════════════════════════════════════════
# v4 收敛模式
# ═══════════════════════════════════════════════════════════════


def run_convergence_loop(task: str, worker_name: str, workers: dict,
                          verifier_names: list | None = None,
                          stable_rounds: int = 2, max_rounds: int = 5,
                          budget: Budget | None = None) -> dict:
    """【v4 P1】迭代执行直到收敛。

    停止条件（任一满足即停）：
    1. 连续 stable_rounds 轮无新增 blocking issues
    2. 达到 max_rounds
    3. 预算耗尽
    4. final_status 为 failed 或 needs_replan
    """
    budget = budget or Budget()
    verifier_names = verifier_names or ["Sophia", "Nathaniel"]

    round_log: list[dict] = []
    stable_count = 0
    current_task = task
    final_result = None

    for r in range(max_rounds):
        result = delegate_with_verification(
            workers, worker_name, current_task,
            verifier_names=verifier_names, max_retries=2, budget=budget,
        )

        verification = result.get("verification", {}) or {}
        blocking_count = len(verification.get("blocking_issues", []))

        round_log.append({
            "round": r + 1,
            "status": result["final_status"],
            "blocking_issues": blocking_count,
        })

        if result["final_status"] in ("failed", "needs_replan"):
            stable_count = 0
            final_result = result
            break

        if blocking_count == 0:
            stable_count += 1
            final_result = result
            if stable_count >= stable_rounds:
                break
        else:
            stable_count = 0
            retry_instr = verification.get("retry_instruction", "继续改进。")
            current_task = (
                f"{task}\n\n[收敛反馈 R{r + 1}]\n{retry_instr}"
            )

    return {
        "rounds": round_log,
        "final_result": final_result,
        "stable_rounds_achieved": stable_count,
    }


# ═══════════════════════════════════════════════════════════════
# v4 WorkflowRun 持久化 + Budget/Replan
# ═══════════════════════════════════════════════════════════════


def save_workflow_run(run: WorkflowRun):
    """将 WorkflowRun 序列化到 project_states/{project_name}_run.json。"""
    _save_workflow_run(run)


def _save_workflow_run(run: WorkflowRun):
    """内部持久化：序列化 WorkflowRun 到 JSON。"""
    ensure_project_state_dir()
    filepath = os.path.join(PROJECT_STATE_DIR, f"{run.project_name}_run.json")
    data = {
        "run_id": run.run_id,
        "project_name": run.project_name,
        "status": run.status,
        "nodes": run.nodes,
        "budget": asdict(run.budget),
        "execution_log": run.execution_log,
        "created_at": run.created_at,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "version": run.version,
    }
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_workflow_run(project_name: str) -> WorkflowRun | None:
    """从 project_states/{project_name}_run.json 反序列化 WorkflowRun。"""
    filepath = os.path.join(PROJECT_STATE_DIR, f"{project_name}_run.json")
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return WorkflowRun(
            run_id=data.get("run_id", ""),
            project_name=data.get("project_name", project_name),
            status=data.get("status", "pending"),
            nodes=data.get("nodes", {}),
            budget=Budget(**data.get("budget", {})),
            execution_log=data.get("execution_log", []),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            version=data.get("version", 4),
        )
    except (json.JSONDecodeError, FileNotFoundError):
        return None


def resume_workflow_run(project_name: str, workers: dict) -> dict:
    """从保存的状态恢复中断的工作流。"""
    run = load_workflow_run(project_name)
    if run is None:
        return {"error": f"未找到项目 '{project_name}' 的工作流记录。"}
    if run.status == "completed":
        return {"status": "already_completed", "message": "该工作流已完成。"}

    # 重置残留 running 节点
    for nid, ndata in run.nodes.items():
        if isinstance(ndata, dict) and ndata.get("status") == "running":
            ndata["status"] = "todo"
    _save_workflow_run(run)

    return run_project_pipeline(project_name, workers, auto_resume=False)


def request_replan(project_name: str, failed_node_id: str, workers: dict) -> dict:
    """【v4 P2】当节点 needs_replan 时，请求 LLM 重新规划 pipeline。"""
    state = load_project_state(project_name)
    run = load_workflow_run(project_name)

    failed_node = {}
    if run and failed_node_id in run.nodes:
        failed_node = run.nodes[failed_node_id]

    prompt = (
        f"[需要重新规划]\n"
        f"项目: {project_name}\n"
        f"失败节点: {failed_node_id}\n"
        f"错误: {failed_node.get('error', 'unknown')}\n"
        f"当前 pipeline 状态: {json.dumps({nid: n.get('status') for nid, n in (run.nodes.items() if run else {})}, ensure_ascii=False)}\n\n"
        f"请重新分析并输出新的 pipeline JSON。已完成(done)的节点保持不变。"
    )

    return {
        "replan_request": project_setup(workers, prompt),
        "failed_node": failed_node_id,
    }


def request_human_approval(node_id: str, proposal: str) -> dict:
    """【v4 P2】人工审批门。CLI 模式下等待输入 approve/reject/replan。"""
    with print_lock:
        print(f"\n  {'!' * 3} 需要人工审批: {node_id}")
        print(f"  提案: {proposal}")
        print(f"  请输入 approve / reject / replan: ", end="")
    choice = input().strip().lower()
    return {"node_id": node_id, "decision": choice}


# ── Manager 工具 ──────────────────────────────────────────
def build_manager_tools(workers: dict) -> list:
    """构建 delegate_task + clear_memory 工具。"""
    worker_names = list(workers.keys())
    worker_descriptions = [
        f"「{w['name']}」- {w['role']}：{w['description']}（工具: {', '.join(w['tool_names'])}）"
        for w in workers.values()
    ]

    return [
        {
            "name": "delegate_task",
            "description": (
                "将任务指派给一名员工独立执行。你可以同时指派多名员工并行工作。\n"
                "员工列表：\n"
                + "\n".join(worker_descriptions)
                + "\n\n根据任务需求选择合适的员工。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "worker_name": {
                        "type": "string",
                        "enum": worker_names,
                        "description": "员工名称",
                    },
                    "task": {
                        "type": "string",
                        "description": "任务描述，越具体越好。",
                    },
                },
                "required": ["worker_name", "task"],
            },
        },
        {
            "name": "clear_worker_memory",
            "description": "清除指定员工的对话记忆。当员工开始全新任务、或者上下文混乱时使用。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "worker_name": {
                        "type": "string",
                        "enum": worker_names,
                        "description": "要清除记忆的员工名称",
                    },
                },
                "required": ["worker_name"],
            },
        },
        {
            "name": "relay_to_worker",
            "description": (
                "将一段信息传递给指定员工，注入到该员工的对话上下文中。"
                "用于员工之间的间接协作——比如把亚历克斯的输出告诉索菲亚让她审查时更有上下文。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "worker_name": {
                        "type": "string",
                        "enum": worker_names,
                        "description": "接收信息的员工名称",
                    },
                    "message": {
                        "type": "string",
                        "description": "要传递的信息，会被注入到该员工的对话上下文中",
                    },
                },
                "required": ["worker_name", "message"],
            },
        },
        {
            "name": "evaluate_result",
            "description": (
                "对员工的交付结果进行结构化评分。用于质量管控和事后复盘。"
                "评分后结果会保存到磁盘。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "worker_name": {
                        "type": "string",
                        "enum": worker_names,
                        "description": "被评分的员工名称",
                    },
                    "correctness": {
                        "type": "integer",
                        "description": "正确性评分 1-5（结果是否正确、无 bug）",
                    },
                    "completeness": {
                        "type": "integer",
                        "description": "完整性评分 1-5（是否覆盖所有需求）",
                    },
                    "quality": {
                        "type": "integer",
                        "description": "代码/文档质量评分 1-5（可读性、规范性、设计）",
                    },
                    "comment": {
                        "type": "string",
                        "description": "简短评语",
                    },
                },
                "required": ["worker_name", "correctness", "completeness", "quality"],
            },
        },
        {
            "name": "create_task",
            "description": "在任务看板中创建一个任务项，用于追踪工作进度。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "任务描述"},
                    "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"], "description": "优先级"},
                    "assigned_worker": {"type": "string", "enum": worker_names, "description": "指派给哪位员工（可选）"},
                },
                "required": ["description"],
            },
        },
        {
            "name": "list_tasks",
            "description": "查看任务看板，了解所有任务的进度状态。可选按状态过滤。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "status_filter": {"type": "string", "enum": ["", "todo", "in_progress", "done", "failed"], "description": "按状态过滤，留空则展示全部"},
                },
            },
        },
        {
            "name": "update_task",
            "description": "更新任务状态或指派人。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "任务 ID"},
                    "status": {"type": "string", "enum": ["todo", "in_progress", "done", "failed"], "description": "新状态"},
                    "assigned_worker": {"type": "string", "enum": worker_names, "description": "新指派人（可选）"},
                },
                "required": ["task_id"],
            },
        },
        {
            "name": "record_knowledge",
            "description": "将经验、决策或最佳实践记录到团队共享知识库。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "知识主题"},
                    "content": {"type": "string", "description": "知识内容"},
                    "author": {"type": "string", "description": "贡献者（可选）"},
                },
                "required": ["topic", "content"],
            },
        },
        {
            "name": "roundtable_discuss",
            "description": (
                "发起一次圆桌讨论：邀请多位员工就一个话题各自发表意见，"
                "然后让他们看到彼此的意见后再次补充，最后汇总共识。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "讨论话题"},
                    "participants": {
                        "type": "array",
                        "items": {"type": "string", "enum": worker_names},
                        "description": "参与讨论的员工名单（2-4人）",
                    },
                },
                "required": ["topic", "participants"],
            },
        },
        {
            "name": "get_dashboard",
            "description": "查看团队状态面板：任务概况、Worker 用量、成功率、知识库状态。",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "consult_deputy",
            "description": (
                "咨询副经理维克托的意见。遇到重大决策、不确定的指派、或者需要第二意见时使用。"
                "副经理会独立分析并给出建议，可能会指出你忽略的问题。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "要咨询的问题。描述清楚背景、你已经做的决策、以及你担心的地方。",
                    },
                },
                "required": ["question"],
            },
        },
        {
            "name": "request_decision_review",
            "description": (
                "请求副经理复核你已经做的决策（如任务指派、评分、审核结论）。"
                "副经理会检查是否有失当之处，同意或提出异议。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "decision_summary": {
                        "type": "string",
                        "description": "已做决策的摘要：做了什么决定、为什么这么决定、涉及哪些员工。",
                    },
                },
                "required": ["decision_summary"],
            },
        },
        {
            "name": "project_setup",
            "description": (
                "【项目启动】接到新项目后，分析需求并为每个成员动态分配本项目的临时领域。\n"
                "这会生成 project_state.json，后续所有任务都基于此状态推进。\n"
                "仅在项目启动时使用一次。分配后通过 delegate_task 指派具体工作。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "project_description": {
                        "type": "string",
                        "description": "项目需求描述，越详细越好。包括目标、数据源、预期产出等。",
                    },
                },
                "required": ["project_description"],
            },
        },
        {
            "name": "update_project_step",
            "description": (
                "更新项目 pipeline 中某个步骤的状态。"
                "Worker 完成任务后，用此工具标记步骤进度。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "project_name": {
                        "type": "string",
                        "description": "项目名称（与 project_setup 中的一致）",
                    },
                    "step_name": {
                        "type": "string",
                        "description": "步骤名称",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["todo", "in_progress", "done", "failed"],
                        "description": "新状态",
                    },
                    "output_path": {
                        "type": "string",
                        "description": "产出文件路径（可选）",
                    },
                    "worker": {
                        "type": "string",
                        "description": "执行的 Worker 名称（可选）",
                    },
                },
                "required": ["project_name", "step_name", "status"],
            },
        },
        # ── v4 新工具定义 ──
        {
            "name": "delegate_with_verification",
            "description": (
                "【v4】将任务指派给员工，然后自动让 Sophia（审查）和 Nathaniel（验证）复核结果。"
                "不通过会自动重试。比 delegate_task 多了验证闭环。"
                "用于需要质量保证的关键任务。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "worker_name": {"type": "string", "enum": worker_names},
                    "task": {"type": "string", "description": "任务描述"},
                    "verifier_names": {
                        "type": "array",
                        "items": {"type": "string", "enum": worker_names},
                        "description": "验证者列表，默认 Sophia + Nathaniel",
                    },
                    "max_retries": {"type": "integer", "description": "最大重试次数，默认 3"},
                },
                "required": ["worker_name", "task"],
            },
        },
        {
            "name": "run_project_pipeline",
            "description": (
                "【v4】按 DAG 拓扑序执行项目 pipeline：找 ready 节点、并行执行、验证、失败阻断下游。"
                "中断后可恢复。是 v4 的核心执行引擎。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "project_name": {"type": "string", "description": "项目名称（与 project_setup 一致）"},
                },
                "required": ["project_name"],
            },
        },
        {
            "name": "run_convergence_loop",
            "description": (
                "【v4】迭代执行直到连续 N 轮无新问题。适用于持续审查、持续测试、持续改进文档。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "迭代任务描述"},
                    "worker_name": {"type": "string", "enum": worker_names},
                    "stable_rounds": {"type": "integer", "description": "需要连续多少轮无新问题才算收敛，默认 2"},
                    "max_rounds": {"type": "integer", "description": "最多执行多少轮，默认 5"},
                },
                "required": ["task", "worker_name"],
            },
        },
        {
            "name": "show_workflow_status",
            "description": "【v4】显示当前工作流的节点状态、执行日志和预算消耗。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "project_name": {"type": "string", "description": "项目名称"},
                },
                "required": ["project_name"],
            },
        },
        {
            "name": "request_replan",
            "description": "【v4】当 pipeline 节点标记为 needs_replan 时，请求 AI 重新生成项目规划。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "project_name": {"type": "string"},
                    "failed_node_id": {"type": "string"},
                },
                "required": ["project_name", "failed_node_id"],
            },
        },
    ]


def delegate_task(workers: dict, worker_name: str, task: str) -> str:
    """管理者调用此函数来指派 Worker 执行任务。"""
    if worker_name not in workers:
        available = ", ".join(workers.keys())
        return f"错误：没有名为「{worker_name}」的员工。可选员工: {available}"

    cfg = workers[worker_name]
    with print_lock:
        print(f"\n  >>> 指派给 Worker-{worker_name}（{cfg['role']}）: {task[:80]}...")
        print("  " + "-" * 40)

    result = run_worker(cfg, task)

    with print_lock:
        print("  " + "-" * 40)
        print(f"  <<< Worker-{worker_name} 完成任务\n")

    return f"Worker-{worker_name}（{cfg['role']}）执行结果:\n{result['result']}"


def clear_worker_memory(workers: dict, worker_name: str) -> str:
    """清除指定 Worker 的对话记忆。"""
    if worker_name not in workers:
        return f"错误：没有名为「{worker_name}」的员工。"

    cfg = workers[worker_name]
    session_key = f"{worker_name}|{cfg['role']}"
    if session_key in worker_sessions:
        del worker_sessions[session_key]
        return f"已清除 Worker-{worker_name} 的对话记忆。"
    return f"Worker-{worker_name} 当前没有对话记忆。"


def relay_to_worker(workers: dict, worker_name: str, message: str) -> str:
    """将信息注入到指定 Worker 的对话上下文（用于 Worker 间间接通信）。"""
    if worker_name not in workers:
        return f"错误：没有名为「{worker_name}」的员工。"

    cfg = workers[worker_name]
    session_key = f"{worker_name}|{cfg['role']}"
    if session_key not in worker_sessions:
        worker_sessions[session_key] = []

    worker_sessions[session_key].append({
        "role": "user",
        "content": f"[来自 Manager 的上下文信息]\n{message}",
    })
    save_sessions()
    return f"已向 Worker-{worker_name} 传递上下文信息。"


def evaluate_result(workers: dict, worker_name: str,
                    correctness: int, completeness: int, quality: int,
                    comment: str = "", verdict: str = "") -> str:
    """对 Worker 交付结果进行结构化评分并持久化。

    v4 新增可选参数 verdict（"pass"|"needs_retry"|"reject"），向后兼容。
    """
    if worker_name not in workers:
        return f"错误：没有名为「{worker_name}」的员工。"

    score = {
        "worker_name": worker_name,
        "correctness": correctness,
        "completeness": completeness,
        "quality": quality,
        "total": correctness + completeness + quality,
        "comment": comment,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if verdict:
        score["verdict"] = verdict

    # 加载历史评分
    all_scores = []
    if os.path.exists(SCORE_FILE):
        try:
            with open(SCORE_FILE, "r", encoding="utf-8") as f:
                all_scores = json.load(f)
        except Exception:
            pass

    all_scores.append(score)

    with open(SCORE_FILE, "w", encoding="utf-8") as f:
        json.dump(all_scores, f, ensure_ascii=False, indent=2)

    avg = score["total"] / 3
    return (
        f"评分已保存。Worker-{worker_name} 本次评分：\n"
        f"  正确性: {correctness}/5 | 完整性: {completeness}/5 | 质量: {quality}/5\n"
        f"  综合: {avg:.1f}/5"
        + (f"\n  评语: {comment}" if comment else "")
    )


def roundtable_discuss(workers: dict, topic: str, participants: list) -> str:
    """发起圆桌讨论：多 Worker 并行发言 + 交叉回应 + 汇总共识。"""
    if len(participants) < 2:
        return "圆桌讨论至少需要 2 名参与者。"

    with print_lock:
        print(f"\n  🏛️ 圆桌讨论: {topic}")
        print(f"  参与者: {', '.join(participants)}")
        print("  " + "=" * 50)

    # 第一轮：并行提问
    round1_results = {}
    round1_tasks = {}
    with ThreadPoolExecutor(max_workers=len(participants)) as executor:
        for name in participants:
            cfg = workers[name]
            prompt = (
                f"[圆桌讨论 - 第 1 轮]\n话题: {topic}\n"
                "请发表你的专业意见，从你的职能角度分析问题，给出具体建议。"
            )
            future = executor.submit(run_worker, cfg, prompt, use_memory=True)
            round1_tasks[future] = name

        for future in as_completed(round1_tasks):
            name = round1_tasks[future]
            try:
                result = future.result()
                round1_results[name] = result["result"]
            except Exception as e:
                round1_results[name] = f"发言失败: {e}"

    with print_lock:
        print("\n  --- 第 1 轮发言汇总 ---")
        for name, opinion in round1_results.items():
            print(f"  [{name}]: {opinion[:200]}...")
        print("  " + "-" * 40)

    # 第二轮：交叉回应
    round2_results = {}
    round2_tasks = {}
    others_summary = "\n".join(f"[{n}]: {o[:300]}" for n, o in round1_results.items())

    with ThreadPoolExecutor(max_workers=len(participants)) as executor:
        for name in participants:
            cfg = workers[name]
            prompt = (
                f"[圆桌讨论 - 第 2 轮]\n话题: {topic}\n\n"
                f"以下是所有参与者的第一轮意见:\n{others_summary}\n\n"
                "请阅读其他人的意见后，补充或修正你的观点。如果同意别人的意见请明确表示，"
                "如果有不同看法请具体说明理由。"
            )
            future = executor.submit(run_worker, cfg, prompt, use_memory=True)
            round2_tasks[future] = name

        for future in as_completed(round2_tasks):
            name = round2_tasks[future]
            try:
                result = future.result()
                round2_results[name] = result["result"]
            except Exception as e:
                round2_results[name] = f"发言失败: {e}"

    with print_lock:
        print("\n  --- 第 2 轮补充意见 ---")
        for name, opinion in round2_results.items():
            print(f"  [{name}]: {opinion[:200]}...")
        print("  " + "=" * 50)

    # 汇总
    summary_parts = []
    for name in participants:
        summary_parts.append(f"### {name}\n第1轮: {round1_results.get(name, '')[:300]}\n第2轮: {round2_results.get(name, '')[:300]}")
    return "圆桌讨论完成。\n" + "\n\n".join(summary_parts)


def consult_deputy(question: str) -> str:
    """向副经理咨询意见。"""
    if not _deputy_config:
        return "错误：未配置副经理。请在 workers.json 中添加 is_deputy: true 的员工。"

    # 需要延迟引用 MANAGER_TOOLS
    manager_tools = build_manager_tools(_workers_config)

    with print_lock:
        print(f"\n  🎩 咨询副经理「{_deputy_config['name']}」...")
        print("  " + "-" * 40)

    result = run_deputy(question, manager_tools)

    with print_lock:
        print("  " + "-" * 40)
        print(f"  🎩 副经理已回复\n")

    return f"副经理「{_deputy_config['name']}」的意见:\n{result['result']}"


def request_decision_review(decision_summary: str) -> str:
    """请求副经理复核决策。"""
    if not _deputy_config:
        return "错误：未配置副经理。"

    prompt = (
        f"[决策复核请求]\n"
        f"正经理做了以下决策，请你复核:\n{decision_summary}\n\n"
        "请逐一检查：\n"
        "1. 指派给的人选是否合适（职能匹配吗？权限够吗？）\n"
        "2. 优先级是否合理\n"
        "3. 是否遗漏了关键步骤（比如没先审查就写代码）\n"
        "4. 评分是否公允（如果涉及评分）\n\n"
        "如果发现任何问题，明确指出来并提出改进建议。如果都合理，也要明确表示同意。"
    )
    return consult_deputy(prompt)


def execute_manager_tool(name: str, args: dict, workers: dict) -> str:
    if name == "delegate_task":
        return delegate_task(workers, args["worker_name"], args["task"])
    if name == "clear_worker_memory":
        return clear_worker_memory(workers, args["worker_name"])
    if name == "relay_to_worker":
        return relay_to_worker(workers, args["worker_name"], args["message"])
    if name == "evaluate_result":
        return evaluate_result(
            workers, args["worker_name"],
            args["correctness"], args["completeness"], args["quality"],
            args.get("comment", ""),
        )
    if name == "create_task":
        return create_task(
            args["description"],
            args.get("priority", "medium"),
            args.get("assigned_worker", ""),
        )
    if name == "list_tasks":
        return list_tasks(args.get("status_filter", ""))
    if name == "update_task":
        return update_task(
            args["task_id"],
            args.get("status", ""),
            args.get("assigned_worker", ""),
        )
    if name == "record_knowledge":
        return record_knowledge(
            args["topic"], args["content"],
            args.get("author", ""),
        )
    if name == "roundtable_discuss":
        return roundtable_discuss(workers, args["topic"], args["participants"])
    if name == "get_dashboard":
        return get_dashboard()
    if name == "consult_deputy":
        return consult_deputy(args["question"])
    if name == "request_decision_review":
        return request_decision_review(args["decision_summary"])
    if name == "project_setup":
        return project_setup(workers, args["project_description"])
    if name == "update_project_step":
        return update_project_step(
            args["project_name"], args["step_name"], args["status"],
            args.get("output_path", ""), args.get("worker", ""),
        ) or f"项目步骤已更新: {args['project_name']}/{args['step_name']} → {args['status']}"

    # ── v4 新工具 ──
    if name == "delegate_with_verification":
        result = delegate_with_verification(
            workers, args["worker_name"], args["task"],
            verifier_names=args.get("verifier_names"),
            max_retries=args.get("max_retries", 3),
        )
        return json.dumps(result, ensure_ascii=False, indent=2)

    if name == "run_project_pipeline":
        result = run_project_pipeline(args["project_name"], workers)
        return json.dumps(result, ensure_ascii=False, indent=2)

    if name == "run_convergence_loop":
        result = run_convergence_loop(
            args["task"], args["worker_name"], workers,
            stable_rounds=args.get("stable_rounds", 2),
            max_rounds=args.get("max_rounds", 5),
        )
        return json.dumps(result, ensure_ascii=False, indent=2)

    if name == "show_workflow_status":
        run = load_workflow_run(args["project_name"])
        if run is None:
            return f"未找到项目 '{args['project_name']}' 的工作流记录。"
        return json.dumps(_summarize_run(run.nodes, run.status), ensure_ascii=False, indent=2)

    if name == "request_replan":
        return json.dumps(
            request_replan(args["project_name"], args["failed_node_id"], workers),
            ensure_ascii=False, indent=2,
        )

    return f"未知工具: {name}"


# ── Manager 对话循环 ───────────────────────────────────────
def main():
    workers = load_workers()
    if not workers:
        print("无法加载员工配置，系统退出。请在 workers.json 中配置员工。")
        return

    load_sessions()
    if worker_sessions:
        names = {k.split("|")[0] for k in worker_sessions}
        print(f"  [已恢复 {len(worker_sessions)} 个 Worker 会话: {', '.join(names)}]")

    MANAGER_TOOLS = build_manager_tools(workers)

    roster = "\n".join(
        f"  - 「{w['name']}」{w['role']}：{w['description']}（工具: {', '.join(w['tool_names'])}）"
        for w in workers.values()
    )

    print("=" * 60)
    print("  Multi-Agent 层级管理系统 v4 — Agentic Workflow Runtime")
    print("  老板（你）→ 管理者（AI,自适应模型）→ 员工（AI Worker,自适应模型）")
    print("=" * 60)
    print(f"\n  [员工花名册]")
    print(roster)
    if _deputy_config:
        print(f"  [管理层] 正经理 + 副经理「{_deputy_config['name']}」（{_deputy_config['role']}）")
    print(f"\n  [v4 特性] DAG Pipeline 引擎 | 验证闭环 | 收敛模式 | 结构化合约 | Workspace 隔离接口")
    print(f"  [v3 特性] 项目动态分工 | 多厂商模型路由 | 任务看板 | 圆桌讨论 | 知识库 | 副经理把关")
    print(f"  [厂商] DeepSeek(主力) | GPT-5.5(重大决策) | 千问/MiniMax(备用)")
    print(f"  [策略] 优先 DeepSeek 1M，搞不定自动升级 GPT-5.4，重大决策直接用 GPT-5.5")
    print(f"  输入 /quit 退出")
    print("=" * 60)

    messages = []
    manager_system = (
        "你是一个技术管理者（Manager）。老板会给你发布任务，你需要：\n"
        "1. 分析任务，判断需要指派给哪位员工\n"
        "2. 根据员工的职能和权限选择合适的员工\n"
        "3. 使用 delegate_task 或 delegate_with_verification 指派任务给员工执行\n"
        "4. 可以同时指派多名员工——他们会并行工作，互不干扰\n"
        "5. 审核员工的执行结果，确保质量\n"
        "6. 汇总结果，向老板做最终汇报\n\n"
        "你的团队成员：\n"
        f"{roster}\n\n"
        "规则：\n"
        "- 【v4】对关键任务使用 delegate_with_verification 而非 delegate_task，这样会自动验证和重试\n"
        "- 【v4】所有项目 pipeline 通过 run_project_pipeline 执行，不要在 project_setup 后逐个 delegate_task\n"
        "- 【v4】发现结果不稳定时，用 run_convergence_loop 迭代到收敛\n"
        "- 【v4】节点状态可以通过 show_workflow_status 查看，中断后可以恢复\n"
        "- 【v4】节点 needs_replan 时用 request_replan 重新规划\n"
        "- 【项目模式】接到新项目/大任务时，先用 project_setup 分析需求并动态分配领域，再进行具体工作\n"
        "- 收到任务后先用 create_task 在看板中记录，做完后用 update_task 更新状态\n"
        "- 需要实际操作时，必须指派给有相应权限的员工\n"
        "- 同一轮回复中返回多个 delegate_task 可以让员工并行工作\n"
        "- 每个员工交付结果后，用 evaluate_result 进行三维评分\n"
        "- 员工会自动根据任务复杂度选择最佳厂商+模型（简单→flash，常规→DeepSeek 1M，复杂→千问，重大→MiniMax）\n"
        "- 遇到需要多方意见的复杂问题时，用 roundtable_discuss 发起圆桌讨论\n"
        "- 重要的经验教训用 record_knowledge 记录到共享知识库\n"
        "- 定期用 get_dashboard 查看团队状态\n"
        "- 遇到重大决策（高风险任务、跨部门协作、对员工评分无把握时）用 consult_deputy 咨询副经理\n"
        "- 做完重要决策后用 request_decision_review 让副经理复核\n"
        "- 最终汇报要清晰、完整"
    )

    while True:
        try:
            user_input = input("\n[老板] ").strip()
        except (EOFError, KeyboardInterrupt):
            save_sessions()
            print("\n系统关闭。（会话已保存）")
            break

        if user_input.lower() in ("/quit", "/exit", "/q"):
            save_sessions()
            print("系统关闭。（会话已保存）")
            break
        if not user_input:
            continue

        print(f"\n{'=' * 60}")
        print(f" 老板任务: {user_input}")
        print(f"{'=' * 60}")

        messages.append({"role": "user", "content": user_input})

        # ── Manager 模型路由（多厂商） ──
        (manager_provider, manager_model_id), needs_confirm = select_manager_model(user_input)
        if (manager_provider, manager_model_id) != MANAGER_DEFAULT_MODEL:
            print(f"\n  [*] Manager 模型路由: [{manager_provider}] {manager_model_id}")
        if needs_confirm:
            print("  [!]  检测到重大决策信号。")

        for _manager_turn in range(5):
            blocks = call_llm(
                provider_key=manager_provider,
                model_id=manager_model_id,
                messages=messages,
                system_prompt=manager_system,
                tools=MANAGER_TOOLS,
                disable_thinking=False,  # v4.2: sanitize 层已处理 thinking block，不禁用避免模型行为异常
            )

            assistant_content = []
            tool_use_blocks = []

            # 收集 blocks（plain dict，来自 call_llm）
            for block in blocks:
                if block["type"] == "text":
                    print(f"\n[Manager] {block['text']}")
                    assistant_content.append(block)

                elif block["type"] == "thinking":
                    assistant_content.append(block)

                elif block["type"] == "tool_use":
                    tool_use_blocks.append(block)

            if not tool_use_blocks:
                if assistant_content:
                    messages.append({"role": "assistant", "content": assistant_content})
                break

            # 有工具调用：将所有 tool_use 加入 assistant 消息
            for block in tool_use_blocks:
                assistant_content.append(block)

            if assistant_content:
                    messages.append({"role": "assistant", "content": assistant_content})
            assistant_content = []

            # 并行执行所有工具调用
            if len(tool_use_blocks) == 1:
                block = tool_use_blocks[0]
                result = execute_manager_tool(block["name"], block["input"], workers)
                tool_results = [(block, result)]
            else:
                with print_lock:
                    print(f"\n  [||] 并行执行 {len(tool_use_blocks)} 个任务...\n")

                def run_tool(block):
                    return execute_manager_tool(block["name"], block["input"], workers)

                tool_results = []
                with ThreadPoolExecutor(max_workers=len(tool_use_blocks)) as executor:
                    future_to_block = {
                        executor.submit(run_tool, block): block
                        for block in tool_use_blocks
                    }
                    for future in as_completed(future_to_block):
                        block = future_to_block[future]
                        try:
                            result = future.result()
                        except Exception as e:
                            result = f"任务执行异常: {e}"
                        tool_results.append((block, result))

            # 发送所有工具结果
            tool_result_content = []
            for block, result in tool_results:
                tool_result_content.append({
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": result,
                })

            messages.append({
                "role": "user",
                "content": tool_result_content,
            })

            # 获取后续回复（多厂商）
            follow_up_blocks = call_llm(
                provider_key=manager_provider,
                model_id=manager_model_id,
                messages=messages,
                system_prompt=manager_system,
                tools=MANAGER_TOOLS,
                disable_thinking=False,  # v4.2: sanitize 层已处理 thinking block，不禁用避免模型行为异常
            )
            for fb in follow_up_blocks:
                if fb["type"] == "text":
                    print(f"\n[Manager] {fb['text']}")
                    assistant_content.append(fb)
                elif fb["type"] == "thinking":
                    assistant_content.append(fb)

            if assistant_content:
                    messages.append({"role": "assistant", "content": assistant_content})

        print(f"\n{'=' * 60}")
        print(" 任务处理完毕，等待下一个任务")
        print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
