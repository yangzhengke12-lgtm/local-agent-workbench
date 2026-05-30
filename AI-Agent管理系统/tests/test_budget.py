import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from manager import _check_budget, Budget


class TestCheckBudget(unittest.TestCase):
    """Tests for _check_budget — verifies budget limit enforcement."""

    def test_budget_under_limit(self) -> None:
        """attempts=2, max=3 → exceeded=False."""
        budget = Budget(max_attempts=3)
        stats = {"attempts": 2}
        result = _check_budget(stats, budget)
        self.assertFalse(result["exceeded"])
        self.assertEqual(result["reason"], "")

    def test_budget_exceeded_attempts(self) -> None:
        """attempts=4, max=3 → exceeded=True, reason mentions 'attempts'."""
        budget = Budget(max_attempts=3)
        stats = {"attempts": 4}
        result = _check_budget(stats, budget)
        self.assertTrue(result["exceeded"])
        self.assertIn("attempts", result["reason"])

    def test_budget_exceeded_model_calls(self) -> None:
        """model_calls=21, max=20 → exceeded=True."""
        budget = Budget(max_model_calls=20)
        stats = {"model_calls": 21}
        result = _check_budget(stats, budget)
        self.assertTrue(result["exceeded"])

    def test_budget_exceeded_tool_calls(self) -> None:
        """tool_calls=51, max=50 → exceeded=True."""
        budget = Budget(max_tool_calls=50)
        stats = {"tool_calls": 51}
        result = _check_budget(stats, budget)
        self.assertTrue(result["exceeded"])

    def test_budget_exact_limit(self) -> None:
        """attempts=3, max=3 → exceeded=False (not over, just at limit)."""
        budget = Budget(max_attempts=3)
        stats = {"attempts": 3}
        result = _check_budget(stats, budget)
        self.assertFalse(result["exceeded"], "Exact limit should not be exceeded")


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
