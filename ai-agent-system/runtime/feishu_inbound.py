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
        return {"events": {}, "task_links": {}}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"events": {}, "task_links": {}}
    if not isinstance(data, dict):
        return {"events": {}, "task_links": {}}
    data.setdefault("events", {})
    data.setdefault("task_links", {})
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
) -> str:
    parts = [
        "来自飞书群聊的用户请求，请作为 Agent 任务处理。",
        "",
        (task_text if task_text is not None else message.text),
    ]
    if worker_name:
        parts.append(f"\n[selected worker: {worker_name}]")
    if message.chat_id:
        parts.append(f"\n[feishu chat_id: {message.chat_id}]")
    if message.message_id:
        parts.append(f"[feishu message_id: {message.message_id}]")
    return "\n".join(parts)


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
    return {
        "enabled": bool(os.environ.get("FEISHU_EVENT_VERIFICATION_TOKEN")),
        "events": len(state.get("events", {})),
        "task_links": len(state.get("task_links", {})),
        "state_file": str(STATE_FILE),
    }
