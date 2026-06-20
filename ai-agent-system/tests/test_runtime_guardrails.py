import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy-key")

import manager
from manager import execute_tool, select_worker_model, select_manager_model


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


if __name__ == "__main__":
    unittest.main()