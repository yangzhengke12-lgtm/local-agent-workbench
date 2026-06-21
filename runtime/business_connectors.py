"""Business connector adapters for safe, demo-ready integrations.

The first version intentionally stays local-first:
- database_query uses a bundled SQLite demo database and only allows SELECT.
- internal_api_request allows a small set of whitelisted paths and can run
  against a local JSON mock when no real INTERNAL_API_BASE_URL is configured.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_DB_PATH = REPO_ROOT / "examples" / "demo_business.db"
DEMO_SQL_PATH = REPO_ROOT / "examples" / "demo_business.sql"
DEMO_API_PATH = REPO_ROOT / "examples" / "internal_api_demo.json"

MAX_DB_ROWS = 50
MAX_API_CHARS = 6000
BLOCKED_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|pragma|"
    r"vacuum|reindex|truncate|grant|revoke)\b",
    re.IGNORECASE,
)
ALLOWED_API_PATTERNS = [
    re.compile(r"^/tickets/[A-Za-z0-9_-]+$"),
    re.compile(r"^/orders/[A-Za-z0-9_-]+$"),
    re.compile(r"^/customers/[A-Za-z0-9_-]+$"),
    re.compile(r"^/metrics/daily$"),
]


def _truncate(text: str, max_chars: int = MAX_API_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n... (truncated, original length={len(text)})"


def ensure_demo_business_db(db_path: str | os.PathLike[str] | None = None) -> Path:
    """Create the demo SQLite DB from SQL seed if it does not exist."""
    path = Path(db_path) if db_path else DEMO_DB_PATH
    if path.exists():
        return path
    if not DEMO_SQL_PATH.exists():
        raise FileNotFoundError(f"Demo SQL seed not found: {DEMO_SQL_PATH}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(DEMO_SQL_PATH.read_text(encoding="utf-8"))
    return path


def _normalize_select(sql: str) -> str:
    query = (sql or "").strip()
    if not query:
        raise ValueError("SQL query is empty")
    if query.endswith(";"):
        query = query[:-1].strip()
    if ";" in query:
        raise ValueError("Only one SELECT statement is allowed")
    lowered = query.lower()
    first = lowered.split(None, 1)[0] if lowered.split(None, 1) else ""
    if first not in {"select", "with"}:
        raise ValueError("Only SELECT queries are allowed")
    if BLOCKED_SQL.search(lowered):
        raise ValueError("Write/admin SQL keywords are not allowed")
    return query


def database_query(query: str, max_rows: int = 20, db_path: str | None = None) -> dict[str, Any]:
    """Run a safe read-only query against the demo business SQLite database."""
    safe_query = _normalize_select(query)
    row_limit = max(1, min(int(max_rows or 20), MAX_DB_ROWS))
    path = ensure_demo_business_db(db_path)

    uri = f"file:{path.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(safe_query)
        rows = cur.fetchmany(row_limit + 1)
        columns = [desc[0] for desc in cur.description] if cur.description else []

    truncated = len(rows) > row_limit
    shown = rows[:row_limit]
    return {
        "database": str(path),
        "columns": columns,
        "rows": [dict(row) for row in shown],
        "row_count": len(shown),
        "truncated": truncated,
        "safety": "read_only_select",
    }


def _allowed_api_path(path: str) -> bool:
    return any(pattern.match(path) for pattern in ALLOWED_API_PATTERNS)


def _load_demo_api() -> dict[str, Any]:
    if not DEMO_API_PATH.exists():
        raise FileNotFoundError(f"Demo API data not found: {DEMO_API_PATH}")
    return json.loads(DEMO_API_PATH.read_text(encoding="utf-8"))


def _demo_api_response(path: str) -> dict[str, Any]:
    data = _load_demo_api()
    parts = path.strip("/").split("/")
    if path == "/metrics/daily":
        return {"source": "demo_internal_api", "path": path, "data": data.get("metrics", {}).get("daily", [])}
    if len(parts) == 2:
        collection, item_id = parts
        bucket = data.get(collection, {})
        if item_id in bucket:
            return {"source": "demo_internal_api", "path": path, "data": bucket[item_id]}
    return {"source": "demo_internal_api", "path": path, "data": None, "error": "not_found"}


def internal_api_request(method: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Call a whitelisted internal API path or return the local demo response."""
    method = (method or "GET").upper()
    if method != "GET":
        raise ValueError("Only GET is allowed in the demo connector")
    if not path.startswith("/"):
        path = "/" + path
    if not _allowed_api_path(path):
        raise ValueError(f"Path is not in the connector allowlist: {path}")

    base_url = os.environ.get("INTERNAL_API_BASE_URL", "").strip()
    if not base_url:
        return _demo_api_response(path)

    query = urllib.parse.urlencode(params or {}, doseq=True)
    url = base_url.rstrip("/") + path + (f"?{query}" if query else "")
    token = os.environ.get("INTERNAL_API_TOKEN", "").strip()
    headers = {"User-Agent": "LocalAgentWorkbench/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            content_type = resp.headers.get("content-type", "")
            parsed: Any
            if "json" in content_type:
                try:
                    parsed = json.loads(body)
                except json.JSONDecodeError:
                    parsed = _truncate(body)
            else:
                parsed = _truncate(body)
            return {"source": "internal_api", "status": resp.status, "path": path, "data": parsed}
    except urllib.error.HTTPError as e:
        return {"source": "internal_api", "status": e.code, "path": path, "error": str(e)}
    except urllib.error.URLError as e:
        return {"source": "internal_api", "path": path, "error": str(e)}
