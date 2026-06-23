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
