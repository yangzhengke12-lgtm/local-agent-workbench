"""v4 纯函数 —— 状态转移、JSON 解析、结果归一化、验证合并、预算检查。

所有函数无副作用、不访问 I/O、不调用外部 API。
依赖：仅 runtime.contracts + stdlib
"""
import json
import re
from datetime import datetime

from runtime.contracts import (
    TaskNodeStatus,
    VALID_TRANSITIONS,
    WorkerResult,
    VerificationResult,
    TaskNode,
    Budget,
)


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
    """自由文本 → VerificationResult。永不抛异常，永不返回 N/A。

    v4.2 防御性处理：
    - 空文本 / 纯文本 / 非 JSON
    - WorkerResult 形状的 JSON（status 而非 verdict）
    - score 为字符串
    - Markdown 围栏 JSON
    - 中文 verdict 词汇
    """
    if not raw_text or not raw_text.strip():
        return VerificationResult(
            verdict="needs_retry",
            score=2,
            blocking_issues=[{"severity": "high", "description": "Verifier produced empty output"}],
            retry_instruction="Verifier returned empty response. Re-run verification.",
            raw_text="",
        )

    try:
        json_str = _extract_json_from_text(raw_text)
        if json_str:
            data = json.loads(json_str)

            # 处理 WorkerResult 形状：status → verdict
            verdict = data.get("verdict", "")
            if not verdict and "status" in data:
                status_map = {
                    "success": "pass", "done": "pass",
                    "partial": "needs_retry", "needs_review": "needs_retry",
                    "failed": "reject", "error": "reject",
                }
                verdict = status_map.get(data.get("status", ""), "needs_retry")

            # 中文 verdict 映射
            cn_map = {
                "通过": "pass", "合格": "pass", "正确": "pass",
                "拒绝": "reject", "驳回": "reject",
                "重试": "needs_retry", "需要重试": "needs_retry", "不通过": "needs_retry",
                "重规划": "needs_replan", "重新规划": "needs_replan",
            }
            if verdict in cn_map:
                verdict = cn_map[verdict]
            if not verdict:
                # 检查 raw_text 中是否包含中文 verdict
                for cn, en in cn_map.items():
                    if cn in raw_text[:200]:
                        verdict = en
                        break
            if not verdict:
                verdict = "needs_retry"

            # score: 容错 string/int/float
            try:
                score = float(data.get("score", 0))
            except (ValueError, TypeError):
                score = 2.0

            return VerificationResult(
                verdict=verdict,
                score=score,
                blocking_issues=data.get("blocking_issues", []),
                retry_instruction=data.get("retry_instruction", raw_text[:500]),
                raw_text=raw_text,
            )
    except (json.JSONDecodeError, TypeError, KeyError, ValueError):
        pass

    # 最终 fallback：纯文本 → needs_retry
    return VerificationResult(
        verdict="needs_retry",
        score=2,
        blocking_issues=[{"severity": "high", "description": f"Verifier returned non-JSON: {raw_text[:100]}"}],
        retry_instruction=raw_text[:500] if raw_text.strip() else "Verifier produced non-JSON output.",
        raw_text=raw_text,
    )


def _merge_verdicts(verdicts: list[VerificationResult]) -> VerificationResult:
    """合并多个验证结果。最坏优先：reject > needs_replan > needs_retry > pass。

    v4.2: 确保永不返回 N/A——空列表、无效 verdict、全 fallback 均兜底。
    """
    if not verdicts:
        return VerificationResult(
            verdict="needs_retry",
            score=2,
            blocking_issues=[{"severity": "critical", "description": "No verifier produced a result"}],
            retry_instruction="All verifiers failed. Inspect worker artifacts manually.",
        )

    VALID_VERDICTS = {"pass", "reject", "needs_retry", "needs_replan"}
    priority = {"reject": 3, "needs_replan": 2, "needs_retry": 1, "pass": 0}

    # 过滤无效 verdict
    valid = [v for v in verdicts if v.verdict in VALID_VERDICTS]
    if not valid:
        valid = verdicts  # 全部无效时兜底——取所有 issues

    worst = max(valid, key=lambda v: priority.get(v.verdict, 0)) if valid else VerificationResult(verdict="needs_retry")

    all_issues: list[dict] = []
    all_instructions: list[str] = []
    total_score = 0.0
    for v in verdicts:
        all_issues.extend(v.blocking_issues)
        if v.retry_instruction:
            all_instructions.append(v.retry_instruction)
        total_score += v.score

    # 去重 blocking_issues
    seen_issues: set[str] = set()
    deduped_issues: list[dict] = []
    for issue in all_issues:
        key = issue.get("description", "")[:80]
        if key not in seen_issues:
            seen_issues.add(key)
            deduped_issues.append(issue)

    merged_verdict = worst.verdict if worst.verdict in VALID_VERDICTS else "needs_retry"

    return VerificationResult(
        verdict=merged_verdict,
        score=round(total_score / max(len(verdicts), 1), 1),
        blocking_issues=deduped_issues,
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
    seen_paths: set[str] = set()
    for entry in log:
        path = ""
        tool_name = ""
        if isinstance(entry, dict):
            tool_name = entry.get("tool", "")
            if tool_name in ("write_file", "save_template"):
                try:
                    args = entry.get("args", {})
                    if isinstance(args, str):
                        args = json.loads(args)
                    path = args.get("path", args.get("file_path", ""))
                except (json.JSONDecodeError, TypeError):
                    pass
        elif isinstance(entry, str):
            # v4.2: 从字符串 log 中正则提取 write_file 路径
            m = re.search(r'write_file.*?file_path["\':]+\s*["\']([^"\']+)', entry)
            if m:
                path = m.group(1)
                tool_name = "write_file"

        if path and path not in seen_paths:
            seen_paths.add(path)
            artifacts.append({
                "path": path,
                "type": tool_name or "write_file",
                "summary": f"{tool_name or 'write_file'}: {path}",
            })
    return artifacts
