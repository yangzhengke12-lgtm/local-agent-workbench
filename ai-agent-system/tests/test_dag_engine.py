import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from manager import (
    _topological_sort,
    _find_ready_nodes,
    _propagate_blocks,
    _resolve_worker_for_step,
    _build_node_task,
)


class TestTopologicalSort(unittest.TestCase):
    """Tests for _topological_sort — Kahn's algorithm with cycle detection."""

    def test_topo_linear(self) -> None:
        """A -> B -> C produces [A, B, C]."""
        nodes = {
            "A": {"id": "A", "name": "A", "status": "todo", "depends_on": [], "assigned_worker": "Alex"},
            "B": {"id": "B", "name": "B", "status": "todo", "depends_on": ["A"], "assigned_worker": "Sophia"},
            "C": {"id": "C", "name": "C", "status": "todo", "depends_on": ["B"], "assigned_worker": "Nathaniel"},
        }
        result = _topological_sort(nodes)
        self.assertEqual(result, ["A", "B", "C"])

    def test_topo_diamond(self) -> None:
        """A -> (B, C) -> D: verify A before B,C, and D last."""
        nodes = {
            "A": {"id": "A", "name": "A", "status": "todo", "depends_on": [], "assigned_worker": "Alex"},
            "B": {"id": "B", "name": "B", "status": "todo", "depends_on": ["A"], "assigned_worker": "Sophia"},
            "C": {"id": "C", "name": "C", "status": "todo", "depends_on": ["A"], "assigned_worker": "Nathaniel"},
            "D": {"id": "D", "name": "D", "status": "todo", "depends_on": ["B", "C"], "assigned_worker": "Elena"},
        }
        result = _topological_sort(nodes)
        self.assertEqual(len(result), 4)
        self.assertEqual(result[0], "A", "A must come before B and C")
        self.assertEqual(result[-1], "D", "D must be last")
        self.assertSetEqual(set(result[1:3]), {"B", "C"}, "B and C between A and D, order may vary")

    def test_topo_independent(self) -> None:
        """A, B, C with no deps → all three in result (any order)."""
        nodes = {
            "A": {"id": "A", "name": "A", "status": "todo", "depends_on": [], "assigned_worker": "Alex"},
            "B": {"id": "B", "name": "B", "status": "todo", "depends_on": [], "assigned_worker": "Sophia"},
            "C": {"id": "C", "name": "C", "status": "todo", "depends_on": [], "assigned_worker": "Nathaniel"},
        }
        result = _topological_sort(nodes)
        self.assertEqual(len(result), 3)
        self.assertSetEqual(set(result), {"A", "B", "C"})

    def test_topo_cycle_detected(self) -> None:
        """A -> B -> A raises ValueError."""
        nodes = {
            "A": {"id": "A", "name": "A", "status": "todo", "depends_on": ["B"], "assigned_worker": "Alex"},
            "B": {"id": "B", "name": "B", "status": "todo", "depends_on": ["A"], "assigned_worker": "Sophia"},
        }
        with self.assertRaises(ValueError):
            _topological_sort(nodes)

    def test_topo_single_node(self) -> None:
        """Single node with no deps → [node]."""
        nodes = {
            "X": {"id": "X", "name": "X", "status": "todo", "depends_on": [], "assigned_worker": "Alex"},
        }
        result = _topological_sort(nodes)
        self.assertEqual(result, ["X"])


class TestFindReadyNodes(unittest.TestCase):
    """Tests for _find_ready_nodes — finds todo nodes with all deps done."""

    def test_find_ready_all_deps_done(self) -> None:
        """A done, B depends_on [A], B status=todo → returns [B]."""
        nodes = {
            "A": {"id": "A", "name": "A", "status": "done", "depends_on": [], "assigned_worker": "Alex"},
            "B": {"id": "B", "name": "B", "status": "todo", "depends_on": ["A"], "assigned_worker": "Sophia"},
        }
        result = _find_ready_nodes(nodes)
        self.assertEqual(result, ["B"])

    def test_find_ready_blocked_dep(self) -> None:
        """A failed, B depends_on [A], B status=todo → B not returned."""
        nodes = {
            "A": {"id": "A", "name": "A", "status": "failed", "depends_on": [], "assigned_worker": "Alex"},
            "B": {"id": "B", "name": "B", "status": "todo", "depends_on": ["A"], "assigned_worker": "Sophia"},
        }
        result = _find_ready_nodes(nodes)
        self.assertEqual(result, [], "B should not be ready because A is failed, not done")

    def test_find_ready_no_deps(self) -> None:
        """Node with empty depends_on and status=todo → returned."""
        nodes = {
            "A": {"id": "A", "name": "A", "status": "todo", "depends_on": [], "assigned_worker": "Alex"},
        }
        result = _find_ready_nodes(nodes)
        self.assertEqual(result, ["A"])

    def test_find_ready_not_todo(self) -> None:
        """Node with status='running', deps satisfied → not returned."""
        nodes = {
            "A": {"id": "A", "name": "A", "status": "running", "depends_on": [], "assigned_worker": "Alex"},
        }
        result = _find_ready_nodes(nodes)
        self.assertEqual(result, [], "Running nodes should not be considered ready")


class TestPropagateBlocks(unittest.TestCase):
    """Tests for _propagate_blocks — marks downstream nodes as blocked."""

    def test_propagate_blocks(self) -> None:
        """A failed, B depends_on [A] → B marked blocked."""
        nodes = {
            "A": {"id": "A", "name": "A", "status": "failed", "depends_on": [], "assigned_worker": "Alex"},
            "B": {"id": "B", "name": "B", "status": "todo", "depends_on": ["A"], "assigned_worker": "Sophia"},
        }
        changed = _propagate_blocks(nodes)
        self.assertGreaterEqual(changed, 1)
        self.assertEqual(nodes["B"]["status"], "blocked")

    def test_propagate_blocks_chain(self) -> None:
        """A failed, B → [A], C → [B] → both B and C blocked."""
        nodes = {
            "A": {"id": "A", "name": "A", "status": "failed", "depends_on": [], "assigned_worker": "Alex"},
            "B": {"id": "B", "name": "B", "status": "todo", "depends_on": ["A"], "assigned_worker": "Sophia"},
            "C": {"id": "C", "name": "C", "status": "todo", "depends_on": ["B"], "assigned_worker": "Nathaniel"},
        }
        # Multiple iterations simulate the pipeline loop for chain propagation
        changed1 = _propagate_blocks(nodes)
        changed2 = _propagate_blocks(nodes)
        total_changed = changed1 + changed2
        self.assertGreaterEqual(total_changed, 2)
        self.assertEqual(nodes["B"]["status"], "blocked")
        self.assertEqual(nodes["C"]["status"], "blocked")

    def test_propagate_blocks_done_unaffected(self) -> None:
        """A done, B depends_on [A] → B not blocked."""
        nodes = {
            "A": {"id": "A", "name": "A", "status": "done", "depends_on": [], "assigned_worker": "Alex"},
            "B": {"id": "B", "name": "B", "status": "todo", "depends_on": ["A"], "assigned_worker": "Sophia"},
        }
        changed = _propagate_blocks(nodes)
        self.assertEqual(changed, 0)
        self.assertEqual(nodes["B"]["status"], "todo", "B should stay todo when upstream is done")

    def test_propagate_blocks_multi_dep_second_failed(self) -> None:
        """v4.2: 节点依赖 [A, B]，A done 但 B failed — 正确传播为 blocked。"""
        nodes = {
            "A": {"id": "A", "name": "A", "status": "done", "depends_on": [], "assigned_worker": "Alex"},
            "B": {"id": "B", "name": "B", "status": "failed", "depends_on": [], "assigned_worker": "Sophia"},
            "C": {"id": "C", "name": "C", "status": "todo", "depends_on": ["A", "B"], "assigned_worker": "Nathaniel"},
        }
        _propagate_blocks(nodes)
        self.assertEqual(nodes["C"]["status"], "blocked",
                         "C depends on [A, B]; B is failed → C must be blocked")
        self.assertEqual(nodes["A"]["status"], "done", "A should be unaffected")

    def test_propagate_blocks_multi_dep_first_blocking(self) -> None:
        """v4.2: 节点依赖 [A, B]，A blocked 时仅检查第一个 dependency 就应触发。"""
        nodes = {
            "A": {"id": "A", "name": "A", "status": "blocked", "depends_on": [], "assigned_worker": "Alex"},
            "B": {"id": "B", "name": "B", "status": "done", "depends_on": [], "assigned_worker": "Sophia"},
            "C": {"id": "C", "name": "C", "status": "todo", "depends_on": ["A", "B"], "assigned_worker": "Nathaniel"},
        }
        _propagate_blocks(nodes)
        self.assertEqual(nodes["C"]["status"], "blocked")


class TestResolveWorkerForStep(unittest.TestCase):
    """Tests for _resolve_worker_for_step — matches step to worker via assignments or keywords."""

    def test_resolve_worker_by_assignment(self) -> None:
        """Step name matches assignment task → correct worker returned."""
        step = {"name": "Implement login page", "description": "Write the login frontend code"}
        assignments = {
            "Alex": {"domain": "frontend", "scope": "login module", "tasks": ["Implement login page", "Write tests"]},
        }
        workers = {"Alex": {"name": "Alex", "role": "Developer"}}
        result = _resolve_worker_for_step(step, assignments, workers)
        self.assertEqual(result, "Alex")

    def test_resolve_worker_fallback(self) -> None:
        """No assignment match → keyword match from description."""
        step = {"name": "Quality audit", "description": "审查全部模块的代码质量"}
        assignments: dict = {}
        workers = {"Sophia": {"name": "Sophia", "role": "Reviewer"}}
        result = _resolve_worker_for_step(step, assignments, workers)
        self.assertEqual(result, "Sophia", "Should match '审查' keyword to Sophia")


class TestBuildNodeTask(unittest.TestCase):
    """Tests for _build_node_task — constructs task string with dependencies and review hints."""

    def test_build_node_task_includes_deps(self) -> None:
        """Task string includes dependency info when node has depends_on."""
        ndata = {
            "name": "Build REST API",
            "description": "Implement the REST API endpoints",
            "depends_on": ["Database Schema", "Auth Module"],
            "assigned_worker": "Alex",
        }
        state: dict = {}
        task = _build_node_task(ndata, state)
        self.assertIn("依赖的上游节点（已完成）: Database Schema, Auth Module", task)

    def test_build_node_task_includes_review_hint(self) -> None:
        """Task string includes review plan info when state has review_plan."""
        ndata = {
            "name": "Build REST API",
            "description": "Implement the REST API endpoints",
            "depends_on": [],
            "assigned_worker": "Alex",
        }
        state = {
            "review_plan": {
                "reviewer": "Sophia",
                "validator": "Nathaniel",
                "final_check": "Victor",
            }
        }
        task = _build_node_task(ndata, state)
        self.assertIn("Sophia", task)
        self.assertIn("审查", task)
        self.assertIn("Nathaniel", task)
        self.assertIn("验证", task)


if __name__ == "__main__":
    unittest.main()
