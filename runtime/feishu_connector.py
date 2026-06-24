"""Feishu/Lark custom bot webhook connector.

This is an outbound-only integration:
- The webhook URL and optional signing secret are read from environment variables.
- Agent tool calls can only provide message content, not arbitrary webhook URLs.
- It supports both Feishu and Lark custom bot webhook hosts.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from dotenv import load_dotenv


load_dotenv()


MAX_TEXT_CHARS = 4000
ALLOWED_WEBHOOK_PREFIXES = (
    "https://open.feishu.cn/open-apis/bot/v2/hook/",
    "https://open.larksuite.com/open-apis/bot/v2/hook/",
)
FEISHU_API_BASE_URL = "https://open.feishu.cn/open-apis"
LARK_API_BASE_URL = "https://open.larksuite.com/open-apis"


def _truncate_text(text: str) -> str:
    if len(text) <= MAX_TEXT_CHARS:
        return text
    return text[:MAX_TEXT_CHARS] + f"\n\n... (truncated, original length={len(text)})"


def _build_feishu_signature(timestamp: int, secret: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _validate_webhook_url(webhook_url: str) -> str:
    url = (webhook_url or "").strip()
    if not url:
        raise ValueError("FEISHU_WEBHOOK_URL is not configured")
    if not any(url.startswith(prefix) for prefix in ALLOWED_WEBHOOK_PREFIXES):
        raise ValueError(
            "FEISHU_WEBHOOK_URL must be a Feishu/Lark custom bot webhook URL "
            "(https://open.feishu.cn/open-apis/bot/v2/hook/...)"
        )
    return url


def build_feishu_text_payload(
    text: str,
    title: str | None = None,
    secret: str | None = None,
    timestamp: int | None = None,
) -> dict[str, Any]:
    """Build the JSON payload accepted by Feishu/Lark custom bots."""
    message = (text or "").strip()
    if not message:
        raise ValueError("Feishu message text is empty")

    title = (title or "").strip()
    if title:
        message = f"[{title}]\n{message}"
    message = _truncate_text(message)

    payload: dict[str, Any] = {
        "msg_type": "text",
        "content": {"text": message},
    }

    if secret:
        ts = int(timestamp if timestamp is not None else time.time())
        payload["timestamp"] = str(ts)
        payload["sign"] = _build_feishu_signature(ts, secret)

    return payload


def send_feishu_message(
    text: str,
    title: str | None = None,
    webhook_url: str | None = None,
    secret: str | None = None,
    timeout: int = 10,
) -> dict[str, Any]:
    """Send a text message to the configured Feishu/Lark custom bot."""
    url = _validate_webhook_url(webhook_url or os.environ.get("FEISHU_WEBHOOK_URL", ""))
    signing_secret = secret if secret is not None else os.environ.get("FEISHU_WEBHOOK_SECRET", "")
    payload = build_feishu_text_payload(text, title=title, secret=signing_secret or None)

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "LocalAgentWorkbench/1.0",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            response_body = resp.read().decode("utf-8", errors="replace")
            try:
                parsed: Any = json.loads(response_body) if response_body else {}
            except json.JSONDecodeError:
                parsed = response_body
            ok = True
            if isinstance(parsed, dict) and parsed.get("code") not in (None, 0):
                ok = False
            return {
                "ok": ok,
                "provider": "feishu_custom_bot",
                "status": resp.status,
                "response": parsed,
            }
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "provider": "feishu_custom_bot",
            "status": e.code,
            "error": error_body or str(e),
        }
    except urllib.error.URLError as e:
        return {
            "ok": False,
            "provider": "feishu_custom_bot",
            "error": str(e),
        }


def _api_base_url() -> str:
    configured = os.environ.get("FEISHU_API_BASE_URL", "").strip()
    if configured:
        return configured.rstrip("/")
    if os.environ.get("FEISHU_APP_USE_LARK_HOST", "").strip().lower() in {"1", "true", "yes"}:
        return LARK_API_BASE_URL
    return FEISHU_API_BASE_URL


def get_tenant_access_token(
    app_id: str | None = None,
    app_secret: str | None = None,
    timeout: int = 10,
) -> str:
    """Fetch a tenant_access_token for Feishu/Lark app APIs."""
    resolved_app_id = (app_id or os.environ.get("FEISHU_APP_ID", "")).strip()
    resolved_secret = (app_secret or os.environ.get("FEISHU_APP_SECRET", "")).strip()
    if not resolved_app_id or not resolved_secret:
        raise ValueError("FEISHU_APP_ID and FEISHU_APP_SECRET are required for app replies")

    url = f"{_api_base_url()}/auth/v3/tenant_access_token/internal"
    body = json.dumps(
        {"app_id": resolved_app_id, "app_secret": resolved_secret},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        response_body = resp.read().decode("utf-8", errors="replace")
    parsed = json.loads(response_body) if response_body else {}
    if parsed.get("code") not in (None, 0):
        raise ValueError(f"Feishu token request failed: {parsed}")
    token = parsed.get("tenant_access_token")
    if not token:
        raise ValueError("Feishu token response did not include tenant_access_token")
    return token


def send_feishu_app_message(
    chat_id: str,
    text: str,
    title: str | None = None,
    token: str | None = None,
    timeout: int = 10,
) -> dict[str, Any]:
    """Send a text message to the chat that triggered an inbound event.

    This requires a Feishu/Lark app with message send permission and app
    credentials in FEISHU_APP_ID / FEISHU_APP_SECRET.
    """
    resolved_chat_id = (chat_id or "").strip()
    if not resolved_chat_id:
        raise ValueError("chat_id is required for Feishu app replies")
    access_token = token or get_tenant_access_token(timeout=timeout)
    payload = {
        "receive_id": resolved_chat_id,
        "msg_type": "text",
        "content": json.dumps(
            build_feishu_text_payload(text, title=title)["content"],
            ensure_ascii=False,
        ),
    }
    url = f"{_api_base_url()}/im/v1/messages?receive_id_type=chat_id"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "LocalAgentWorkbench/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            response_body = resp.read().decode("utf-8", errors="replace")
            parsed: Any = json.loads(response_body) if response_body else {}
            ok = True
            if isinstance(parsed, dict) and parsed.get("code") not in (None, 0):
                ok = False
            return {
                "ok": ok,
                "provider": "feishu_app_message",
                "status": resp.status,
                "chat_id": resolved_chat_id,
                "response": parsed,
            }
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "provider": "feishu_app_message",
            "status": e.code,
            "chat_id": resolved_chat_id,
            "error": error_body or str(e),
        }
    except urllib.error.URLError as e:
        return {
            "ok": False,
            "provider": "feishu_app_message",
            "chat_id": resolved_chat_id,
            "error": str(e),
        }


def send_feishu_task_reply(
    text: str,
    chat_id: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """Reply to Feishu using app chat messages, falling back to custom bot."""
    if chat_id and os.environ.get("FEISHU_APP_ID") and os.environ.get("FEISHU_APP_SECRET"):
        return send_feishu_app_message(chat_id, text, title=title)
    return send_feishu_message(text, title=title)
