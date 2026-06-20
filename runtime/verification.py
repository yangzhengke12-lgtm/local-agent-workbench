"""验证闭环 —— _run_verifier + delegate_with_verification。

双 Verifier 并行审查（Sophia∥Nathaniel），最坏优先合并，自动重试。
"""
import json
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict

from runtime.contracts import WorkerResult, VerificationResult, Budget
from runtime.pure_functions import (
    _normalize_worker_result,
    _normalize_verification_result,
    _merge_verdicts,
    _check_budget,
)
from runtime.workers import run_worker


def _run_verifier(verifier_cfg: dict, worker_result: WorkerResult,
                  original_task: str, verifier_mode: str = "code_review") -> VerificationResult:
    """运行单个验证者对 Worker 产出做质量验证。永不返回 N/A。"""
    name = verifier_cfg.get("name", "Verifier")

    if verifier_mode == "test_validation":
        mode_instructions = (
            "你是测试验证者。你的首要任务是运行测试来验证 Worker 的产出。\n"
            "1. 如果 Worker 修改了代码，用 run_command 执行项目的测试命令（如 pytest）\n"
            "2. 基于测试输出判断：全部通过 → pass；有失败 → needs_retry\n"
            "3. 除非测试失败需要定位原因，否则不要 read_file\n"
            "4. 每个文件最多读取 1 次。读取后立即给出结论。\n"
            f"5. 最多使用 4 个工具调用。超过后必须输出 VerificationResult JSON。"
        )
    else:
        mode_instructions = (
            "你是代码审查者。请检查 Worker 的产出质量。\n"
            "1. 如果 Worker 修改了文件，用 read_file 检查代码\n"
            "2. 关注：逻辑正确性、安全隐患、代码风格、边界条件\n"
            "3. 每个文件最多读取 1 次。不要重复读取同一文件。\n"
            f"4. 最多使用 4 个工具调用。超过后必须输出 VerificationResult JSON。"
        )

    evidence_text = ""
    raw_data = {}
    try:
        raw_data = json.loads(worker_result.raw_text) if worker_result.raw_text else {}
    except (json.JSONDecodeError, TypeError):
        pass
    evidence = raw_data.get("evidence", {}) if isinstance(raw_data, dict) else {}
    if evidence:
        evidence_text = f"Runtime 证据: {json.dumps(evidence, ensure_ascii=False)}\n\n"

    prompt = (
        f"请验证以下工作产出的质量。\n\n"
        f"原始任务: {original_task}\n"
        f"Worker 状态: {worker_result.status}\n"
        f"Worker 摘要: {worker_result.summary}\n"
        f"产物列表: {json.dumps(worker_result.artifacts, ensure_ascii=False)}\n"
        f"发现的问题: {json.dumps(worker_result.issues, ensure_ascii=False)}\n"
        f"{evidence_text}"
        f"{mode_instructions}\n\n"
        "⚠️ 关键规则：\n"
        "- 不要重复读取同一个文件。读一次就够了。\n"
        "- 禁止连续两次执行相同的工具调用。\n"
        "- 读完后立即分析，不要再读。\n"
        "- 必须在回复中输出 JSON 格式的验证结果。\n"
        "- 【重要】如果 Worker status 是 'needs_review' 或 'partial'，不要因此自动判定失败。\n"
        "  这只表示 Worker 被 runtime guard 终止了。你必须检查 artifacts 中列出的文件，\n"
        "  用 read_file 看内容或用 run_command 跑测试。如果文件修改正确、测试通过 → 返回 pass。\n"
        "- 【重要】'guard_reason'(如 noop_write/duplicate_write_blocked/write_budget_exceeded)\n"
        "  是 runtime 的保护机制，不是 Worker 的错误。只要产物正确就返回 pass。\n\n"
        "输出格式：\n"
        '{"verdict": "pass|reject|needs_retry|needs_replan", '
        '"score": 1-5, '
        '"blocking_issues": [{"severity": "critical|high|medium|low", '
        '"description": "...", "suggestion": "..."}], '
        '"retry_instruction": "如果不通过，给 Worker 的具体改进指令"}'
    )

    raw_output = ""
    try:
        raw = run_worker(verifier_cfg, prompt, use_memory=False, fresh_session=True,
                         disable_thinking=True, is_verifier=True)
        if not isinstance(raw, dict):
            raise TypeError(f"run_worker returned {type(raw).__name__} instead of dict")
        raw_output = raw.get("result", "")
    except Exception as e:
        traceback.print_exc()
        try:
            raw = run_worker(verifier_cfg, prompt, use_memory=False, fresh_session=True,
                             disable_thinking=False, is_verifier=True)
            if isinstance(raw, dict):
                raw_output = raw.get("result", "")
        except Exception as e2:
            traceback.print_exc()

    result = _normalize_verification_result(raw_output)
    if not result.blocking_issues and result.verdict != "pass":
        result.blocking_issues = [{
            "severity": "high",
            "description": f"Verifier {name} ({verifier_mode}) failed to produce valid VerificationResult",
            "suggestion": "Inspect worker artifacts manually and retry with stricter validation.",
        }]
    if not result.retry_instruction.strip():
        result.retry_instruction = (
            f"Verifier {name} execution failed or produced non-JSON output. "
            "Please re-check your work and ensure all requirements are met."
        )
    return result


def delegate_with_verification(workers: dict, worker_name: str, task: str,
                                verifier_names: list | None = None,
                                max_retries: int = 3,
                                project_name: str = "",
                                budget: Budget | None = None) -> dict:
    """【v4 P0】委托任务 + 质量验证 + 不通过自动重试。"""
    if verifier_names is None:
        verifier_names = []
        for name in ("Sophia", "Nathaniel"):
            if name in workers:
                verifier_names.append(name)

    budget = budget or Budget()
    attempt_log: list[dict] = []
    current_task = task

    for attempt in range(1, max_retries + 2):
        try:
            raw = run_worker(workers[worker_name], current_task,
                             project_name=project_name,
                             fresh_session=True,
                             session_scope=f"verified_{attempt}")
            worker_result = _normalize_worker_result(raw["result"])
        except Exception as e:
            traceback.print_exc()
            return {
                "worker_result": None,
                "verification": None,
                "attempts": attempt_log,
                "final_status": "failed",
                "reason": f"Worker execution failed: {e}",
            }

        budget_status = _check_budget(
            {"attempts": attempt, "model_calls": attempt * 3},
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

        if not verifier_names:
            return {
                "worker_result": asdict(worker_result),
                "verification": None,
                "attempts": attempt_log,
                "final_status": worker_result.status,
            }

        verdicts: list[VerificationResult] = []
        with ThreadPoolExecutor(max_workers=len(verifier_names)) as executor:
            futures = {}
            for vname in verifier_names:
                if vname in workers:
                    mode = "test_validation" if vname == "Nathaniel" else "code_review"
                    future = executor.submit(
                        _run_verifier, workers[vname], worker_result, task, mode
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
            case _:
                return {
                    "worker_result": asdict(worker_result),
                    "verification": asdict(merged),
                    "attempts": attempt_log,
                    "final_status": "failed",
                    "reason": f"未知判决: {merged.verdict}",
                }

    return {
        "worker_result": asdict(worker_result),
        "verification": None,
        "attempts": attempt_log,
        "final_status": "failed",
    }
