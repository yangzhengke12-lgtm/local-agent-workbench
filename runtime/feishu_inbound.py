"""Inbound Feishu/Lark event bridge.

This module keeps the event parsing and idempotency state out of server.py.
It intentionally supports the common unencrypted event subscription flow:
- URL verification challenge
- im.message.receive_v1 text messages
- token validation via FEISHU_EVENT_VERIFICATION_TOKEN
"""
from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = REPO_ROOT / "feishu_events.json"
MAX_INBOUND_TEXT_CHARS = 4000
MAX_TASK_REPLY_CHARS = 3500
MAX_CHAT_HISTORY_MESSAGES = 30
MAX_CONTEXT_MESSAGES = 8
MAX_CONTEXT_MESSAGE_CHARS = 500
MAX_TODAY_TASKS = 12
MAX_TASK_SUMMARY_CHARS = 700

EVENT_TYPE_MESSAGE_RECEIVE = "im.message.receive_v1"
EVENT_TYPE_URL_VERIFICATION = "url_verification"

_state_lock = threading.Lock()


@dataclass
class FeishuInboundMessage:
    event_id: str
    event_type: str
    chat_id: str | None
    message_id: str | None
    sender_id: str | None
    text: str
    raw: dict[str, Any]


@dataclass
class FeishuWorkerSelection:
    worker_name: str
    task_text: str
    source: str


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"events": {}, "task_links": {}, "chat_history": {}}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"events": {}, "task_links": {}, "chat_history": {}}
    if not isinstance(data, dict):
        return {"events": {}, "task_links": {}, "chat_history": {}}
    data.setdefault("events", {})
    data.setdefault("task_links", {})
    data.setdefault("chat_history", {})
    return data


def _save_state_locked(data: dict[str, Any]) -> None:
    tmp_path = STATE_FILE.with_suffix(f"{STATE_FILE.suffix}.{os.getpid()}.tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, STATE_FILE)


def _header(payload: dict[str, Any]) -> dict[str, Any]:
    header = payload.get("header")
    return header if isinstance(header, dict) else {}


def _payload_token(payload: dict[str, Any]) -> str:
    header = _header(payload)
    return str(header.get("token") or payload.get("token") or "")


def verify_event_token(payload: dict[str, Any], expected_token: str | None = None) -> bool:
    expected = (expected_token if expected_token is not None else os.environ.get("FEISHU_EVENT_VERIFICATION_TOKEN", "")).strip()
    if not expected:
        return True
    return _payload_token(payload) == expected


def is_url_verification(payload: dict[str, Any]) -> bool:
    header = _header(payload)
    return (
        payload.get("type") == EVENT_TYPE_URL_VERIFICATION
        or header.get("event_type") == EVENT_TYPE_URL_VERIFICATION
    )


def challenge_response(payload: dict[str, Any]) -> dict[str, str]:
    challenge = payload.get("challenge")
    if not isinstance(challenge, str) or not challenge:
        raise ValueError("Feishu URL verification challenge is missing")
    return {"challenge": challenge}


def _event_id(payload: dict[str, Any]) -> str:
    header = _header(payload)
    event_id = header.get("event_id") or payload.get("uuid") or payload.get("event_id")
    if event_id:
        return str(event_id)
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    message_id = message.get("message_id") or event.get("message_id")
    if message_id:
        return f"message:{message_id}"
    raise ValueError("Feishu event id is missing")


def _event_type(payload: dict[str, Any]) -> str:
    header = _header(payload)
    return str(header.get("event_type") or payload.get("type") or "")


def _extract_sender_id(event: dict[str, Any]) -> str | None:
    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    sender_id = sender.get("sender_id") if isinstance(sender.get("sender_id"), dict) else {}
    return (
        sender_id.get("open_id")
        or sender_id.get("user_id")
        or sender_id.get("union_id")
        or sender.get("sender_id")
    )


def _extract_text_from_message(message: dict[str, Any]) -> str:
    message_type = str(message.get("message_type") or message.get("msg_type") or "")
    if message_type and message_type != "text":
        raise ValueError(f"Only text messages are supported, got: {message_type}")
    content = message.get("content") or ""
    parsed: Any = content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            parsed = {"text": content}
    if isinstance(parsed, dict):
        text = parsed.get("text") or parsed.get("content") or ""
    else:
        text = str(parsed)
    text = str(text).strip()
    if not text:
        raise ValueError("Feishu message text is empty")
    if len(text) > MAX_INBOUND_TEXT_CHARS:
        text = text[:MAX_INBOUND_TEXT_CHARS] + f"\n\n... (truncated, original length={len(text)})"
    return text


def parse_inbound_message(payload: dict[str, Any]) -> FeishuInboundMessage:
    if "encrypt" in payload:
        raise ValueError("Encrypted Feishu events are not supported yet; disable encryption or add FEISHU_EVENT_ENCRYPT_KEY support")
    if is_url_verification(payload):
        raise ValueError("URL verification payload is not a message event")

    event_type = _event_type(payload)
    if event_type not in {EVENT_TYPE_MESSAGE_RECEIVE, "event_callback"}:
        raise ValueError(f"Unsupported Feishu event type: {event_type or '(missing)'}")

    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    message = event.get("message") if isinstance(event.get("message"), dict) else event
    text = _extract_text_from_message(message)
    return FeishuInboundMessage(
        event_id=_event_id(payload),
        event_type=event_type,
        chat_id=message.get("chat_id") or event.get("chat_id"),
        message_id=message.get("message_id") or event.get("message_id"),
        sender_id=_extract_sender_id(event),
        text=text,
        raw=payload,
    )


def get_task_id_for_event(event_id: str) -> str | None:
    with _state_lock:
        state = _load_state()
        item = state.get("events", {}).get(event_id)
        if isinstance(item, dict):
            return item.get("task_id")
        return None


def get_event_record(event_id: str) -> dict[str, Any] | None:
    with _state_lock:
        state = _load_state()
        item = state.get("events", {}).get(event_id)
        return dict(item) if isinstance(item, dict) else None


def _shorten(text: Any, max_chars: int) -> str:
    value = str(text or "").strip()
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + f"... (truncated, original length={len(value)})"


def _chat_message_record(message: FeishuInboundMessage) -> dict[str, Any]:
    return {
        "event_id": message.event_id,
        "chat_id": message.chat_id,
        "message_id": message.message_id,
        "sender_id": message.sender_id,
        "text": _shorten(message.text, MAX_CONTEXT_MESSAGE_CHARS),
        "received_at": _now(),
    }


def get_chat_context(chat_id: str | None, limit: int = MAX_CONTEXT_MESSAGES) -> list[dict[str, Any]]:
    if not chat_id:
        return []
    with _state_lock:
        state = _load_state()
        items = state.get("chat_history", {}).get(chat_id, [])
    if not isinstance(items, list):
        return []
    context = [dict(item) for item in items if isinstance(item, dict)]
    return context[-max(0, limit):]


def _append_chat_history_locked(state: dict[str, Any], message: FeishuInboundMessage) -> None:
    if not message.chat_id:
        return
    history = state.setdefault("chat_history", {}).setdefault(message.chat_id, [])
    if not isinstance(history, list):
        history = []
        state["chat_history"][message.chat_id] = history
    if any(item.get("event_id") == message.event_id for item in history if isinstance(item, dict)):
        return
    history.append(_chat_message_record(message))
    del history[:-MAX_CHAT_HISTORY_MESSAGES]


def link_event_to_task(
    message: FeishuInboundMessage,
    task_id: str,
    worker_name: str | None = None,
    worker_selection: str | None = None,
) -> None:
    with _state_lock:
        state = _load_state()
        state["events"][message.event_id] = {
            "event_id": message.event_id,
            "task_id": task_id,
            "chat_id": message.chat_id,
            "message_id": message.message_id,
            "sender_id": message.sender_id,
            "text": message.text,
            "worker_name": worker_name,
            "worker_selection": worker_selection,
            "received_at": _now(),
        }
        state["task_links"][task_id] = {
            "event_id": message.event_id,
            "chat_id": message.chat_id,
            "message_id": message.message_id,
            "sender_id": message.sender_id,
            "reply_sent": False,
            "reply_attempts": 0,
            "reply_result": None,
        }
        _append_chat_history_locked(state, message)
        _save_state_locked(state)


def get_task_link(task_id: str) -> dict[str, Any] | None:
    with _state_lock:
        state = _load_state()
        item = state.get("task_links", {}).get(task_id)
        return dict(item) if isinstance(item, dict) else None


def mark_task_reply(task_id: str, result: dict[str, Any]) -> None:
    with _state_lock:
        state = _load_state()
        link = state.get("task_links", {}).get(task_id)
        if not isinstance(link, dict):
            return
        link["reply_attempts"] = int(link.get("reply_attempts") or 0) + 1
        link["reply_sent"] = bool(result.get("ok"))
        link["reply_result"] = result
        link["reply_at"] = _now()
        _save_state_locked(state)


def _worker_lookup(workers: dict[str, Any]) -> dict[str, str]:
    return {name.lower(): name for name in workers.keys()}


def select_worker_for_text(
    text: str,
    workers: dict[str, Any],
    default_worker: str,
) -> FeishuWorkerSelection:
    raw_text = str(text or "").strip()
    lookup = _worker_lookup(workers)
    default = default_worker if default_worker in workers else next(iter(workers.keys()), default_worker)

    command_match = re.match(r"^/(?:worker|w)\s+([A-Za-z][\w.-]*)\b\s*(.*)$", raw_text, re.IGNORECASE | re.DOTALL)
    if command_match:
        requested = command_match.group(1).lower()
        if requested in lookup:
            task_text = command_match.group(2).strip() or raw_text
            return FeishuWorkerSelection(lookup[requested], task_text, "slash_command")

    mention_match = re.match(r"^[#@]([A-Za-z][\w.-]*)\b[:：]?\s*(.*)$", raw_text, re.DOTALL)
    if mention_match:
        requested = mention_match.group(1).lower()
        if requested in lookup:
            task_text = mention_match.group(2).strip() or raw_text
            return FeishuWorkerSelection(lookup[requested], task_text, "worker_prefix")

    colon_match = re.match(r"^([A-Za-z][\w.-]*)[:：]\s*(.+)$", raw_text, re.DOTALL)
    if colon_match:
        requested = colon_match.group(1).lower()
        if requested in lookup:
            return FeishuWorkerSelection(lookup[requested], colon_match.group(2).strip(), "worker_prefix")

    return FeishuWorkerSelection(default, raw_text, "default")


def build_task_description(
    message: FeishuInboundMessage,
    task_text: str | None = None,
    worker_name: str | None = None,
    chat_context: list[dict[str, Any]] | None = None,
    today_tasks: str | None = None,
) -> str:
    parts = [
        "来自飞书群聊的用户请求，请作为 Agent 任务处理。",
        "",
        "当前用户消息:",
        (task_text if task_text is not None else message.text),
    ]
    if chat_context:
        parts.append("\n近期飞书群聊上下文（同一 chat_id，较早 -> 较新）:")
        for item in chat_context[-MAX_CONTEXT_MESSAGES:]:
            received_at = item.get("received_at") or ""
            sender_id = item.get("sender_id") or "unknown"
            text = _shorten(item.get("text", ""), MAX_CONTEXT_MESSAGE_CHARS)
            if text:
                parts.append(f"- [{received_at}] {sender_id}: {text}")
    if today_tasks:
        parts.append("\n今日 Agent 任务摘要（用于回答“今天做了什么”等问题）:")
        parts.append(today_tasks)
    if worker_name:
        parts.append(f"\n[selected worker: {worker_name}]")
    if message.chat_id:
        parts.append(f"\n[feishu chat_id: {message.chat_id}]")
    if message.message_id:
        parts.append(f"[feishu message_id: {message.message_id}]")
    return "\n".join(parts)


def _task_attr(task: Any, name: str, default: Any = "") -> Any:
    if isinstance(task, dict):
        return task.get(name, default)
    return getattr(task, name, default)


def _task_result_summary(result: Any) -> str:
    raw = str(result or "").strip()
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return _shorten(raw, MAX_TASK_SUMMARY_CHARS)
    if isinstance(parsed, dict):
        return _shorten(parsed.get("summary") or parsed.get("result") or raw, MAX_TASK_SUMMARY_CHARS)
    return _shorten(raw, MAX_TASK_SUMMARY_CHARS)


def _task_description_brief(description: Any) -> str:
    text = str(description or "").strip()
    if "\n今日 Agent 任务摘要" in text:
        text = text.split("\n今日 Agent 任务摘要", 1)[0].strip()
    if "\n近期飞书群聊上下文" in text:
        text = text.split("\n近期飞书群聊上下文", 1)[0].strip()
    if "当前用户消息:" in text:
        text = text.split("当前用户消息:", 1)[1].strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        if not line.startswith("[") and line != "来自飞书群聊的用户请求，请作为 Agent 任务处理。":
            return _shorten(line, 180)
    return _shorten(text, 180)


def summarize_today_tasks(
    tasks: list[Any],
    today: str | None = None,
    limit: int = MAX_TODAY_TASKS,
) -> str:
    target_day = today or datetime.now().strftime("%Y-%m-%d")
    candidates = []
    for task in tasks:
        created_at = str(_task_attr(task, "created_at", "") or "")
        updated_at = str(_task_attr(task, "updated_at", "") or "")
        if not (created_at.startswith(target_day) or updated_at.startswith(target_day)):
            continue
        candidates.append(task)

    if not candidates:
        return ""

    candidates.sort(key=lambda item: str(_task_attr(item, "updated_at", "") or _task_attr(item, "created_at", "")))
    shown = candidates[-max(0, limit):]
    lines = []
    for task in shown:
        task_id = _task_attr(task, "task_id", "")
        status = _task_attr(task, "status", "")
        task_type = _task_attr(task, "type", "")
        worker = _task_attr(task, "worker_name", None) or ("Manager" if task_type == "manager_task" else "system")
        desc = _task_description_brief(_task_attr(task, "description", ""))
        summary = _task_result_summary(_task_attr(task, "result", ""))
        error = _shorten(_task_attr(task, "error", ""), 220)
        tail = summary or error or _shorten(_task_attr(task, "progress", ""), 220)
        line = f"- {task_id} | {status} | {worker} | {desc}"
        if tail:
            line += f" => {tail}"
        lines.append(line)
    if len(candidates) > len(shown):
        lines.insert(0, f"- ... 今日共有 {len(candidates)} 个任务，这里显示最近 {len(shown)} 个。")
    return "\n".join(lines)


def task_reply_text(task: Any) -> str:
    status = getattr(task, "status", "")
    if status == "completed":
        result = getattr(task, "result", "") or ""
        summary = result
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict):
                summary = parsed.get("summary") or parsed.get("result") or result
        except json.JSONDecodeError:
            pass
        return (str(summary).strip() or "处理完成。")[:MAX_TASK_REPLY_CHARS]

    error = getattr(task, "error", "") or getattr(task, "progress", "") or "任务未成功完成"
    prefix = "已取消" if status == "cancelled" else "处理失败"
    return f"{prefix}：{str(error).strip()}"[:MAX_TASK_REPLY_CHARS]


def inbound_status() -> dict[str, Any]:
    with _state_lock:
        state = _load_state()
    chat_history = state.get("chat_history", {})
    history_messages = sum(len(items) for items in chat_history.values() if isinstance(items, list))
    return {
        "enabled": bool(os.environ.get("FEISHU_EVENT_VERIFICATION_TOKEN")),
        "events": len(state.get("events", {})),
        "task_links": len(state.get("task_links", {})),
        "chat_history_chats": len(chat_history) if isinstance(chat_history, dict) else 0,
        "chat_history_messages": history_messages,
        "state_file": str(STATE_FILE),
    }
