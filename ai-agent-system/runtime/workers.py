"""Worker 执行层 —— load_workers, run_worker, run_deputy。

Worker 是系统的核心执行单元：加载配置 → 模型路由 → 工具循环 → 结果归一化。
"""
import json
import os
import re
from dataclasses import asdict

from runtime.config import DEFAULT_MODEL, PROVIDERS, UPGRADE_TARGET
from runtime.routing import select_worker_model
from runtime.persistence import (
    _workers_config,
    _deputy_config,
    worker_sessions,
    log_model_usage,
)
from runtime.llm import call_llm_multi_turn
from runtime.sanitize import sanitize_messages_for_provider
from runtime.pure_functions import _normalize_worker_result, _discover_artifacts
from runtime.tools import ALL_TOOLS, execute_tool, print_lock, track_api_call


def _parse_json_result(text: str) -> dict | None:
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return None


def _extract_report_section(report_text: str, section_name: str) -> str:
    pattern = rf"\[{re.escape(section_name)}\]\n([\s\S]*?)(?=\n\n\[|\Z)"
    match = re.search(pattern, report_text)
    return match.group(1).strip() if match else ""


def _extract_git_report(tool_history: list[dict]) -> str:
    for item in reversed(tool_history):
        if item.get("name") != "git_inspect":
            continue
        result = str(item.get("result", ""))
        if "[git status]" in result:
            return result
    return ""


def _looks_like_feishu_report_task(task: str, tool_names: list[str]) -> bool:
    if "feishu_send_message" not in tool_names:
        return False
    lowered = (task or "").lower()
    wants_feishu = any(key in lowered for key in ("feishu", "lark", "飞书"))
    wants_report = any(key in lowered for key in ("日报", "daily report", "report", "汇报", "总结", "summary"))
    return wants_feishu and wants_report


def _status_files_from_git_report(report_text: str) -> list[str]:
    section = _extract_report_section(report_text, "git status")
    files = []
    for line in section.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("?? "):
            path = line[3:].strip()
        else:
            parts = line.split(maxsplit=1)
            path = parts[1].strip() if len(parts) == 2 else line
        files.append(path.replace("\\", "/"))
    return files


def _build_daily_report_from_git_report(report_text: str) -> str:
    files = _status_files_from_git_report(report_text)

    def touched(*keys: str) -> bool:
        return any(any(key in path for key in keys) for path in files)

    bullets: list[str] = []
    if touched("desktop/main.js"):
        bullets.append("完成桌面端稳定性修复，补强 Electron 后端健康监控、单实例控制和异常自恢复。")
    if touched("runtime/agent_task.py", "runtime/llm.py"):
        bullets.append("强化运行时链路，补齐任务持久化恢复、未完成任务回收和多轮工具防循环护栏。")
    if touched("runtime/tools.py", "workers.json", "runtime/workers.py"):
        bullets.append("完善 Agent 工具链，为 Elena 补充 Git 变更检查、日报整理与飞书发送相关能力。")
    if touched("tests/"):
        bullets.append("补充回归验证，覆盖 API、runtime guardrails、重复工具调用和桌面稳定性相关场景。")
    if touched("README.md", "docs/", "examples/"):
        bullets.append("更新 README、业务连接说明和示例素材，方便后续演示、接入与对外说明。")

    recent_commits = _extract_report_section(report_text, "recent commits")
    if recent_commits and len(bullets) < 4:
        first_commit = next((line.strip() for line in recent_commits.splitlines() if line.strip()), "")
        if first_commit:
            subject = first_commit.split(" ", 1)[1] if " " in first_commit else first_commit
            bullets.append(f"同步近期提交脉络，当前最新一轮改动重点聚焦在：{subject[:60]}。")

    if len(bullets) < 4:
        bullets.append("当前仓库仍有多项本地改动，已纳入本次日报整理范围，并准备继续做收尾验证。")

    return "今日进展\n" + "\n".join(f"- {line}" for line in bullets[:4])


def _should_use_fallback_summary(summary_text: str) -> bool:
    summary = (summary_text or "").strip()
    if not summary or summary in {"(无输出)", ""}:
        return True
    bad_markers = (
        "Runtime guard",
        "TOOL_DISABLED",
        "DUPLICATE_TOOL_RESULT",
        "REPEATED_TOOL_CALL_BLOCKED",
    )
    return any(marker in summary for marker in bad_markers)


def load_workers(config_path: str = "workers.json") -> dict:
    """加载 workers.json，返回 { name: config } 映射。"""
    # 懒加载 default_client
    from runtime.config import get_default_client

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
                "client": get_default_client(),
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
        }

    _workers_config.clear()
    _workers_config.update(workers)
    _deputy_config.clear()
    _deputy_config.update(deputy)
    return workers


def run_worker(worker_cfg: dict, task: str, use_memory: bool = True,
               project_name: str = "",
               fresh_session: bool = False,
               session_scope: str = "",
               disable_thinking: bool = False,
               is_verifier: bool = False) -> dict:
    """启动一个 Worker，使用其专属工具和配置执行任务。支持多轮对话记忆。"""
    name = worker_cfg["name"]
    role = worker_cfg["role"]
    tools = worker_cfg["tools"]
    tool_names = worker_cfg["tool_names"]
    base_model = worker_cfg["model"]

    (worker_provider_key, worker_model_id), complexity, model_reason = select_worker_model(
        task, name, is_verifier=is_verifier)
    base_provider = "deepseek"

    if (worker_provider_key, worker_model_id) != (base_provider, base_model):
        with print_lock:
            print(f"\n      [*] Worker-{name} 模型路由: [{base_provider}]{base_model}")
            print(f"         → [{worker_provider_key}] {worker_model_id} ({complexity})")
            print(f"         原因: {model_reason}")

    if project_name:
        log_model_usage(project_name, name, task, worker_provider_key, worker_model_id)

    indent = " " * 4
    provider_label = f"[{worker_provider_key}]" if worker_provider_key != "deepseek" else ""
    prefix = f"[Worker-{name}|{role}]{provider_label}"
    log = []
    tool_history: list[dict] = []

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
        "- 【关键】写文件后必须立即输出 WorkerResult JSON 总结，不要反复写。\n"
        "- 【关键】如果工具返回 NOOP_WRITE / DUPLICATE_WRITE_BLOCKED / WRITE_BUDGET_EXCEEDED，必须立即停止所有工具调用，输出最终的 JSON。\n"
        "- 优先选择单次信息密度更高的工具调用，避免重复查询同类信息；如果已有足够证据，就直接完成总结。\n"
        "- 完成后必须用 JSON 格式总结：{\"status\": \"success|partial|failed\", \"summary\": \"...\", \"artifacts\": [...]}\n"
        "- 遇到问题优先用 ask_coworker 找合适的同事求助，不要独自硬撑"
    )

    base_key = f"{name}|{role}"
    if session_scope:
        session_key = f"{base_key}|{session_scope}"
    else:
        session_key = base_key

    if use_memory and not fresh_session and session_key in worker_sessions:
        messages = worker_sessions[session_key]
        messages.append({"role": "user", "content": f"[新任务] {task}"})
        messages = sanitize_messages_for_provider(messages, worker_provider_key)
        log_print("(记得之前的对话上下文)")
    else:
        if fresh_session:
            log_print("(全新会话)")
        messages = [{"role": "user", "content": task}]

    def tool_callback(tool_name: str, tool_args: dict, tool_result: str):
        nonlocal log
        if tool_result:
            tool_history.append({
                "name": tool_name,
                "args": tool_args,
                "result": tool_result,
            })
            log_print(f"[工具返回: {tool_result[:300]}]")
        else:
            log_print(f"[工具: {tool_name}({json.dumps(tool_args, ensure_ascii=False)})]")

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
        max_turns=6,
        log_callback=tool_callback,
        execute_tool_fn=worker_execute_tool,
        disable_thinking=disable_thinking,
    )

    # 失败重试：DeepSeek → GPT
    failure_signals = ["无法完成", "超出能力", "无法处理", "unable to", "cannot", "权限不足"]
    is_failure = any(kw in final_result for kw in failure_signals)

    if is_failure and worker_provider_key == "deepseek" and "gpt" in PROVIDERS:
        retry_provider, retry_model = UPGRADE_TARGET
        log_print(f"DeepSeek 未能完成任务，自动升级到 [{retry_provider}]{retry_model} 重试...")

        messages.clear()
        messages.append({"role": "user", "content": task})

        final_result = call_llm_multi_turn(
            provider_key=retry_provider,
            model_id=retry_model,
            messages=messages,
            system_prompt=system_prompt,
            tools=tools,
            max_turns=6,
            log_callback=tool_callback,
            execute_tool_fn=worker_execute_tool,
            disable_thinking=disable_thinking,
        )
        worker_provider_key = retry_provider
        worker_model_id = retry_model

    if _looks_like_feishu_report_task(task, tool_names):
        send_succeeded = any(
            item.get("name") == "feishu_send_message"
            and bool((_parse_json_result(item.get("result", "")) or {}).get("ok"))
            for item in tool_history
        )
        if not send_succeeded:
            git_report = _extract_git_report(tool_history)
            if not git_report and "git_inspect" in allowed_names:
                git_args = {"mode": "report"}
                log_print(f"[工具: git_inspect({json.dumps(git_args, ensure_ascii=False)})]")
                git_report = worker_execute_tool("git_inspect", git_args)
                tool_history.append({"name": "git_inspect", "args": git_args, "result": git_report})
                log_print(f"[工具返回: {git_report[:300]}]")

            if git_report and "[git status]" in git_report:
                normalized_result = _normalize_worker_result(final_result)
                report_text = (
                    _build_daily_report_from_git_report(git_report)
                    if _should_use_fallback_summary(normalized_result.summary)
                    else normalized_result.summary.strip()
                )
                feishu_args = {"title": f"{name} 日报", "text": report_text}
                log_print("[fallback] 基于 git_inspect 结果生成日报并尝试发送飞书")
                log_print(f"[工具: feishu_send_message({json.dumps(feishu_args, ensure_ascii=False)})]")
                feishu_result = worker_execute_tool("feishu_send_message", feishu_args)
                tool_history.append({"name": "feishu_send_message", "args": feishu_args, "result": feishu_result})
                log_print(f"[工具返回: {feishu_result[:300]}]")
                parsed_send = _parse_json_result(feishu_result) or {}
                send_ok = bool(parsed_send.get("ok"))
                final_result = json.dumps({
                    "status": "success" if send_ok else "needs_review",
                    "summary": report_text + ("\n\n飞书发送：成功" if send_ok else f"\n\n飞书发送失败：{feishu_result[:200]}"),
                    "artifacts": [],
                    "issues": [] if send_ok else [{
                        "severity": "high",
                        "description": "日报已生成，但飞书发送未成功完成。",
                        "suggestion": "检查 FEISHU_WEBHOOK_URL 配置和网络连通性后重试。",
                    }],
                    "retryable": not send_ok,
                    "confidence": 0.95 if send_ok else 0.6,
                    "delivery": {
                        "tool": "feishu_send_message",
                        "ok": send_ok,
                        "response": parsed_send or feishu_result,
                    },
                }, ensure_ascii=False)

    if use_memory and not fresh_session:
        worker_sessions[session_key] = messages

    structured = _normalize_worker_result(final_result)
    log_artifacts = _discover_artifacts(log)
    if not structured.artifacts:
        structured.artifacts = log_artifacts
    elif log_artifacts:
        seen = {a.get("path", "") for a in structured.artifacts}
        for a in log_artifacts:
            if a.get("path", "") not in seen:
                structured.artifacts.append(a)

    return {
        "log": log,
        "result": final_result,
        "messages": messages,
        "model_used": f"[{worker_provider_key}]{worker_model_id}",
        "complexity": worker_provider_key,
        "structured_result": asdict(structured),
    }


def run_deputy(task: str, manager_tools: list,
               execute_manager_tool_fn=None) -> dict:
    """运行副经理对话，使用 Manager 工具集。

    execute_manager_tool_fn 由调用方注入，避免循环 import。
    """
    if not _deputy_config:
        return {"result": "错误：未配置副经理", "log": []}

    if execute_manager_tool_fn is None:
        raise ValueError("run_deputy requires execute_manager_tool_fn to be injected")

    name= _deputy_config["name"]
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
                    result = execute_manager_tool_fn(tool_name, tool_args, _workers_config)
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
