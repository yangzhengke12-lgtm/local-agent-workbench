"""Agent Task API 测试 —— HTTP 接口 + 数据模型 + 验证逻辑。"""
import os
import sys
import tempfile
import unittest
import json
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy-key")

from fastapi.testclient import TestClient

# 导入 server 的 app（会触发 manager 和 agent_task 的模块级初始化）
from server import app

from runtime.agent_task import (
    AgentTask,
    TaskStore,
    TaskValidationError,
    validate_create_task,
    _generate_task_id,
    _extract_project_name_from_setup_result,
    VALID_TASK_TYPES,
)
from runtime.config import MissingProviderClient, get_default_client
import server

client = TestClient(app)


class TestTaskDataModel(unittest.TestCase):
    """纯数据模型测试 — 不需要 HTTP。"""

    def test_agent_task_defaults(self):
        task = AgentTask(
            task_id="task_test_001",
            type="worker_task",
            description="测试任务",
        )
        self.assertEqual(task.status, "pending")
        self.assertEqual(task.logs, [])
        self.assertEqual(task.artifacts, [])
        self.assertIsNone(task.result)
        self.assertIsNone(task.error)
        self.assertTrue(task.created_at)
        self.assertTrue(task.updated_at)
        self.assertFalse(task.cancel_requested)

    def test_generate_task_id_is_unique(self):
        ids = {_generate_task_id() for _ in range(100)}
        self.assertEqual(len(ids), 100, "100 个 task_id 应该全部唯一")

    def test_task_id_format(self):
        tid = _generate_task_id()
        self.assertTrue(tid.startswith("task_"), f"task_id 应以 task_ 开头: {tid}")
        # task_YYYYMMDD_HHMMSS_xxxxxx
        parts = tid.split("_")
        self.assertEqual(len(parts), 4, f"task_id 应有 4 段: {tid}")

    def test_task_to_dict_serializable(self):
        import json
        task = AgentTask(
            task_id="task_json_001",
            type="worker_task",
            description="JSON 序列化测试",
            worker_name="Alex",
        )
        d = task.__dict__
        # 不应抛异常
        s = json.dumps(d, ensure_ascii=False, default=str)
        self.assertIn("task_json_001", s)

    def test_extract_project_name_from_real_setup_report(self):
        report = """
=======================================================
  项目分工表: TodoWorkbench
=======================================================

[Pipeline 步骤]
  [ ] 实现接口: ...

状态文件: project_states/TodoWorkbench_state.json
=======================================================
"""
        self.assertEqual(
            _extract_project_name_from_setup_result(report),
            "TodoWorkbench",
        )


class TestInputValidation(unittest.TestCase):
    """输入校验 — 同步函数，无 IO。"""

    def setUp(self):
        self.workers = {
            "Alex": {"name": "Alex", "role": "dev"},
            "Sophia": {"name": "Sophia", "role": "reviewer"},
        }

    def test_valid_worker_task_passes(self):
        validate_create_task("worker_task", "do something", "Alex", self.workers)

    def test_invalid_task_type_raises(self):
        with self.assertRaises(TaskValidationError) as ctx:
            validate_create_task("bad_type", "do something", "Alex", self.workers)
        self.assertIn("bad_type", str(ctx.exception))

    def test_empty_description_raises(self):
        with self.assertRaises(TaskValidationError) as ctx:
            validate_create_task("worker_task", "", "Alex", self.workers)
        self.assertIn("不能为空", str(ctx.exception))

    def test_whitespace_only_description_raises(self):
        with self.assertRaises(TaskValidationError) as ctx:
            validate_create_task("worker_task", "   ", "Alex", self.workers)
        self.assertIn("不能为空", str(ctx.exception))

    def test_none_description_raises(self):
        with self.assertRaises(TaskValidationError):
            validate_create_task("worker_task", None, "Alex", self.workers)

    def test_missing_worker_name_for_worker_task_raises(self):
        with self.assertRaises(TaskValidationError) as ctx:
            validate_create_task("worker_task", "do something", None, self.workers)
        self.assertIn("worker_name", str(ctx.exception))

    def test_unknown_worker_name_raises(self):
        with self.assertRaises(TaskValidationError) as ctx:
            validate_create_task("worker_task", "do something", "NonExistent", self.workers)
        self.assertIn("NonExistent", str(ctx.exception))

    def test_verified_task_needs_worker_name(self):
        with self.assertRaises(TaskValidationError) as ctx:
            validate_create_task("verified_task", "do something", None, self.workers)
        self.assertIn("worker_name", str(ctx.exception))

    def test_all_valid_types_accepted(self):
        for t in VALID_TASK_TYPES:
            if t == "project_pipeline_task":
                validate_create_task(t, "desc", None, self.workers)
            else:
                validate_create_task(t, "desc", "Alex", self.workers)


class TestTaskAPI(unittest.TestCase):
    """HTTP API 测试。"""

    @classmethod
    def setUpClass(cls):
        """Mock 所有 LLM 入口 + 启用同步执行模式，消除后台线程泄漏。"""

        # 1. 导入 task_executor（server 模块级创建的同一个实例）
        from runtime.agent_task import get_executor
        cls._executor = get_executor()

        # 2. 开启同步模式：submit() 在当前线程直接执行，不创建后台线程
        cls._executor._sync_mode = True

        # 3. Mock 所有 runtime 入口函数，确保绝不访问真实 LLM
        cls._run_worker_patcher = patch("runtime.workers.run_worker")
        cls.mock_run_worker = cls._run_worker_patcher.start()
        cls.mock_run_worker.return_value = {
            "log": ["[test] 模拟执行"],
            "result": '{"status":"success","summary":"模拟结果","artifacts":[]}',
            "messages": [],
            "model_used": "[deepseek]deepseek-v4-pro",
            "complexity": "deepseek",
            "structured_result": {"status": "success", "summary": "模拟结果", "artifacts": []},
        }

        cls._verified_patcher = patch("runtime.verification.delegate_with_verification")
        cls.mock_verified = cls._verified_patcher.start()
        cls.mock_verified.return_value = {"status": "done", "verdict": "pass"}

        # project_pipeline_task 需要 mock project_setup + run_project_pipeline
        cls._proj_setup_patcher = patch("runtime.pipeline.project_setup")
        cls.mock_proj_setup = cls._proj_setup_patcher.start()
        cls.mock_proj_setup.return_value = (
            "\n=======================================================\n"
            "  项目分工表: test_project\n"
            "=======================================================\n"
            "\n状态文件: project_states/test_project_state.json\n"
        )

        cls._pipeline_patcher = patch("runtime.pipeline.run_project_pipeline")
        cls.mock_pipeline = cls._pipeline_patcher.start()
        cls.mock_pipeline.return_value = {"status": "done", "nodes_completed": 3}

    @classmethod
    def tearDownClass(cls):
        """先关闭 executor（等待任务完成）→ 再停止 mock patch。"""
        if cls._executor:
            cls._executor.shutdown(wait=True)
        cls._run_worker_patcher.stop()
        cls._verified_patcher.stop()
        cls._proj_setup_patcher.stop()
        cls._pipeline_patcher.stop()

    def setUp(self):
        self._settings_file = server._settings_path()
        self._settings_file_existed = os.path.exists(self._settings_file)
        self._settings_file_content = None
        if self._settings_file_existed:
            with open(self._settings_file, "r", encoding="utf-8") as f:
                self._settings_file_content = f.read()
        self._runtime_settings = dict(server.runtime_settings)
        self._current_workspace = dict(server._current_workspace)

    def tearDown(self):
        server.runtime_settings.clear()
        server.runtime_settings.update(self._runtime_settings)
        server._current_workspace.clear()
        server._current_workspace.update(self._current_workspace)
        if self._settings_file_existed:
            with open(self._settings_file, "w", encoding="utf-8") as f:
                f.write(self._settings_file_content or "")
        elif os.path.exists(self._settings_file):
            os.remove(self._settings_file)

    def test_create_worker_task_returns_task_id(self):
        resp = client.post("/agent/tasks", json={
            "type": "worker_task",
            "description": "检查项目代码质量",
            "worker_name": "Alex",
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertIn("task_", data["task_id"])
        self.assertIn(data["status"], ("pending", "running", "completed"))

    def test_create_verified_task_returns_task_id(self):
        resp = client.post("/agent/tasks", json={
            "type": "verified_task",
            "description": "审查架构设计",
            "worker_name": "Sophia",
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertIn("task_", data["task_id"])

    def test_create_pipeline_task_returns_task_id(self):
        resp = client.post("/agent/tasks", json={
            "type": "project_pipeline_task",
            "description": "构建一个 Todo 应用",
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertTrue(data["ok"])

    def test_invalid_task_type_returns_422(self):
        resp = client.post("/agent/tasks", json={
            "type": "shell_exec",
            "description": "执行任意命令",
        })
        self.assertEqual(resp.status_code, 422, resp.text)

    def test_invalid_worker_name_returns_400(self):
        resp = client.post("/agent/tasks", json={
            "type": "worker_task",
            "description": "测试",
            "worker_name": "NonExistentWorker",
        })
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertIn("NonExistentWorker", resp.json()["detail"])

    def test_empty_description_returns_422(self):
        resp = client.post("/agent/tasks", json={
            "type": "worker_task",
            "description": "",
            "worker_name": "Alex",
        })
        self.assertEqual(resp.status_code, 422, resp.text)

    def test_missing_worker_name_for_worker_task_returns_400(self):
        resp = client.post("/agent/tasks", json={
            "type": "worker_task",
            "description": "需要 worker 但没有指定",
        })
        self.assertEqual(resp.status_code, 400, resp.text)

    def test_get_task_returns_status(self):
        # 先创建
        resp = client.post("/agent/tasks", json={
            "type": "worker_task",
            "description": "获取状态测试",
            "worker_name": "Alex",
        })
        task_id = resp.json()["task_id"]

        # 查询
        resp2 = client.get(f"/agent/tasks/{task_id}")
        self.assertEqual(resp2.status_code, 200)
        data = resp2.json()
        self.assertEqual(data["task_id"], task_id)
        self.assertEqual(data["type"], "worker_task")
        self.assertIn(data["status"], ("pending", "running", "completed", "failed"))

    def test_get_nonexistent_task_returns_404(self):
        resp = client.get("/agent/tasks/task_nonexistent_000")
        self.assertEqual(resp.status_code, 404)

    def test_get_task_logs(self):
        resp = client.post("/agent/tasks", json={
            "type": "worker_task",
            "description": "日志测试",
            "worker_name": "Alex",
        })
        task_id = resp.json()["task_id"]

        resp2 = client.get(f"/agent/tasks/{task_id}/logs")
        self.assertEqual(resp2.status_code, 200)
        data = resp2.json()
        self.assertEqual(data["task_id"], task_id)
        self.assertIsInstance(data["logs"], list)

    def test_cancel_pending_task_api(self):
        """通过 API 取消一个 pending 任务（不经过 executor 提交避免竞态）。"""
        # 直接写入 TaskStore，不触发 executor.submit()
        tid = _generate_task_id()
        task = AgentTask(
            task_id=tid,
            type="worker_task",
            description="pending cancel api 测试",
            worker_name="Alex",
        )
        TaskStore.save(task)

        # 通过 API 取消
        resp = client.post(f"/agent/tasks/{tid}/cancel")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["status"], "cancelled")
        self.assertIn("已取消", data["message"])

    def test_cancel_already_completed_task(self):
        """取消已完成任务 → 返回当前状态不操作。"""
        resp = client.post("/agent/tasks", json={
            "type": "worker_task",
            "description": "cancel completed 测试",
            "worker_name": "Alex",
        })
        task_id = resp.json()["task_id"]

        # sync_mode 下任务已同步完成
        r = client.get(f"/agent/tasks/{task_id}")
        self.assertEqual(r.json()["status"], "completed")

        # 对已完成的任务发送 cancel
        resp_cancel = client.post(f"/agent/tasks/{task_id}/cancel")
        self.assertEqual(resp_cancel.status_code, 200)
        data = resp_cancel.json()
        self.assertIn("无需取消", data["message"])

    def test_completed_task_has_result_or_error(self):
        """模拟 run_worker 后，任务同步完成（sync_mode），应立即有 result。"""
        resp = client.post("/agent/tasks", json={
            "type": "worker_task",
            "description": "模拟执行任务",
            "worker_name": "Alex",
        })
        task_id = resp.json()["task_id"]

        # sync_mode 下任务已同步完成，无需轮询等待
        r = client.get(f"/agent/tasks/{task_id}")
        status = r.json()["status"]
        self.assertEqual(status, "completed",
                         f"sync_mode 下任务应立即完成，实际: {status}")

        r = client.get(f"/agent/tasks/{task_id}/result")
        self.assertEqual(r.status_code, 200)
        result_data = r.json()
        self.assertIsNotNone(result_data.get("result"))

    def test_list_tasks(self):
        resp = client.get("/agent/tasks?limit=5")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("total", data)
        self.assertIn("items", data)
        self.assertIsInstance(data["items"], list)

    def test_get_task_detail(self):
        resp = client.post("/agent/tasks", json={
            "type": "worker_task",
            "description": "detail 测试",
            "worker_name": "Alex",
        })
        task_id = resp.json()["task_id"]

        resp2 = client.get(f"/agent/tasks/{task_id}/detail")
        self.assertEqual(resp2.status_code, 200)
        data = resp2.json()
        self.assertEqual(data["task_id"], task_id)
        self.assertIn("logs", data)
        self.assertIn("cancel_requested", data)

    def test_cannot_create_task_with_empty_type(self):
        resp = client.post("/agent/tasks", json={
            "type": "",
            "description": "空 type 测试",
            "worker_name": "Alex",
        })
        self.assertGreaterEqual(resp.status_code, 400)

    def test_workspace_file_list_and_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "README.md"), "w", encoding="utf-8") as f:
                f.write("# demo\nhello")
            os.makedirs(os.path.join(tmp, "node_modules"))
            with open(os.path.join(tmp, "node_modules", "hidden.js"), "w", encoding="utf-8") as f:
                f.write("hidden")

            resp = client.post("/agent/workspace", json={"path": tmp})
            self.assertEqual(resp.status_code, 200, resp.text)

            files = client.get("/agent/workspace/files")
            self.assertEqual(files.status_code, 200, files.text)
            names = [entry["name"] for entry in files.json()["entries"]]
            self.assertIn("README.md", names)
            self.assertNotIn("node_modules", names)

            preview = client.get("/agent/workspace/file", params={"path": "README.md"})
            self.assertEqual(preview.status_code, 200, preview.text)
            data = preview.json()
            self.assertTrue(data["previewable"])
            self.assertEqual(data["relative_path"], "README.md")
            self.assertIn("hello", data["content"])

    def test_workspace_file_rejects_path_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            client.post("/agent/workspace", json={"path": tmp})
            resp = client.get("/agent/workspace/files", params={"path": "../"})
            self.assertEqual(resp.status_code, 403, resp.text)

    def test_workspace_binary_preview_returns_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "data.bin"), "wb") as f:
                f.write(b"\x00\x01\x02abc")
            client.post("/agent/workspace", json={"path": tmp})
            resp = client.get("/agent/workspace/file", params={"path": "data.bin"})
            self.assertEqual(resp.status_code, 200, resp.text)
            data = resp.json()
            self.assertFalse(data["previewable"])
            self.assertIn("二进制", data["reason"])

    def test_task_events_parse_tool_logs(self):
        tid = _generate_task_id()
        task = AgentTask(
            task_id=tid,
            type="worker_task",
            description="events 测试",
            worker_name="Alex",
            logs=[
                '[2026-01-01 10:00:00] [工具: read_file({"file_path":"README.md"})]',
                "[2026-01-01 10:00:01] [工具返回: hello]",
                "[2026-01-01 10:00:02] 异常: boom",
            ],
        )
        TaskStore.save(task)
        resp = client.get(f"/agent/tasks/{tid}/events")
        self.assertEqual(resp.status_code, 200, resp.text)
        events = resp.json()["events"]
        self.assertEqual(events[0]["type"], "tool_call")
        self.assertEqual(events[0]["tool_name"], "read_file")
        self.assertEqual(events[0]["args"]["file_path"], "README.md")
        self.assertEqual(events[1]["type"], "tool_result")
        self.assertEqual(events[2]["type"], "error")

    def test_memory_and_rules_have_stable_shape(self):
        memory = client.get("/agent/memory")
        self.assertEqual(memory.status_code, 200, memory.text)
        memory_data = memory.json()
        self.assertIn("sessions", memory_data)
        self.assertIn("knowledge", memory_data)
        self.assertIn("project_states", memory_data)

        rules = client.get("/agent/rules")
        self.assertEqual(rules.status_code, 200, rules.text)
        rules_data = rules.json()
        self.assertIn("task_types", rules_data)
        self.assertIn("workers", rules_data)
        self.assertIn("dangerous_tools", rules_data)

    def test_health_identifies_project_runtime(self):
        resp = client.get("/health")
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["app"], "local-agent-workbench")
        self.assertIn("project_dir", data)
        self.assertTrue(os.path.isdir(data["project_dir"]))

    def test_settings_endpoint_returns_runtime_status_without_keys(self):
        resp = client.get("/agent/settings")
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertIn("settings", data)
        self.assertIn("schema", data)
        self.assertIn("runtime", data)
        self.assertIn("providers", data["runtime"])
        self.assertNotIn("test-dummy-key", resp.text)
        self.assertNotIn("sk-", resp.text)

    def test_settings_patch_rejects_unknown_fields(self):
        resp = client.patch("/agent/settings", json={"api_key": "should-not-save"})
        self.assertEqual(resp.status_code, 422, resp.text)

    def test_settings_patch_persists_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            resp = client.patch("/agent/settings", json={
                "workspace_path": tmp,
                "default_task_type": "verified_task",
                "theme": "blue",
                "refresh_interval_sec": 7,
            })
            self.assertEqual(resp.status_code, 200, resp.text)
            data = resp.json()
            self.assertEqual(data["settings"]["workspace_path"], os.path.abspath(tmp))
            self.assertEqual(data["settings"]["default_task_type"], "verified_task")
            self.assertEqual(server._current_workspace["path"], os.path.abspath(tmp))

            workspace = client.get("/agent/workspace")
            self.assertEqual(workspace.json()["workspace"], os.path.abspath(tmp))

    def test_missing_default_client_fails_only_on_call(self):
        import runtime.config as cfg
        old_key = cfg.DEEPSEEK_API_KEY
        old_providers = dict(cfg.PROVIDERS)
        try:
            cfg.DEEPSEEK_API_KEY = ""
            cfg.PROVIDERS.clear()
            client_obj = get_default_client()
            self.assertIsInstance(client_obj, MissingProviderClient)
            with self.assertRaises(RuntimeError):
                client_obj.messages.create(model="x", messages=[])
        finally:
            cfg.DEEPSEEK_API_KEY = old_key
            cfg.PROVIDERS.clear()
            cfg.PROVIDERS.update(old_providers)


class TestTaskPersistence(unittest.TestCase):
    """任务持久化测试。"""

    def test_save_and_load_roundtrip(self):
        task = AgentTask(
            task_id="task_persist_test",
            type="worker_task",
            description="持久化测试",
            worker_name="Alex",
        )
        TaskStore.save(task)

        loaded = TaskStore.get("task_persist_test")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.task_id, task.task_id)
        self.assertEqual(loaded.type, task.type)
        self.assertEqual(loaded.description, task.description)

    def test_update_preserves_changes(self):
        task = AgentTask(
            task_id="task_update_test",
            type="worker_task",
            description="before update",
        )
        TaskStore.save(task)

        task.status = "running"
        task.progress = "50%"
        TaskStore.save(task)

        loaded = TaskStore.get("task_update_test")
        self.assertEqual(loaded.status, "running")
        self.assertEqual(loaded.progress, "50%")

    def test_list_all_returns_tasks(self):
        tasks_before = TaskStore.list_all()
        task = AgentTask(
            task_id="task_list_all_test",
            type="worker_task",
            description="list 测试",
        )
        TaskStore.save(task)
        tasks_after = TaskStore.list_all()
        self.assertGreaterEqual(len(tasks_after), len(tasks_before))

    def test_recover_incomplete_marks_running_task_failed(self):
        task = AgentTask(
            task_id="task_recover_running",
            type="worker_task",
            description="recover 测试",
            worker_name="Alex",
            status="running",
            progress="执行中",
        )
        TaskStore.save(task)

        recovered = TaskStore.recover_incomplete("测试恢复")
        loaded = TaskStore.get("task_recover_running")

        self.assertGreaterEqual(recovered, 1)
        self.assertEqual(loaded.status, "failed")
        self.assertEqual(loaded.progress, "执行中断")
        self.assertIn("测试恢复", loaded.error)
        self.assertTrue(any("系统恢复" in line for line in loaded.logs))

    def test_load_falls_back_to_backup_file(self):
        from runtime import agent_task as agent_task_module

        original_main = agent_task_module._TASKS_FILE
        original_backup = agent_task_module._TASKS_BACKUP_FILE
        original_cache = dict(agent_task_module._tasks_cache)
        with tempfile.TemporaryDirectory() as tmp:
            main_file = os.path.join(tmp, "agent_tasks.json")
            backup_file = f"{main_file}.bak"
            with open(main_file, "w", encoding="utf-8") as f:
                f.write("{bad json")
            with open(backup_file, "w", encoding="utf-8") as f:
                json.dump([{
                    "task_id": "task_backup_ok",
                    "type": "worker_task",
                    "status": "completed",
                    "description": "backup",
                    "worker_name": "Alex",
                    "project_name": None,
                    "workspace_path": None,
                    "progress": "完成",
                    "logs": [],
                    "result": "ok",
                    "artifacts": [],
                    "error": None,
                    "created_at": "2026-01-01 00:00:00",
                    "updated_at": "2026-01-01 00:00:00",
                    "cancel_requested": False,
                }], f, ensure_ascii=False, indent=2)

            try:
                agent_task_module._TASKS_FILE = main_file
                agent_task_module._TASKS_BACKUP_FILE = backup_file
                agent_task_module._tasks_cache = {}
                loaded = TaskStore.load()
            finally:
                agent_task_module._TASKS_FILE = original_main
                agent_task_module._TASKS_BACKUP_FILE = original_backup
                agent_task_module._tasks_cache = original_cache

        self.assertIn("task_backup_ok", loaded)
        self.assertEqual(loaded["task_backup_ok"].result, "ok")


if __name__ == "__main__":
    unittest.main()
