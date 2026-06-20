import sys
import os
import json
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Ensure required env vars exist for module-level initialization in manager.py
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy-key")

from manager import (
    delegate_with_verification, WorkerResult, VerificationResult, Budget,
    _normalize_worker_result, _normalize_verification_result,
)


class TestRuntimeRetryE2E(unittest.TestCase):
    """E2E tests for the verify-reject-retry closed loop in delegate_with_verification."""

    def setUp(self) -> None:
        self.workers: dict = {
            "Alex": {"name": "Alex", "role": "Senior Developer", "tools": ["write_file"]},
            "Sophia": {"name": "Sophia", "role": "Code Reviewer", "tools": ["read_file"]},
            "Nathaniel": {"name": "Nathaniel", "role": "Test Engineer", "tools": ["run_command"]},
        }

    # ── helpers ──────────────────────────────────────────────────

    def _worker_response(self, text: str) -> dict:
        """Build a run_worker return dict from a raw result text."""
        return {"result": text, "log": []}

    # ── Test 1 ───────────────────────────────────────────────────

    @patch("runtime.verification._run_verifier")
    @patch("runtime.verification.run_worker")
    def test_retry_instruction_enters_second_task(
        self, mock_worker: MagicMock, mock_verifier: MagicMock
    ) -> None:
        """Retry instruction from needs_retry verdict is injected into the second task."""
        mock_worker.side_effect = [
            self._worker_response(
                '{"status":"partial","summary":"completed update logic is commented out","artifacts":[]}'
            ),
            self._worker_response(
                '{"status":"success","summary":"fixed completed field",'
                '"artifacts":[{"path":"main.py","type":"write_file","summary":"fixed"}]}'
            ),
        ]
        mock_verifier.side_effect = [
            VerificationResult(
                verdict="needs_retry",
                retry_instruction="修复 PATCH /todos/{id} 未更新 completed 字段的问题",
            ),
            VerificationResult(verdict="pass", score=5),
        ]

        result = delegate_with_verification(
            self.workers,
            "Alex",
            "Fix the todo API",
            verifier_names=["Sophia"],
            max_retries=2,
        )

        self.assertEqual(result["final_status"], "done")
        self.assertEqual(mock_worker.call_count, 2)

        # Verify retry_instruction was injected into the second task text
        second_task = mock_worker.call_args_list[1].args[1]
        self.assertIn(
            "修复 PATCH /todos/{id} 未更新 completed 字段的问题",
            second_task,
        )

    # ── Test 2 ───────────────────────────────────────────────────

    @patch("runtime.verification._run_verifier")
    @patch("runtime.verification.run_worker")
    def test_max_retries_exceeded(
        self, mock_worker: MagicMock, mock_verifier: MagicMock
    ) -> None:
        """All attempts return needs_retry; final_status becomes failed."""
        mock_worker.return_value = self._worker_response(
            '{"status":"needs_review","summary":"Still broken","artifacts":[]}'
        )
        mock_verifier.return_value = VerificationResult(
            verdict="needs_retry", score=1, retry_instruction="Try harder"
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
        # 3 attempts: 1 initial + 2 retries
        self.assertEqual(mock_worker.call_count, 3)
        self.assertEqual(mock_verifier.call_count, 3)

    # ── Test 3 ───────────────────────────────────────────────────

    @patch("runtime.verification._run_verifier")
    @patch("runtime.verification.run_worker")
    def test_pass_first_try(
        self, mock_worker: MagicMock, mock_verifier: MagicMock
    ) -> None:
        """Worker succeeds and verifier passes on the very first attempt."""
        mock_worker.return_value = self._worker_response(
            '{"status":"success","summary":"All done","artifacts":[]}'
        )
        mock_verifier.return_value = VerificationResult(verdict="pass", score=5)

        result = delegate_with_verification(
            self.workers,
            "Alex",
            "Build a REST API",
            verifier_names=["Sophia"],
        )

        self.assertEqual(result["final_status"], "done")
        self.assertEqual(mock_worker.call_count, 1)

    # ── Test 4 ───────────────────────────────────────────────────

    @patch("runtime.verification._run_verifier")
    @patch("runtime.verification.run_worker")
    def test_needs_replan_stops_immediately(
        self, mock_worker: MagicMock, mock_verifier: MagicMock
    ) -> None:
        """needs_replan verdict stops the loop immediately, no retry attempted."""
        mock_worker.return_value = self._worker_response(
            '{"status":"success","summary":"Built wrong architecture","artifacts":[]}'
        )
        mock_verifier.return_value = VerificationResult(
            verdict="needs_replan", score=1, retry_instruction="Redesign"
        )

        result = delegate_with_verification(
            self.workers,
            "Alex",
            "Build a system",
            verifier_names=["Sophia"],
        )

        self.assertEqual(result["final_status"], "needs_replan")
        mock_worker.assert_called_once()
        mock_verifier.assert_called_once()

    # ── Test 5 ───────────────────────────────────────────────────

    @patch("runtime.verification._run_verifier")
    @patch("runtime.verification.run_worker")
    def test_verdict_merge_worst_wins(
        self, mock_worker: MagicMock, mock_verifier: MagicMock
    ) -> None:
        """Two verifiers (pass vs reject) -- merge yields reject, final_status != done."""
        mock_worker.return_value = self._worker_response(
            '{"status":"success","summary":"Done","artifacts":[]}'
        )

        def verifier_side_effect(verifier_cfg: dict,
                                  worker_result: WorkerResult,
                                  original_task: str,
                                  verifier_mode: str = "code_review") -> VerificationResult:
            if verifier_cfg["name"] == "Sophia":
                return VerificationResult(verdict="pass", score=5)
            else:
                return VerificationResult(verdict="reject", score=1)

        mock_verifier.side_effect = verifier_side_effect

        result = delegate_with_verification(
            self.workers,
            "Alex",
            "Build a REST API",
            verifier_names=["Sophia", "Nathaniel"],
            max_retries=0,  # single attempt so reject triggers immediate failure
        )

        self.assertNotEqual(result["final_status"], "done")
        self.assertIsNotNone(result["verification"])
        self.assertEqual(result["verification"]["verdict"], "reject")


if __name__ == "__main__":
    unittest.main()
