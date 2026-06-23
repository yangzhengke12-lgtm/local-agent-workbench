import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy-key")

import manager
from manager import execute_tool, select_worker_model, select_manager_model
from runtime.business_connectors import database_query, ensure_demo_business_db, internal_api_request
from runtime.feishu_connector import build_feishu_text_payload, send_feishu_message


class _FakeHTTPResponse:
    def __init__(self, body='{"code":0,"msg":"success"}', status=200):
        self._body = body.encode("utf-8")
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._body


class TestRuntimeGuardrails(unittest.TestCase):
    def test_system_metadata_is_deterministic(self):
        metadata = manager.get_system_metadata()

        self.assertIn('"version": "v4.2"', metadata)
        self.assertIn('"worker_sessions"', metadata)
        self.assertIn('"task_board"', metadata)
    def test_find_files_is_cross_platform(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = os.path.join(tmp, "pyproject.toml")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("[project]\nversion = '1.2.3'\n")

            result = execute_tool("find_files", {"pattern": "pyproject.toml", "path": tmp})

        self.assertIn("pyproject.toml", result)
        self.assertNotIn("未找到", result)

    def test_search_code_is_cross_platform(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = os.path.join(tmp, "sample.py")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("VERSION = '4.2.0'\n")

            result = execute_tool("search_code", {"pattern": "VERSION", "path": tmp, "file_types": "*.py"})

        self.assertIn("sample.py", result)
        self.assertIn("VERSION", result)

    def test_ls_la_is_normalized_on_windows(self):
        if sys.platform != "win32":
            self.skipTest("Windows-specific command compatibility")

        result = execute_tool("run_command", {"command": "ls -la"})

        self.assertNotIn("不是内部或外部命令", result)
        self.assertTrue(result.strip())

    def test_plain_key_task_does_not_route_to_major(self):
        task = "请查找当前项目的关键版本文件"
        (_, _), complexity, reason = select_worker_model(task, "Alex")
        (_, manager_model), needs_confirm = select_manager_model(task)

        self.assertNotEqual(complexity, "major")
        self.assertFalse(needs_confirm)
        self.assertNotEqual(manager_model, "gpt-5.5")
        self.assertNotIn("重大决策", reason)

    def test_repeated_tool_call_forces_runtime_stop(self):
        messages = [{"role": "user", "content": "fetch once"}]
        blocks = [[{"type": "tool_use", "id": f"tool-{i}", "name": "fetch_url", "input": {"url": "https://example.com"}}] for i in range(4)]

        with patch("runtime.llm.call_llm", side_effect=blocks):
            result = manager.call_llm_multi_turn(
                provider_key="deepseek",
                model_id="deepseek-v4-pro[1M]",
                messages=messages,
                tools=[{"name": "fetch_url", "input_schema": {"type": "object", "properties": {}}}],
                execute_tool_fn=lambda name, args: "HTTP 200\nok",
                max_turns=5,
            )

        self.assertIn("repeated_tool_call_blocked", result)
        self.assertIn("needs_review", result)
    def test_fresh_session_does_not_persist_worker_memory(self):
        manager.worker_sessions.clear()
        worker = {
            "name": "Marcus",
            "role": "DevOps Engineer",
            "tools": [],
            "tool_names": [],
            "model": "deepseek-chat",
        }

        with patch("runtime.workers.call_llm_multi_turn", return_value='{"status":"success","summary":"ok","artifacts":[]}'):
            manager.run_worker(worker, "first task", use_memory=True, fresh_session=True, session_scope="delegate")

        self.assertEqual(manager.worker_sessions, {})

    def test_database_query_reads_demo_business_data(self):
        db_path = ensure_demo_business_db()

        result = database_query(
            "SELECT ticket_id, priority, status FROM tickets WHERE status = 'open'",
            max_rows=5,
        )

        self.assertTrue(os.path.exists(db_path))
        self.assertEqual(result["safety"], "read_only_select")
        self.assertEqual(result["row_count"], 1)
        self.assertEqual(result["rows"][0]["ticket_id"], "ticket_9001")

    def test_database_query_blocks_write_sql(self):
        with self.assertRaises(ValueError):
            database_query("DELETE FROM tickets")

        with self.assertRaises(ValueError):
            database_query("SELECT * FROM tickets; DROP TABLE tickets")

    def test_internal_api_request_uses_demo_allowlist(self):
        result = internal_api_request("GET", "/tickets/ticket_9001")

        self.assertEqual(result["source"], "demo_internal_api")
        self.assertEqual(result["data"]["ticket_id"], "ticket_9001")
        self.assertIn("recommended_action", result["data"])

    def test_internal_api_request_rejects_unlisted_path(self):
        with self.assertRaises(ValueError):
            internal_api_request("GET", "/admin/secrets")

    def test_business_connectors_are_registered_tools(self):
        db_result = execute_tool("database_query", {
            "query": "SELECT customer_id, tier FROM customers WHERE tier = 'enterprise'",
            "max_rows": 10,
        })
        api_result = execute_tool("internal_api_request", {"path": "/orders/ord_1001"})

        self.assertIn("cust_001", db_result)
        self.assertIn("read_only_select", db_result)
        self.assertIn("ord_1001", api_result)
        self.assertIn("demo_internal_api", api_result)

    def test_feishu_payload_supports_optional_signature(self):
        payload = build_feishu_text_payload(
            "部署完成",
            title="Agent 通知",
            secret="test-secret",
            timestamp=100,
        )

        self.assertEqual(payload["msg_type"], "text")
        self.assertEqual(payload["timestamp"], "100")
        self.assertIn("sign", payload)
        self.assertIn("[Agent 通知]", payload["content"]["text"])

    def test_feishu_send_message_requires_configured_webhook(self):
        with patch.dict(os.environ, {"FEISHU_WEBHOOK_URL": "", "FEISHU_WEBHOOK_SECRET": ""}, clear=False):
            with self.assertRaises(ValueError) as ctx:
                send_feishu_message("hello")

        self.assertIn("FEISHU_WEBHOOK_URL", str(ctx.exception))

    def test_feishu_send_message_posts_to_configured_webhook(self):
        webhook = "https://open.feishu.cn/open-apis/bot/v2/hook/test-token"
        captured = {}

        def fake_urlopen(req, timeout=10):
            captured["url"] = req.full_url
            captured["timeout"] = timeout
            captured["body"] = req.data.decode("utf-8")
            captured["content_type"] = req.headers.get("Content-type")
            return _FakeHTTPResponse()

        with patch.dict(os.environ, {"FEISHU_WEBHOOK_URL": webhook, "FEISHU_WEBHOOK_SECRET": ""}, clear=False):
            with patch("runtime.feishu_connector.urllib.request.urlopen", side_effect=fake_urlopen):
                result = send_feishu_message("任务完成", title="Workbench")

        self.assertTrue(result["ok"])
        self.assertEqual(captured["url"], webhook)
        self.assertIn("任务完成", captured["body"])
        self.assertIn("Workbench", captured["body"])

    def test_feishu_tool_is_registered(self):
        self.assertIn("feishu_send_message", manager.ALL_TOOLS)

        with patch("runtime.tools.send_feishu_message", return_value={"ok": True, "status": 200}):
            result = execute_tool("feishu_send_message", {"text": "日报已生成", "title": "Agent"})

        self.assertIn('"ok": true', result.lower())


if __name__ == "__main__":
    unittest.main()
