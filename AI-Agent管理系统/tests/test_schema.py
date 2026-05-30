"""
Tests for normalization/parsing functions in manager.py.
Covers: _extract_json_from_text, _normalize_worker_result,
        _normalize_verification_result, _merge_verdicts.
"""
import sys
import os
import json
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from manager import (  # type: ignore[import-untyped]
    _normalize_worker_result,
    _normalize_verification_result,
    _merge_verdicts,
    _extract_json_from_text,
    WorkerResult,
    VerificationResult,
)


class TestExtractJsonFromText(unittest.TestCase):
    """Tests for _extract_json_from_text."""

    def test_extract_json_fenced(self) -> None:
        raw = 'Here is the result:\n```json\n{"status": "success"}\n```\nDone.'
        result = _extract_json_from_text(raw)
        self.assertIsNotNone(result)
        self.assertIn('"status"', result)
        parsed = json.loads(result)
        self.assertEqual(parsed["status"], "success")

    def test_extract_json_fenced_no_lang(self) -> None:
        raw = "```\n{\"key\": 42}\n```"
        result = _extract_json_from_text(raw)
        self.assertIsNotNone(result)
        parsed = json.loads(result)
        self.assertEqual(parsed["key"], 42)

    def test_extract_json_bare(self) -> None:
        raw = 'The data is {"name": "test", "value": 123} somewhere.'
        result = _extract_json_from_text(raw)
        self.assertIsNotNone(result)
        parsed = json.loads(result)
        self.assertEqual(parsed["name"], "test")
        self.assertEqual(parsed["value"], 123)

    def test_extract_json_none(self) -> None:
        raw = "This is just plain text with no JSON."
        result = _extract_json_from_text(raw)
        self.assertIsNone(result)

    def test_extract_json_nested_braces(self) -> None:
        raw = '{"outer": {"inner": true}}'
        result = _extract_json_from_text(raw)
        self.assertIsNotNone(result)
        parsed = json.loads(result)
        self.assertIsInstance(parsed["outer"], dict)
        self.assertTrue(parsed["outer"]["inner"])


class TestNormalizeWorkerResult(unittest.TestCase):
    """Tests for _normalize_worker_result."""

    def test_normalize_worker_valid_json(self) -> None:
        raw = (
            '```json\n'
            '{"status": "success", "summary": "Done", '
            '"artifacts": [{"path": "/tmp/a.py", "type": "code", "summary": "script"}], '
            '"confidence": 0.95}\n'
            '```'
        )
        result = _normalize_worker_result(raw)
        self.assertIsInstance(result, WorkerResult)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.summary, "Done")
        self.assertEqual(len(result.artifacts), 1)
        self.assertEqual(result.artifacts[0]["path"], "/tmp/a.py")
        self.assertEqual(result.confidence, 0.95)

    def test_normalize_worker_free_text(self) -> None:
        raw = "I tried my best but the task was too complex and I could not finish."
        result = _normalize_worker_result(raw)
        self.assertIsInstance(result, WorkerResult)
        self.assertEqual(result.status, "needs_review")
        self.assertEqual(result.raw_text, raw)

    def test_normalize_worker_partial_json(self) -> None:
        raw = '```json\n{"summary": "Partial work completed"}\n```'
        result = _normalize_worker_result(raw)
        self.assertIsInstance(result, WorkerResult)
        # status defaults to "needs_review" when missing from JSON
        self.assertEqual(result.status, "needs_review")
        self.assertEqual(result.summary, "Partial work completed")
        # Defaults for missing fields
        self.assertEqual(result.artifacts, [])
        self.assertEqual(result.issues, [])
        self.assertEqual(result.confidence, 0.8)
        self.assertFalse(result.needs_replan)
        self.assertTrue(result.retryable)

    def test_normalize_worker_with_artifacts_override(self) -> None:
        raw = "Free text with no JSON"
        supplied = [{"path": "out.txt", "type": "file", "summary": "output"}]
        result = _normalize_worker_result(raw, artifacts=supplied)
        self.assertEqual(result.artifacts, supplied)

    def test_normalize_worker_malformed_json(self) -> None:
        raw = "```json\n{broken json!!!\n```"
        result = _normalize_worker_result(raw)
        # Falls back to free-text handling
        self.assertEqual(result.status, "needs_review")
        self.assertEqual(result.raw_text, raw)

    def test_normalize_worker_issues_field(self) -> None:
        raw = (
            '{"status": "partial", "summary": "has bugs", '
            '"issues": [{"severity": "high", "description": "null ptr", "suggestion": "add check"}]}'
        )
        result = _normalize_worker_result(raw)
        self.assertEqual(result.status, "partial")
        self.assertEqual(len(result.issues), 1)
        self.assertEqual(result.issues[0]["severity"], "high")


class TestNormalizeVerificationResult(unittest.TestCase):
    """Tests for _normalize_verification_result."""

    def test_normalize_verification_pass(self) -> None:
        raw = '```json\n{"verdict": "pass", "score": 5}\n```'
        result = _normalize_verification_result(raw)
        self.assertIsInstance(result, VerificationResult)
        self.assertEqual(result.verdict, "pass")
        self.assertEqual(result.score, 5.0)

    def test_normalize_verification_reject(self) -> None:
        raw = (
            '{"verdict": "reject", "score": 1, '
            '"blocking_issues": [{"severity": "critical", '
            '"description": "security hole", "suggestion": "sanitize input"}], '
            '"retry_instruction": "Fix the SQL injection"}'
        )
        result = _normalize_verification_result(raw)
        self.assertEqual(result.verdict, "reject")
        self.assertEqual(result.score, 1.0)
        self.assertEqual(len(result.blocking_issues), 1)
        self.assertEqual(result.blocking_issues[0]["severity"], "critical")
        self.assertEqual(result.retry_instruction, "Fix the SQL injection")

    def test_normalize_verification_free_text(self) -> None:
        raw = "Looks ok to me, ship it."
        result = _normalize_verification_result(raw)
        self.assertEqual(result.verdict, "needs_retry")
        self.assertEqual(result.raw_text, raw)

    def test_normalize_verification_partial_json(self) -> None:
        raw = '{"score": 3}'
        result = _normalize_verification_result(raw)
        self.assertEqual(result.verdict, "needs_retry")  # default
        self.assertEqual(result.score, 3.0)

    def test_normalize_verification_malformed_json(self) -> None:
        raw = "```json\n{not valid]\n```"
        result = _normalize_verification_result(raw)
        self.assertEqual(result.verdict, "needs_retry")
        self.assertEqual(result.raw_text, raw)


class TestMergeVerdicts(unittest.TestCase):
    """Tests for _merge_verdicts."""

    def test_merge_verdicts_worst_wins(self) -> None:
        verdicts = [
            VerificationResult(verdict="reject", blocking_issues=[
                {"severity": "critical", "description": "crash", "suggestion": ""}
            ]),
            VerificationResult(verdict="pass", score=5),
            VerificationResult(verdict="needs_retry", retry_instruction="try again"),
        ]
        merged = _merge_verdicts(verdicts)
        self.assertEqual(merged.verdict, "reject")

    def test_merge_verdicts_all_pass(self) -> None:
        verdicts = [
            VerificationResult(verdict="pass", score=4),
            VerificationResult(verdict="pass", score=5),
            VerificationResult(verdict="pass", score=3),
        ]
        merged = _merge_verdicts(verdicts)
        self.assertEqual(merged.verdict, "pass")
        self.assertEqual(merged.score, 4.0)  # (4+5+3)/3

    def test_merge_verdicts_empty(self) -> None:
        merged = _merge_verdicts([])
        self.assertEqual(merged.verdict, "needs_retry")
        self.assertEqual(merged.score, 0.0)
        self.assertEqual(merged.blocking_issues, [])

    def test_merge_verdicts_collects_issues(self) -> None:
        verdicts = [
            VerificationResult(verdict="pass", blocking_issues=[
                {"severity": "low", "description": "typo", "suggestion": "fix"}
            ]),
            VerificationResult(verdict="needs_retry", blocking_issues=[
                {"severity": "high", "description": "logic error", "suggestion": "rewrite"}
            ]),
        ]
        merged = _merge_verdicts(verdicts)
        self.assertEqual(merged.verdict, "needs_retry")  # needs_retry > pass
        self.assertEqual(len(merged.blocking_issues), 2)
        severities = {i["severity"] for i in merged.blocking_issues}
        self.assertIn("low", severities)
        self.assertIn("high", severities)

    def test_merge_verdicts_needs_replan_beats_needs_retry(self) -> None:
        verdicts = [
            VerificationResult(verdict="needs_retry"),
            VerificationResult(verdict="needs_replan"),
            VerificationResult(verdict="pass"),
        ]
        merged = _merge_verdicts(verdicts)
        self.assertEqual(merged.verdict, "needs_replan")

    def test_merge_verdicts_aggregates_instructions(self) -> None:
        verdicts = [
            VerificationResult(verdict="needs_retry", retry_instruction="Fix bug A"),
            VerificationResult(verdict="needs_retry", retry_instruction="Fix bug B"),
            VerificationResult(verdict="pass"),
        ]
        merged = _merge_verdicts(verdicts)
        self.assertIn("Fix bug A", merged.retry_instruction)
        self.assertIn("Fix bug B", merged.retry_instruction)


if __name__ == "__main__":
    unittest.main()
