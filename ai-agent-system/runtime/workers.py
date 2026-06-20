"""Worker 执行层 —— load_workers, run_worker, run_deputy。

Worker 是系统的核心执行单元：加载配置 → 模型路由 → 工具循环 → 结果归一化。
"""
import json
import os
from dataclasses import asdict

from runtime.config import DEFAULT_MODEL, PROVIDERS, UPGRADE_TARGET, _init_providers
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


def load_workers(config_path: str = "workers.json") -> dict:
    """加载 workers.json，返回 { name: config } 映射。"""
    _init_providers()
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
