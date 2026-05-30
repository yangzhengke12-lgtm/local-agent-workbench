import sys
import os
import json
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Ensure required env vars exist for module-level initialization in manager.py
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy-key")

from manager import (
    delegate_with_verification,
    _normalize_worker_result,
    WorkerResult,
    VerificationResult,
    Budget,
)


class TestIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.workers: dict[str, MagicMock] = {
            "Alex": MagicMock(name="Alex"),
            "Sophia": MagicMock(name="Sophia"),
            "Nathaniel": MagicMock(name="Nathaniel"),
        }

    # ── helpers ────────────────────────────────────────────────

    def _make_success_response(self) -> dict:
        return {
            "result": '{"status":"success","summary":"Task completed successfully","artifacts":[]}',
            "log": [],
        }

    def _make_needs_retry_response(self) -> dict:
        return {
            "result": '{"status":"needs_review","summary":"Attempt failed","issues":[{"severity":"high","description":"Quality not sufficient"}],"retryable":true}',
            "log": [],
        }

    # ── test cases ─────────────────────────────────────────────

    @patch("manager._run_verifier")
    @patch("manager.run_worker")
    def test_delegate_pass_first_try(
        self, mock_worker: MagicMock, mock_verifier: MagicMock
    ) -> None:
        mock_worker.return_value = self._make_success_response()
        mock_verifier.return_value = VerificationResult(verdict="pass", score=5)

        result = delegate_with_verification(
            self.workers,
            "Alex",
            "Build a REST API",
            verifier_names=["Sophia"],
            max_retries=1,
        )

        self.assertEqual(result["final_status"], "done")
        self.assertIsNotNone(result["verification"])
        mock_worker.assert_called_once()
        mock_verifier.assert_called_once()

    @patch("manager._run_verifier")
    @patch("manager.run_worker")
    def test_delegate_retry_then_pass(
        self, mock_worker: MagicMock, mock_verifier: MagicMock
    ) -> None:
        mock_worker.side_effect = [
            self._make_needs_retry_response(),
            self._make_success_response(),
        ]
        mock_verifier.side_effect = [
            VerificationResult(
                verdict="needs_retry", score=2, retry_instruction="Fix the issues"
            ),
            VerificationResult(verdict="pass", score=5),
        ]

        result = delegate_with_verification(
            self.workers,
            "Alex",
            "Build a REST API",
            verifier_names=["Sophia"],
            max_retries=2,
        )

        self.assertEqual(result["final_status"], "done")
        self.assertEqual(mock_worker.call_count, 2)
        self.assertEqual(mock_verifier.call_count, 2)

    @patch("manager._run_verifier")
    @patch("manager.run_worker")
    def test_delegate_max_retries_exceeded(
        self, mock_worker: MagicMock, mock_verifier: MagicMock
    ) -> None:
        mock_worker.return_value = self._make_needs_retry_response()
        mock_verifier.return_value = VerificationResult(
            verdict="needs_retry", score=1, retry_instruction="Still not good"
        )

        result = delegate_with_verification(
            self.workers,
            "Alex",
            "Build a REST API",
            verifier_names=["Sophia"],
            max_retries=2,
        )

        self.assertEqual(result["final_status"], "failed")
        self.assertIn("超过最大重试次数", result.get("reason", ""))
        # 3 attempts: initial + 2 retries
        self.assertEqual(mock_worker.call_count, 3)
        self.assertEqual(mock_verifier.call_count, 3)

    @patch("manager.run_worker")
    def test_delegate_no_verifiers(self, mock_worker: MagicMock) -> None:
        mock_worker.return_value = self._make_success_response()

        result = delegate_with_verification(
            self.workers,
            "Alex",
            "Build a REST API",
            verifier_names=[],
        )

        self.assertEqual(result["final_status"], "success")
        self.assertIsNone(result["verification"])
        mock_worker.assert_called_once()

    @patch("manager._run_verifier")
    @patch("manager.run_worker")
    def test_delegate_budget_exceeded(
        self, mock_worker: MagicMock, mock_verifier: MagicMock
    ) -> None:
        mock_worker.return_value = self._make_success_response()
        # Verifier returns reject so the loop retries, hitting budget on attempt 2
        mock_verifier.return_value = VerificationResult(
            verdict="reject", score=1, retry_instruction="Redo everything"
        )

        budget = Budget(max_attempts=1, max_model_calls=100)

        result = delegate_with_verification(
            self.workers,
            "Alex",
            "Build a REST API",
            verifier_names=["Sophia"],
            max_retries=3,
            budget=budget,
        )

        self.assertEqual(result["final_status"], "failed")
        self.assertIn("max_attempts", result.get("reason", ""))
        # run_worker called on attempt 1 and attempt 2 (before budget check fails)
        self.assertEqual(mock_worker.call_count, 2)
        # verifier only called on attempt 1 (budget check fails on attempt 2 before
        # reaching verifiers)
        self.assertEqual(mock_verifier.call_count, 1)

    @patch("manager._run_verifier")
    @patch("manager.run_worker")
    def test_delegate_needs_replan(
        self, mock_worker: MagicMock, mock_verifier: MagicMock
    ) -> None:
        mock_worker.return_value = self._make_success_response()
        mock_verifier.return_value = VerificationResult(
            verdict="needs_replan", score=1, retry_instruction="Architecture is wrong"
        )

        result = delegate_with_verification(
            self.workers,
            "Alex",
            "Build a REST API",
            verifier_names=["Sophia"],
        )

        self.assertEqual(result["final_status"], "needs_replan")
        self.assertIn("验证者判定需要重新规划", result.get("reason", ""))
        mock_worker.assert_called_once()
        mock_verifier.assert_called_once()


if __name__ == "__main__":
    unittest.main()
