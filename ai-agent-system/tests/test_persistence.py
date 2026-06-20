import sys
import os
import json
import unittest
import tempfile
import shutil
import time
from unittest.mock import patch, MagicMock
from dataclasses import asdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Ensure required env vars exist for module-level initialization in manager.py
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy-key")

from manager import (
    WorkflowRun,
    Budget,
    TaskNode,
    _save_workflow_run,
    load_workflow_run,
    resume_workflow_run,
    PROJECT_STATE_DIR,
)


class TestPersistence(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self._proj_dir_patcher = patch("manager.PROJECT_STATE_DIR", self.tmpdir)
        self._proj_dir_patcher.start()

    def tearDown(self) -> None:
        self._proj_dir_patcher.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ── helpers ────────────────────────────────────────────────

    def _create_sample_run(self) -> WorkflowRun:
        run = WorkflowRun(
            run_id="test_run_001",
            project_name="test_project",
            status="running",
            created_at="2025-01-01 00:00:00",
        )
        run.nodes = {
            "node_a": asdict(
                TaskNode(
                    id="node_a",
                    name="Node A",
                    description="First node",
                    status="done",
                    assigned_worker="Alex",
                    attempts=1,
                )
            ),
            "node_b": asdict(
                TaskNode(
                    id="node_b",
                    name="Node B",
                    description="Second node",
                    status="running",
                    assigned_worker="Sophia",
                    depends_on=["node_a"],
                )
            ),
        }
        run.budget = Budget(max_attempts=5, max_model_calls=50)
        run.execution_log = [{"event": "start", "timestamp": "2025-01-01"}]
        return run

    # ── test cases ─────────────────────────────────────────────

    def test_save_and_load_roundtrip(self) -> None:
        run = self._create_sample_run()
        _save_workflow_run(run)

        loaded = load_workflow_run("test_project")
        self.assertIsNotNone(loaded, "loaded run should not be None")
        assert loaded is not None  # type narrow
        self.assertEqual(loaded.run_id, "test_run_001")
        self.assertEqual(loaded.project_name, "test_project")
        self.assertEqual(loaded.status, "running")
        self.assertEqual(len(loaded.nodes), 2)
        self.assertIn("node_a", loaded.nodes)
        self.assertIn("node_b", loaded.nodes)
        self.assertEqual(loaded.nodes["node_a"]["status"], "done")
        self.assertEqual(loaded.nodes["node_a"]["assigned_worker"], "Alex")
        self.assertEqual(loaded.nodes["node_b"]["status"], "running")
        self.assertEqual(loaded.nodes["node_b"]["depends_on"], ["node_a"])
        self.assertEqual(loaded.budget.max_attempts, 5)
        self.assertEqual(loaded.budget.max_model_calls, 50)
        self.assertEqual(len(loaded.execution_log), 1)
        self.assertEqual(loaded.execution_log[0]["event"], "start")
        self.assertEqual(loaded.version, 4)
        # updated_at should be populated by _save_workflow_run
        self.assertTrue(loaded.updated_at, "updated_at should be non-empty")

    def test_load_missing_returns_none(self) -> None:
        result = load_workflow_run("nonexistent_project")
        self.assertIsNone(result)

    @patch("runtime.pipeline.run_project_pipeline", return_value={"status": "ok"})
    def test_resume_resets_running_nodes(self, mock_pipeline: MagicMock) -> None:
        run = self._create_sample_run()
        self.assertEqual(run.nodes["node_b"]["status"], "running")
        # status must not be "completed" so resume proceeds
        run.status = "running"
        _save_workflow_run(run)

        result = resume_workflow_run("test_project", {"Alex": MagicMock()})

        # Reload and verify the running node was reset to "todo"
        loaded = load_workflow_run("test_project")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.nodes["node_b"]["status"], "todo")
        # done node should be unchanged
        self.assertEqual(loaded.nodes["node_a"]["status"], "done")
        # run_project_pipeline should have been called
        mock_pipeline.assert_called_once()

    def test_save_updates_timestamp(self) -> None:
        run = WorkflowRun(
            run_id="ts_test",
            project_name="ts_project",
            status="pending",
        )
        _save_workflow_run(run)

        loaded1 = load_workflow_run("ts_project")
        self.assertIsNotNone(loaded1)
        assert loaded1 is not None
        ts1 = loaded1.updated_at
        self.assertTrue(ts1, "first updated_at should be set")

        # Ensure the next save produces a different timestamp
        time.sleep(1.1)

        loaded1.status = "running"
        _save_workflow_run(loaded1)

        loaded2 = load_workflow_run("ts_project")
        self.assertIsNotNone(loaded2)
        assert loaded2 is not None
        ts2 = loaded2.updated_at
        self.assertTrue(ts2, "second updated_at should be set")

        self.assertNotEqual(ts1, ts2, "updated_at should change after second save")

    def test_nodes_preserved(self) -> None:
        run = WorkflowRun(
            run_id="multi_nodes",
            project_name="multi_project",
            status="running",
        )
        run.nodes = {
            "n1": asdict(TaskNode(id="n1", name="Todo Node", status="todo")),
            "n2": asdict(TaskNode(id="n2", name="Done Node", status="done")),
            "n3": asdict(TaskNode(id="n3", name="Failed Node", status="failed")),
            "n4": asdict(
                TaskNode(
                    id="n4",
                    name="Blocked Node",
                    status="blocked",
                    depends_on=["n3"],
                )
            ),
        }
        _save_workflow_run(run)

        loaded = load_workflow_run("multi_project")
        self.assertIsNotNone(loaded)
        assert loaded is not None

        self.assertEqual(len(loaded.nodes), 4)
        self.assertEqual(loaded.nodes["n1"]["status"], "todo")
        self.assertEqual(loaded.nodes["n2"]["status"], "done")
        self.assertEqual(loaded.nodes["n3"]["status"], "failed")
        self.assertEqual(loaded.nodes["n4"]["status"], "blocked")
        self.assertEqual(loaded.nodes["n4"]["depends_on"], ["n3"])


if __name__ == "__main__":
    unittest.main()
