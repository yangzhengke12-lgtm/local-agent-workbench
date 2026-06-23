"""LLM 调用层 —— 统一多厂商 API 调用入口。

包含：
- call_llm: 单次 API 调用（Anthropic/OpenAI 自动适配）
- call_llm_once: 简化单轮入口
- call_llm_multi_turn: 多轮工具调用循环 + Runtime 防护
- Anthropic ↔ OpenAI 格式转换
"""
import json
import os
import hashlib
import subprocess as _sp

from runtime.config import PROVIDERS
from runtime.routing import _resolve_route
from runtime.sanitize import sanitize_messages_for_provider
from runtime.pure_functions import _extract_json_from_text


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
    """Anthropic 消息格式 → OpenAI 消息格式。"""
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
                    pass  # OpenAI 不支持 thinking 块

        if tool_calls:
            openai_msgs.append({
                "role": "assistant",
                "content": "\n".join(text_parts) if text_parts else None,
                "tool_calls": tool_calls,
            })
        elif text_parts:
            openai_msgs.append({"role": role, "content": "\n".join(text_parts)})

        for tr in tool_results:
            openai_msgs.append(tr)

    return openai_msgs


def openai_response_to_anthropic_blocks(response) -> list:
    """OpenAI chat.completions 响应 → Anthropic 风格 content blocks。"""
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

    if hasattr(msg, "reasoning_content") and msg.reasoning_content:
        blocks.insert(0, {"type": "thinking", "thinking": msg.reasoning_content, "signature": ""})

    return blocks


# ── 统一 LLM 调用 ────────────────────────────────────────────

def call_llm(provider_key: str, model_id: str, messages: list,
             system_prompt: str = "", tools: list = None,
             max_tokens: int = 4096, disable_thinking: bool = False) -> list:
    """统一的 LLM 调用接口：根据 provider 自动选择 Anthropic 或 OpenAI 格式。"""
    provider = PROVIDERS.get(provider_key)
    if not provider:
        raise ValueError(f"未知厂商: {provider_key}。可用: {list(PROVIDERS.keys())}")

    if provider["type"] == "anthropic":
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


def call_llm_once(prompt: str,
                  system_prompt: str = "",
                  tier: str = "normal",
                  max_tokens: int = 4096,
                  provider_key: str | None = None,
                  model_id: str | None = None,
                  disable_thinking: bool = False) -> str:
    """统一 LLM 调用入口。禁止业务函数绕开此函数直接调底层 client。"""
    if provider_key and model_id:
        pk, mid = provider_key, model_id
    else:
        pk, mid = _resolve_route(tier)

    messages = [{"role": "user", "content": prompt}]
    messages = sanitize_messages_for_provider(messages, pk)

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


def call_llm_multi_turn(provider_key: str, model_id: str, messages: list,
                         system_prompt: str = "", tools: list = None,
                         max_turns: int = 10, max_tokens: int = 4096,
                         log_callback=None, execute_tool_fn=None,
                         disable_thinking: bool = False) -> str:
    """多轮 LLM 调用：自动处理工具调用循环，返回最终文本结果。

    execute_tool_fn 为 None 时从 runtime.tools 懒加载。
    """
    # 懒加载 execute_tool 避免循环 import
    if execute_tool_fn is None:
        from runtime.tools import execute_tool as _default_exec
        tool_executor = _default_exec
    else:
        tool_executor = execute_tool_fn

    final_text = ""
    last_tool_sig = ""
    repeat_count = 0
    total_reads = 0
    total_writes = 0
    tool_call_history: dict[str, int] = {}
    tool_result_cache: dict[str, str] = {}
    blocked_tool_names: set[str] = set()
    write_content_hashes: dict[str, int] = {}
    write_budget_exceeded = False
    force_break_loop = False
    first_write_success = False
    noop_write_count = 0
    duplicate_write_count = 0
    guard_reason = ""
    MAX_WRITES_PER_ATTEMPT = 3

    for _turn in range(max_turns):
        force_stop = False
        restart_turn_early = False
        if repeat_count >= 3:
            messages.append({
                "role": "user",
                "content": "你已经连续多次执行相同的操作。请立即停止，基于已有的信息给出你的结论。不要再调用任何工具。",
            })
            repeat_count = 0
            force_stop = True
        elif total_reads >= 3:
            messages.append({
                "role": "user",
                "content": "你已经读取了足够多的文件内容。请立即给出你的分析和结论，不要再调用 read_file。直接输出文本回复。",
            })
            total_reads = 0
            force_stop = True
        elif total_writes >= MAX_WRITES_PER_ATTEMPT:
            write_budget_exceeded = True
            messages.append({
                "role": "user",
                "content": f"写入预算已耗尽（最多 {MAX_WRITES_PER_ATTEMPT} 次）。禁止再调用 write_file。请立即输出 WorkerResult JSON。",
            })

        safe_messages = sanitize_messages_for_provider(messages, provider_key)
        available_tools = [
            tool for tool in (tools or [])
            if tool.get("name") not in blocked_tool_names
        ]
        available_tool_names = {tool.get("name") for tool in available_tools}
        blocks = call_llm(provider_key, model_id, safe_messages,
                          system_prompt=system_prompt, tools=available_tools,
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
                    continue
                has_tool_use = True
                tool_name = block["name"]
                tool_args = block["input"]
                tool_id = block["id"]

                if tools is not None and tool_name not in available_tool_names:
                    result = (
                        f"TOOL_DISABLED: {tool_name} 当前不可用。"
                        "请不要再次调用它，改为基于已有信息完成总结，"
                        "或选择其它仍然可用的工具继续。"
                    )
                    if log_callback:
                        log_callback(tool_name, tool_args, result)
                    assistant_content.append(block)
                    messages.append({"role": "assistant", "content": assistant_content})
                    assistant_content = []
                    messages.append({
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": result}],
                    })
                    restart_turn_early = True
                    break

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

                # ── v4.2: write_file 专项 guard ──
                write_handled = False
                if tool_name == "write_file":
                    content = tool_args.get("content", "")
                    path = tool_args.get("file_path", "")
                    content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()

                    # Guard 1: NOOP
                    if os.path.isfile(path):
                        try:
                            with open(path, "r", encoding="utf-8") as _f:
                                existing = _f.read()
                            if existing == content:
                                result = (
                                    f"NOOP_WRITE: {path} 已包含完全相同的內容。"
                                    "不需要再次写入。请立即输出最终的 WorkerResult JSON。"
                                )
                                write_handled = True
                                force_break_loop = True
                                noop_write_count += 1
                                if not guard_reason:
                                    guard_reason = "noop_write"
                        except Exception:
                            pass

                    # Guard 2: DUPLICATE
                    if not write_handled:
                        dedup_write_key = f"{path}:{content_hash}"
                        write_count = write_content_hashes.get(dedup_write_key, 0) + 1
                        write_content_hashes[dedup_write_key] = write_count
                        if write_count > 1:
                            result = (
                                f"DUPLICATE_WRITE_BLOCKED: {path} 已被写入相同內容 {write_count} 次。"
                                "立即停止工具调用，输出最终的 WorkerResult JSON。"
                            )
                            write_handled = True
                            force_break_loop = True
                            duplicate_write_count += 1
                            if not guard_reason:
                                guard_reason = "duplicate_write_blocked"

                    # Guard 3: BUDGET
                    if not write_handled and write_budget_exceeded:
                        result = (
                            "WRITE_BUDGET_EXCEEDED: 本轮已达到最大写入次数限制。"
                            "禁止再写文件。立即输出最终的 WorkerResult JSON。"
                        )
                        write_handled = True
                        force_break_loop = True
                        if not guard_reason:
                            guard_reason = "write_budget_exceeded"

                # ── v4.2: general tool call dedup ──
                if not write_handled:
                    dedup_key = f"{tool_name}:{json.dumps(tool_args, sort_keys=True, ensure_ascii=False)}"
                    dedup_count = tool_call_history.get(dedup_key, 0) + 1
                    tool_call_history[dedup_key] = dedup_count
                    if dedup_count > 2:
                        result = (
                            f"REPEATED_TOOL_CALL_BLOCKED: {tool_name} 使用相同参数重复 {dedup_count} 次。"
                            "运行时已强制停止继续调用工具。请基于已有工具结果输出最终 WorkerResult JSON。"
                        )
                        force_break_loop = True
                        if not guard_reason:
                            guard_reason = "repeated_tool_call_blocked"
                    elif dedup_count == 2 and dedup_key in tool_result_cache:
                        blocked_tool_names.add(tool_name)
                        cached = tool_result_cache[dedup_key]
                        result = (
                            f"DUPLICATE_TOOL_RESULT: 你已经拿到过同一工具与同一参数的结果。"
                            f"系统已暂时禁用 {tool_name} 的后续同参调用。"
                            "请直接基于下面的缓存结果继续完成任务；"
                            "如果需要下一步动作，请改为调用别的工具或直接给出结论。\n\n"
                            f"{cached}"
                        )
                        restart_turn_early = True
                    else:
                        try:
                            result = tool_executor(tool_name, tool_args)
                            tool_result_cache[dedup_key] = result
                            if tool_name == "write_file" and "成功" in str(result):
                                first_write_success = True
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
                if restart_turn_early:
                    break

        if assistant_content and not any(b.get("type") == "text" for b in assistant_content):
            assistant_content.insert(0, {"type": "text", "text": "[分析中]"})

        if assistant_content:
            messages.append({"role": "assistant", "content": assistant_content})

        if restart_turn_early:
            continue

        if not has_tool_use or force_break_loop:
            break

    # v4.2: force-break → evidence-rich WorkerResult
    if force_break_loop:
        written_files = list({k.split(":")[0] for k in write_content_hashes.keys()})
        artifacts_list = [
            {"path": p, "type": "write_file", "summary": f"Modified {os.path.basename(p)}"}
            for p in written_files
        ]
        artifacts_json = json.dumps(artifacts_list, ensure_ascii=False)

        diff_evidence: dict[str, str] = {}
        for fpath in written_files:
            if os.path.isfile(fpath):
                try:
                    parent = os.path.dirname(fpath) or "."
                    fname = os.path.basename(fpath)
                    r = _sp.run(
                        ["git", "diff", "--", fname],
                        capture_output=True, encoding="utf-8", errors="replace",
                        timeout=5, cwd=parent,
                    )
                    if r.stdout.strip():
                        diff_evidence[fpath] = r.stdout.strip()[:2000]
                except Exception:
                    pass

        evidence = {
            "changed_files": written_files,
            "write_count": total_writes,
            "noop_write_count": noop_write_count,
            "duplicate_write_count": duplicate_write_count,
            "first_write_succeeded": first_write_success,
            "guard_reason": guard_reason or "unknown",
        }
        if diff_evidence:
            evidence["diff"] = diff_evidence

        issues = []
        if guard_reason:
            issues.append({
                "severity": "medium",
                "description": f"Runtime guard triggered: {guard_reason}. Verifier must inspect artifacts to confirm correctness.",
                "suggestion": "Check file content, run tests, and verify the change is correct before passing.",
            })

        status = "needs_review"
        existing_json = _extract_json_from_text(final_text) if final_text.strip() else None
        if existing_json:
            try:
                data = json.loads(existing_json)
                data["artifacts"] = artifacts_list
                data["evidence"] = evidence
                if issues:
                    data["issues"] = data.get("issues", []) + issues
                data["status"] = status
                final_text = json.dumps(data, ensure_ascii=False)
            except (json.JSONDecodeError, KeyError):
                final_text = json.dumps({
                    "status": status,
                    "summary": f"Runtime guard '{guard_reason}' stopped tool execution. Verifier must inspect artifacts.",
                    "artifacts": artifacts_list,
                    "issues": issues,
                    "evidence": evidence,
                    "retryable": True,
                    "confidence": 0.5,
                }, ensure_ascii=False)
        else:
            final_text = json.dumps({
                "status": status,
                "summary": f"Runtime guard '{guard_reason}' stopped tool execution. Verifier must inspect artifacts.",
                "artifacts": artifacts_list,
                "issues": issues,
                "evidence": evidence,
                "retryable": True,
                "confidence": 0.5,
            }, ensure_ascii=False)

    return final_text
