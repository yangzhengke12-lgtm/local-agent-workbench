import os
import json
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy-key")

import manager
from manager import execute_tool, select_worker_model, select_manager_model
from runtime.business_connectors import database_query, ensure_demo_business_db, internal_api_request
from runtime.feishu_connector import (
    build_feishu_text_payload,
    get_tenant_access_token,
    send_feishu_app_message,
    send_feishu_message,
)
from runtime.feishu_inbound import (
    build_task_description,
    challenge_response,
    is_url_verification,
    parse_inbound_message,
    summarize_workspace_changes,
    summarize_today_tasks,
    select_worker_for_text,
    task_reply_text,
    verify_event_token,
)


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

    def test_model_routing_uses_configured_gpt_when_deepseek_is_absent(self):
        old_providers = dict(manager.PROVIDERS)
        try:
            manager.PROVIDERS.clear()
            manager.PROVIDERS["gpt"] = {"type": "openai"}

            (provider, model_id), _, _ = select_worker_model("hello from feishu", "Elena")
            (verifier_provider, _), _, _ = select_worker_model(
                "verify feishu task",
                "Sophia",
                is_verifier=True,
            )
            (retry_provider, _), _, _ = select_worker_model(
                "retry failed feishu task",
                "Alex",
                previous_failures=2,
            )

            self.assertEqual((provider, model_id), ("gpt", "gpt-5.4"))
            self.assertEqual(verifier_provider, "gpt")
            self.assertEqual(retry_provider, "gpt")
        finally:
            manager.PROVIDERS.clear()
            manager.PROVIDERS.update(old_providers)

    def test_repeated_tool_call_forces_runtime_stop(self):
        messages = [{"role": "user", "content": "fetch once"}]
        blocks = [
            [{"type": "tool_use", "id": "tool-1", "name": "fetch_url", "input": {"url": "https://example.com"}}],
            [{"type": "tool_use", "id": "tool-2", "name": "fetch_url", "input": {"url": "https://example.com"}}],
            [{"type": "tool_use", "id": "tool-3", "name": "fetch_url", "input": {"url": "https://example.com"}}],
            [{"type": "text", "text": '{"status":"needs_review","summary":"stopped","artifacts":[]}'}],
        ]

        with patch("runtime.llm.call_llm", side_effect=blocks):
            result = manager.call_llm_multi_turn(
                provider_key="deepseek",
                model_id="deepseek-v4-pro[1M]",
                messages=messages,
                tools=[{"name": "fetch_url", "input_schema": {"type": "object", "properties": {}}}],
                execute_tool_fn=lambda name, args: "HTTP 200\nok",
                max_turns=5,
            )

        self.assertIn("needs_review", result)

    def test_second_duplicate_tool_call_returns_cached_result_hint(self):
        messages = [{"role": "user", "content": "inspect once"}]
        blocks = [
            [{"type": "tool_use", "id": "tool-1", "name": "fetch_url", "input": {"url": "https://example.com"}}],
            [{"type": "tool_use", "id": "tool-2", "name": "fetch_url", "input": {"url": "https://example.com"}}],
            [{"type": "text", "text": "done"}],
        ]

        seen_results = []
        tools_seen_by_model = []

        def fake_tool(name, args):
            seen_results.append((name, args))
            return "HTTP 200\\nok"

        def fake_call_llm(provider_key, model_id, messages, system_prompt="", tools=None, **kwargs):
            tools_seen_by_model.append([tool.get("name") for tool in (tools or [])])
            return blocks.pop(0)

        with patch("runtime.llm.call_llm", side_effect=fake_call_llm):
            result = manager.call_llm_multi_turn(
                provider_key="deepseek",
                model_id="deepseek-v4-pro[1M]",
                messages=messages,
                tools=[{"name": "fetch_url", "input_schema": {"type": "object", "properties": {}}}],
                execute_tool_fn=fake_tool,
                max_turns=5,
            )

        self.assertEqual(len(seen_results), 1)
        self.assertEqual(result, "done")
        self.assertEqual(tools_seen_by_model[0], ["fetch_url"])
        self.assertEqual(tools_seen_by_model[-1], [])

    def test_duplicate_tool_call_restarts_turn_before_third_repeat(self):
        messages = [{"role": "user", "content": "inspect once"}]
        blocks = [
            [
                {"type": "tool_use", "id": "tool-1", "name": "fetch_url", "input": {"url": "https://example.com"}},
                {"type": "tool_use", "id": "tool-2", "name": "fetch_url", "input": {"url": "https://example.com"}},
                {"type": "tool_use", "id": "tool-3", "name": "fetch_url", "input": {"url": "https://example.com"}},
            ],
            [{"type": "text", "text": "final"}],
        ]

        seen_results = []

        def fake_tool(name, args):
            seen_results.append((name, args))
            return "HTTP 200\\nok"

        with patch("runtime.llm.call_llm", side_effect=blocks):
            result = manager.call_llm_multi_turn(
                provider_key="deepseek",
                model_id="deepseek-v4-pro[1M]",
                messages=messages,
                tools=[{"name": "fetch_url", "input_schema": {"type": "object", "properties": {}}}],
                execute_tool_fn=fake_tool,
                max_turns=5,
            )

        self.assertEqual(len(seen_results), 1)
        self.assertEqual(result, "final")

    def test_disabled_tool_is_not_executed_again(self):
        messages = [{"role": "user", "content": "inspect once"}]
        blocks = [
            [{"type": "tool_use", "id": "tool-1", "name": "fetch_url", "input": {"url": "https://example.com"}}],
            [{"type": "tool_use", "id": "tool-2", "name": "fetch_url", "input": {"url": "https://example.com"}}],
            [{"type": "tool_use", "id": "tool-3", "name": "fetch_url", "input": {"url": "https://example.com"}}],
            [{"type": "text", "text": "final"}],
        ]

        seen_results = []

        def fake_tool(name, args):
            seen_results.append((name, args))
            return "HTTP 200\\nok"

        with patch("runtime.llm.call_llm", side_effect=blocks):
            result = manager.call_llm_multi_turn(
                provider_key="deepseek",
                model_id="deepseek-v4-pro[1M]",
                messages=messages,
                tools=[{"name": "fetch_url", "input_schema": {"type": "object", "properties": {}}}],
                execute_tool_fn=fake_tool,
                max_turns=6,
            )

        self.assertEqual(len(seen_results), 1)
        self.assertEqual(result, "final")
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

    def test_git_inspect_report_mode_returns_compound_summary(self):
        result = execute_tool("git_inspect", {"mode": "report", "limit": 3})

        self.assertIn("[git status]", result)
        self.assertIn("[git diff --stat]", result)
        self.assertIn("[recent commits]", result)

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

    def test_run_worker_fallback_sends_feishu_report(self):
        worker = {
            "name": "Elena",
            "role": "Technical Writer",
            "tools": [
                manager.ALL_TOOLS["git_inspect"],
                manager.ALL_TOOLS["feishu_send_message"],
            ],
            "tool_names": ["git_inspect", "feishu_send_message"],
            "model": "deepseek-chat",
        }

        fake_report = (
            "[git status]\n"
            "M README.md\n"
            " M desktop/main.js\n"
            " M runtime/agent_task.py\n"
            " M runtime/tools.py\n"
            " M tests/test_agent_api.py\n\n"
            "[git diff --stat]\n"
            "README.md | 10 +++++\n\n"
            "[recent commits]\n"
            "abc123 fix desktop backend restart\n"
        )

        def fake_exec(name, args):
            if name == "git_inspect":
                return fake_report
            if name == "feishu_send_message":
                return '{"ok": true, "status": 200, "msg": "success"}'
            return f"unexpected tool {name}"

        with patch("runtime.workers.call_llm_multi_turn", return_value='{"status":"needs_review","summary":"Runtime guard stopped tool execution.","artifacts":[]}'):
            with patch("runtime.workers.execute_tool", side_effect=fake_exec):
                result = manager.run_worker(
                    worker,
                    "请整理日报并发送到飞书",
                    use_memory=False,
                    fresh_session=True,
                )

        payload = json.loads(result["result"])
        self.assertEqual(payload["status"], "success")
        self.assertTrue(payload["delivery"]["ok"])
        self.assertIn("飞书发送：成功", payload["summary"])


class TestFeishuBidirectional(unittest.TestCase):
    def test_feishu_task_reply_text_is_clean_for_chat(self):
        completed = SimpleNamespace(
            status="completed",
            task_id="task_20260624_232952_084485",
            result=json.dumps({"summary": "clean reply"}, ensure_ascii=False),
            error="",
            progress="",
        )
        failed = SimpleNamespace(
            status="failed",
            task_id="task_20260624_232952_084485",
            result="",
            error="model failed",
            progress="",
        )

        completed_text = task_reply_text(completed)
        failed_text = task_reply_text(failed)

        self.assertEqual(completed_text, "clean reply")
        for text in (completed_text, failed_text):
            self.assertNotIn("Agent Task Result", text)
            self.assertNotIn("task_20260624_232952_084485", text)
            self.assertNotIn("\u4efb\u52a1ID", text)
            self.assertNotIn("\u4efb\u52a1\u5df2\u5b8c\u6210", text)

    def test_feishu_worker_selection_from_message_prefix(self):
        workers = {"Alex": {}, "Sophia": {}, "Elena": {}}

        slash = select_worker_for_text("/worker Alex fix the failing test", workers, "Elena")
        mention = select_worker_for_text("@Sophia review this diff", workers, "Elena")
        default = select_worker_for_text("summarize today's progress", workers, "Elena")

        self.assertEqual((slash.worker_name, slash.task_text, slash.source), ("Alex", "fix the failing test", "slash_command"))
        self.assertEqual((mention.worker_name, mention.task_text, mention.source), ("Sophia", "review this diff", "worker_prefix"))
        self.assertEqual((default.worker_name, default.source), ("Elena", "default"))

    def test_feishu_event_url_verification(self):
        payload = {
            "type": "url_verification",
            "token": "verify-token",
            "challenge": "challenge-value",
        }

        self.assertTrue(is_url_verification(payload))
        self.assertTrue(verify_event_token(payload, "verify-token"))
        self.assertEqual(challenge_response(payload), {"challenge": "challenge-value"})

    def test_feishu_inbound_message_parsing(self):
        payload = {
            "schema": "2.0",
            "header": {
                "event_id": "evt_001",
                "event_type": "im.message.receive_v1",
                "token": "verify-token",
            },
            "event": {
                "sender": {"sender_id": {"open_id": "ou_1"}},
                "message": {
                    "message_id": "om_1",
                    "chat_id": "oc_1",
                    "message_type": "text",
                    "content": json.dumps({"text": "请总结今天的项目进展"}, ensure_ascii=False),
                },
            },
        }

        message = parse_inbound_message(payload)

        self.assertEqual(message.event_id, "evt_001")
        self.assertEqual(message.chat_id, "oc_1")
        self.assertEqual(message.message_id, "om_1")
        self.assertEqual(message.text, "请总结今天的项目进展")
        self.assertIn("请总结今天的项目进展", build_task_description(message))

    def test_feishu_task_description_includes_chat_context_and_today_work(self):
        message = parse_inbound_message({
            "schema": "2.0",
            "header": {
                "event_id": "evt_context_002",
                "event_type": "im.message.receive_v1",
                "token": "verify-token",
            },
            "event": {
                "sender": {"sender_id": {"open_id": "ou_2"}},
                "message": {
                    "message_id": "om_context_002",
                    "chat_id": "oc_context",
                    "message_type": "text",
                    "content": json.dumps({"text": "总结今天干了什么"}, ensure_ascii=False),
                },
            },
        })
        today_tasks = summarize_today_tasks(
            [
                SimpleNamespace(
                    task_id="task_today_context",
                    type="manager_task",
                    status="completed",
                    description="接入飞书双向消息并修复回填格式",
                    worker_name=None,
                    result=json.dumps({"summary": "完成飞书双向接入、Manager 默认流和干净回填"}, ensure_ascii=False),
                    error="",
                    created_at="2026-06-25 09:00:00",
                    updated_at="2026-06-25 10:00:00",
                )
            ],
            today="2026-06-25",
        )

        description = build_task_description(
            message,
            chat_context=[
                {
                    "received_at": "2026-06-25 09:30:00",
                    "sender_id": "ou_1",
                    "text": "我们已经完成了飞书事件订阅和 GitHub 同步",
                }
            ],
            today_tasks=today_tasks,
            workspace_changes="[git status --short]\nM runtime/feishu_inbound.py",
        )

        self.assertIn("近期飞书群聊上下文", description)
        self.assertIn("我们已经完成了飞书事件订阅和 GitHub 同步", description)
        self.assertIn("今日 Agent 任务摘要", description)
        self.assertIn("完成飞书双向接入", description)
        self.assertIn("本地工作区变更摘要", description)
        self.assertIn("M runtime/feishu_inbound.py", description)

    def test_summarize_workspace_changes_reports_local_git_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess_env = os.environ.copy()
            subprocess_env.setdefault("PYTHONUTF8", "1")
            subprocess.run(["git", "init"], cwd=tmp, check=True, capture_output=True, text=True, env=subprocess_env)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp, check=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp, check=True)
            readme = os.path.join(tmp, "README.md")
            with open(readme, "w", encoding="utf-8") as f:
                f.write("hello\n")
            subprocess.run(["git", "add", "README.md"], cwd=tmp, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp, check=True, capture_output=True, text=True, env=subprocess_env)
            with open(readme, "a", encoding="utf-8") as f:
                f.write("today\n")

            summary = summarize_workspace_changes(tmp)

        self.assertIn("[git status --short]", summary)
        self.assertIn("README.md", summary)
        self.assertIn("[git diff --stat]", summary)

    def test_feishu_app_message_uses_chat_id(self):
        captured = {}

        def fake_urlopen(req, timeout=10):
            captured.setdefault("urls", []).append(req.full_url)
            body = req.data.decode("utf-8")
            captured.setdefault("bodies", []).append(body)
            if req.full_url.endswith("/auth/v3/tenant_access_token/internal"):
                return _FakeHTTPResponse('{"code":0,"tenant_access_token":"tenant-token"}')
            return _FakeHTTPResponse('{"code":0,"data":{"message_id":"om_reply"}}')

        with patch.dict(
            os.environ,
            {
                "FEISHU_APP_ID": "cli_test",
                "FEISHU_APP_SECRET": "secret",
                "FEISHU_API_BASE_URL": "https://open.feishu.cn/open-apis",
            },
            clear=False,
        ):
            with patch("runtime.feishu_connector.urllib.request.urlopen", side_effect=fake_urlopen):
                token = get_tenant_access_token()
                result = send_feishu_app_message("oc_1", "任务完成", title="Agent", token=token)

        self.assertEqual(token, "tenant-token")
        self.assertTrue(result["ok"])
        self.assertIn("receive_id_type=chat_id", captured["urls"][-1])
        self.assertIn('"receive_id": "oc_1"', captured["bodies"][-1])
        self.assertIn("任务完成", captured["bodies"][-1])


if __name__ == "__main__":
    unittest.main()
