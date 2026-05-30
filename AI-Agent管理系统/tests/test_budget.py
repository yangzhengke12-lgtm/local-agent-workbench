import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from manager import _check_budget, Budget


class TestCheckBudget(unittest.TestCase):
    """Tests for _check_budget — verifies budget limit enforcement."""

    def test_budget_under_limit(self) -> None:
        """attempts=2, max=3 → allowed=True."""
        budget = Budget(max_attempts=3)
        stats = {"attempts": 2}
        result = _check_budget(stats, budget)
        self.assertTrue(result["allowed"])

    def test_budget_exceeded_attempts(self) -> None:
        """attempts=4, max=3 → allowed=False, budget_type="max_attempts"."""
        budget = Budget(max_attempts=3)
        stats = {"attempts": 4}
        result = _check_budget(stats, budget)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["budget_type"], "max_attempts")
        self.assertEqual(result["current"], 4)
        self.assertEqual(result["limit"], 3)

    def test_budget_exceeded_model_calls(self) -> None:
        """model_calls=21, max=20 → allowed=False, budget_type="max_model_calls"."""
        budget = Budget(max_model_calls=20)
        stats = {"model_calls": 21}
        result = _check_budget(stats, budget)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["budget_type"], "max_model_calls")

    def test_budget_exceeded_tool_calls(self) -> None:
        """tool_calls=51, max=50 → allowed=False, budget_type="max_tool_calls"."""
        budget = Budget(max_tool_calls=50)
        stats = {"tool_calls": 51}
        result = _check_budget(stats, budget)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["budget_type"], "max_tool_calls")

    def test_budget_exact_limit(self) -> None:
        """attempts=3, max=3 → allowed=True (not exceeded, just at limit)."""
        budget = Budget(max_attempts=3)
        stats = {"attempts": 3}
        result = _check_budget(stats, budget)
        self.assertTrue(result["allowed"], "Exact limit should be allowed")

    def test_budget_exceeded_rounds(self) -> None:
        """rounds=6, max_rounds=5 → allowed=False, budget_type="max_rounds"."""
        budget = Budget(max_rounds=5)
        stats = {"rounds": 6}
        result = _check_budget(stats, budget)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["budget_type"], "max_rounds")

    def test_budget_exceeded_runtime(self) -> None:
        """runtime_seconds=601, max=600 → allowed=False, budget_type="max_runtime_seconds"."""
        budget = Budget(max_runtime_seconds=600)
        stats = {"runtime_seconds": 601}
        result = _check_budget(stats, budget)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["budget_type"], "max_runtime_seconds")


class TestBudgetDefaults(unittest.TestCase):
    """Tests for Budget dataclass — default values and custom construction."""

    def test_budget_default_values(self) -> None:
        """Budget() has correct default field values."""
        b = Budget()
        self.assertEqual(b.max_attempts, 3)
        self.assertEqual(b.max_rounds, 5)
        self.assertEqual(b.max_tool_calls, 50)
        self.assertEqual(b.max_runtime_seconds, 600)
        self.assertEqual(b.max_model_calls, 20)

    def test_budget_custom(self) -> None:
        """Budget(max_attempts=10, max_model_calls=100) stores correctly."""
        b = Budget(max_attempts=10, max_model_calls=100)
        self.assertEqual(b.max_attempts, 10)
        self.assertEqual(b.max_model_calls, 100)
        # Unspecified fields should keep their defaults
        self.assertEqual(b.max_rounds, 5)
        self.assertEqual(b.max_tool_calls, 50)
        self.assertEqual(b.max_runtime_seconds, 600)


if __name__ == "__main__":
    unittest.main()
