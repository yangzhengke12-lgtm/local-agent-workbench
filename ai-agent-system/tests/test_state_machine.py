"""
Tests for the state machine in manager.py.
Covers: _transition_node, TaskNode, TaskNodeStatus, VALID_TRANSITIONS.
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from manager import (  # type: ignore[import-untyped]
    _transition_node,
    TaskNode,
    TaskNodeStatus,
    VALID_TRANSITIONS,
)


class TestValidTransitions(unittest.TestCase):
    """Tests for legal state transitions via _transition_node."""

    def test_valid_todo_to_ready(self) -> None:
        node = TaskNode(id="t1", name="Task 1")
        self.assertEqual(node.status, TaskNodeStatus.TODO)
        result = _transition_node(node, TaskNodeStatus.READY)
        self.assertEqual(result.status, TaskNodeStatus.READY)
        self.assertIs(result, node)  # returns the same object

    def test_valid_running_to_verifying(self) -> None:
        node = TaskNode(id="t2", name="Task 2", status=TaskNodeStatus.RUNNING)
        result = _transition_node(node, TaskNodeStatus.VERIFYING)
        self.assertEqual(result.status, TaskNodeStatus.VERIFYING)

    def test_valid_verifying_to_done(self) -> None:
        node = TaskNode(id="t3", name="Task 3", status=TaskNodeStatus.VERIFYING)
        result = _transition_node(node, TaskNodeStatus.DONE)
        self.assertEqual(result.status, TaskNodeStatus.DONE)

    def test_valid_todo_to_running(self) -> None:
        node = TaskNode(id="t4", name="Task 4")
        # TODO -> RUNNING is valid per VALID_TRANSITIONS
        result = _transition_node(node, TaskNodeStatus.RUNNING)
        self.assertEqual(result.status, TaskNodeStatus.RUNNING)

    def test_valid_running_to_retrying(self) -> None:
        node = TaskNode(id="t5", name="Task 5", status=TaskNodeStatus.RUNNING)
        result = _transition_node(node, TaskNodeStatus.RETRYING)
        self.assertEqual(result.status, TaskNodeStatus.RETRYING)

    def test_valid_running_to_failed(self) -> None:
        node = TaskNode(id="t6", name="Task 6", status=TaskNodeStatus.RUNNING)
        result = _transition_node(node, TaskNodeStatus.FAILED)
        self.assertEqual(result.status, TaskNodeStatus.FAILED)


class TestInvalidTransitions(unittest.TestCase):
    """Tests for illegal state transitions that must raise ValueError."""

    def test_invalid_done_to_running(self) -> None:
        node = TaskNode(id="t7", name="Task 7", status=TaskNodeStatus.DONE)
        with self.assertRaises(ValueError) as ctx:
            _transition_node(node, TaskNodeStatus.RUNNING, reason="try to re-run")
        self.assertIn("Illegal transition", str(ctx.exception))
        self.assertIn("DONE", str(ctx.exception).upper() or "done")

    def test_invalid_todo_to_done(self) -> None:
        node = TaskNode(id="t8", name="Task 8")
        with self.assertRaises(ValueError) as ctx:
            _transition_node(node, TaskNodeStatus.DONE)
        self.assertIn("Illegal transition", str(ctx.exception))

    def test_invalid_verifying_to_todo(self) -> None:
        node = TaskNode(id="t9", name="Task 9", status=TaskNodeStatus.VERIFYING)
        with self.assertRaises(ValueError):
            _transition_node(node, TaskNodeStatus.TODO)

    def test_invalid_blocked_to_done(self) -> None:
        node = TaskNode(id="t10", name="Task 10", status=TaskNodeStatus.BLOCKED)
        with self.assertRaises(ValueError):
            _transition_node(node, TaskNodeStatus.DONE)


class TestRetryMechanics(unittest.TestCase):
    """Tests for retry counting and retry transitions."""

    def test_retry_increments_attempts(self) -> None:
        node = TaskNode(id="t11", name="Task 11", status=TaskNodeStatus.RUNNING)
        self.assertEqual(node.attempts, 0)
        _transition_node(node, TaskNodeStatus.RETRYING)
        self.assertEqual(node.attempts, 1)

    def test_retry_to_running(self) -> None:
        node = TaskNode(
            id="t12", name="Task 12",
            status=TaskNodeStatus.RETRYING, attempts=1,
        )
        result = _transition_node(node, TaskNodeStatus.RUNNING)
        self.assertEqual(result.status, TaskNodeStatus.RUNNING)
        # attempts should NOT increment again (only on entering RETRYING)
        self.assertEqual(result.attempts, 1)

    def test_multiple_retries_bump_attempts_each_time(self) -> None:
        node = TaskNode(id="t13", name="Task 13", status=TaskNodeStatus.RUNNING)
        # First retry
        _transition_node(node, TaskNodeStatus.RETRYING)
        self.assertEqual(node.attempts, 1)
        # Go back to running
        _transition_node(node, TaskNodeStatus.RUNNING)
        # Second retry
        _transition_node(node, TaskNodeStatus.RETRYING)
        self.assertEqual(node.attempts, 2)


class TestTerminalStates(unittest.TestCase):
    """Tests for terminal/semi-terminal state behavior."""

    def test_terminal_states_no_outgoing(self) -> None:
        # DONE has empty set of valid targets
        done_node = TaskNode(id="td1", name="Done Node", status=TaskNodeStatus.DONE)
        with self.assertRaises(ValueError):
            _transition_node(done_node, TaskNodeStatus.RUNNING)
        with self.assertRaises(ValueError):
            _transition_node(done_node, TaskNodeStatus.RETRYING)
        with self.assertRaises(ValueError):
            _transition_node(done_node, TaskNodeStatus.TODO)

    def test_failed_only_manual_retry(self) -> None:
        # FAILED can only transition to RUNNING (manual retry)
        failed_node = TaskNode(id="tf1", name="Failed Node", status=TaskNodeStatus.FAILED)
        # RUNNING is valid
        _transition_node(failed_node, TaskNodeStatus.RUNNING)

    def test_failed_cannot_go_to_done(self) -> None:
        failed_node = TaskNode(id="tf2", name="Failed Node 2", status=TaskNodeStatus.FAILED)
        with self.assertRaises(ValueError):
            _transition_node(failed_node, TaskNodeStatus.DONE)

    def test_blocked_only_unblock(self) -> None:
        # BLOCKED can only transition to RUNNING (after upstream resolved)
        blocked_node = TaskNode(id="tb1", name="Blocked Node", status=TaskNodeStatus.BLOCKED)
        _transition_node(blocked_node, TaskNodeStatus.RUNNING)


class TestTransitionSideEffects(unittest.TestCase):
    """Tests for side effects of _transition_node."""

    def test_transition_updates_timestamp(self) -> None:
        node = TaskNode(id="t14", name="Task 14")
        self.assertEqual(node.updated_at, "")
        _transition_node(node, TaskNodeStatus.READY)
        self.assertNotEqual(node.updated_at, "")
        # Should be a datetime-like string (YYYY-MM-DD HH:MM:SS)
        parts = node.updated_at.split()
        self.assertEqual(len(parts), 2)
        self.assertEqual(len(parts[0]), 10)  # YYYY-MM-DD
        self.assertEqual(len(parts[1]), 8)   # HH:MM:SS

    def test_reason_in_error_message(self) -> None:
        node = TaskNode(id="t15", name="Task 15", status=TaskNodeStatus.DONE)
        reason = "Cannot restart a completed task"
        with self.assertRaises(ValueError) as ctx:
            _transition_node(node, TaskNodeStatus.RUNNING, reason=reason)
        self.assertIn(reason, str(ctx.exception))


class TestValidTransitionsComplete(unittest.TestCase):
    """Meta-test: verify VALID_TRANSITIONS covers all status constants."""

    def test_all_statuses_in_valid_transitions(self) -> None:
        all_statuses = [
            TaskNodeStatus.TODO,
            TaskNodeStatus.READY,
            TaskNodeStatus.RUNNING,
            TaskNodeStatus.VERIFYING,
            TaskNodeStatus.DONE,
            TaskNodeStatus.RETRYING,
            TaskNodeStatus.FAILED,
            TaskNodeStatus.BLOCKED,
            TaskNodeStatus.NEEDS_REPLAN,
        ]
        for status in all_statuses:
            with self.subTest(status=status):
                self.assertIn(status, VALID_TRANSITIONS)

    def test_valid_transitions_values_are_sets(self) -> None:
        for status, targets in VALID_TRANSITIONS.items():
            with self.subTest(status=status):
                self.assertIsInstance(targets, set)


if __name__ == "__main__":
    unittest.main()
