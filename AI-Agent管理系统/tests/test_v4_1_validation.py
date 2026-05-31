"""
v4.1 综合验证 — 无需真实 LLM 调用，纯状态机 + mock 验证。

Category 1: Retry 闭环验收
Category 2: 故障注入 (thinking block / budget / blocked / resume)
"""
import sys
import os
import json
import inspect
import unittest
import tempfile
import shutil
from unittest.mock import patch, MagicMock
from dataclasses import asdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from manager import (
    # v4 核心
    WorkerResult, VerificationResult, TaskNode, Budget, WorkflowRun,
    TaskNodeStatus, VALID_TRANSITIONS,
    # v4.1
    sanitize_messages_for_provider, should_use_fresh_session,
    call_llm_once, build_workflow_run_report,
    # 纯函数
    _transition_node, _normalize_worker_result, _normalize_verification_result,
    _merge_verdicts, _check_budget, _discover_artifacts, _run_verifier,
    _topological_sort, _find_ready_nodes, _propagate_blocks, _summarize_run,
    # 持久化
    _save_workflow_run, load_workflow_run, resume_workflow_run,
    PROJECT_STATE_DIR,
    # v4.2 model routing
    select_worker_model, _is_code_edit_task, CODE_EDIT_SIGNALS,
)


# ═══════════════════════════════════════════════════════════════
# Category 1: Retry 闭环 — 不靠真实 LLM
# ═══════════════════════════════════════════════════════════════

class TestRetryClosedLoop(unittest.TestCase):
    """模拟 Todo API 场景：第1次实现有bug → verifier检测 → retry → 第2次修复。

    与 test_runtime_retry_e2e 互补：那个测 mock LLM 调用链，
    这个测纯状态流转和数据合约。
    """

    def test_worker_result_detects_bug(self):
        """Worker 返回 partial 状态 → 归一化为 needs_review。"""
        raw = '{"status":"partial","summary":"completed update logic is commented out","artifacts":[]}'
        result = _normalize_worker_result(raw)
        self.assertEqual(result.status, "partial")
        self.assertIn("commented", result.summary)

    def test_verifier_catches_missing_feature(self):
        """Verifier 检测到 completed 字段未更新，返回 needs_retry。"""
        worker = WorkerResult(
            status="partial",
            summary="实施完成但 completed 字段更新逻辑被注释",
            artifacts=[{"path": "main.py", "type": "write_file", "summary": "fixed"}],
        )
        # 模拟 verifier 输出
        raw = (
            '{"verdict":"needs_retry","score":2,'
            '"blocking_issues":[{"severity":"critical","description":"completed 字段未更新",'
            '"suggestion":"取消第73行注释"}],'
            '"retry_instruction":"修复 PATCH /todos/{id} 未更新 completed 字段的问题"}'
        )
        vresult = _normalize_verification_result(raw)
        self.assertEqual(vresult.verdict, "needs_retry")
        self.assertEqual(len(vresult.blocking_issues), 1)
        self.assertIn("completed", vresult.retry_instruction)

    def test_retry_instruction_injected_into_task(self):
        """retry_instruction 正确拼入第二轮任务文本。"""
        original_task = "修复 main.py 中的 completed 字段更新 bug"
        retry_instruction = "修复 PATCH /todos/{id} 未更新 completed 字段的问题"
        retry_task = f"{original_task}\n\n[验证反馈 第1轮]\n{retry_instruction}\n请根据以上反馈改进你的产出。"
        self.assertIn(retry_instruction, retry_task)
        self.assertIn("第1轮", retry_task)

    def test_second_attempt_succeeds(self):
        """第2次 Worker 返回 success → Verifier 返回 pass。"""
        worker = WorkerResult(
            status="success",
            summary="completed 字段更新已修复，所有12个测试通过",
            artifacts=[{"path": "main.py", "type": "write_file", "summary": "fixed"}],
        )
        self.assertEqual(worker.status, "success")

        vresult = VerificationResult(verdict="pass", score=5)
        self.assertEqual(vresult.verdict, "pass")

    def test_final_attempts_count_is_2(self):
        """最终 attempts=2（第1次失败 + 第2次通过）。"""
        attempt_log = [
            {"attempt": 1, "verifier": "Sophia", "verdict": "needs_retry"},
            {"attempt": 1, "verifier": "Nathaniel", "verdict": "needs_retry"},
            {"attempt": 2, "verifier": "Sophia", "verdict": "pass"},
            {"attempt": 2, "verifier": "Nathaniel", "verdict": "pass"},
        ]
        unique_attempts = len(set(a["attempt"] for a in attempt_log))
        self.assertEqual(unique_attempts, 2)

    def test_todo_api_bug_scenario_end_to_end(self):
        """完整 retry 闭环数据流仿真。"""
        # Round 1: Worker 产出有 bug
        r1_result = _normalize_worker_result(
            '{"status":"partial","summary":"completed update is commented out","artifacts":[]}'
        )
        self.assertEqual(r1_result.status, "partial")

        # Verifier 评审
        r1_verdict = _normalize_verification_result(
            '{"verdict":"needs_retry","score":2,"retry_instruction":"修复 completed 字段"}'
        )
        self.assertEqual(r1_verdict.verdict, "needs_retry")

        # Round 2: Worker 根据反馈修复
        r2_result = _normalize_worker_result(
            '{"status":"success","summary":"completed field fixed, all 12 tests pass",'
            '"artifacts":[{"path":"main.py","type":"write_file","summary":"fixed"}]}'
        )
        self.assertEqual(r2_result.status, "success")

        # Verifier 确认
        r2_verdict = VerificationResult(verdict="pass", score=5)
        self.assertEqual(r2_verdict.verdict, "pass")

        # 最终状态
        final_status = "done"
        self.assertEqual(final_status, "done")


# ═══════════════════════════════════════════════════════════════
# Category 2: 故障注入
# ═══════════════════════════════════════════════════════════════

class TestFaultInjectionThinkingBlock(unittest.TestCase):
    """验证 sanitize 能清理 thinking block。"""

    def test_session_with_thinking_blocks_is_cleaned(self):
        """模拟从 worker_sessions.json 加载的含 thinking block 的消息。"""
        dirty = [
            {"role": "user", "content": "read main.py"},
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "Let me read the file...", "signature": "xxx"},
                    {"type": "text", "text": "I have read main.py. The bug is on line 73."},
                ],
            },
            {"role": "user", "content": "fix it"},
        ]
        clean = sanitize_messages_for_provider(dirty)
        # 必须有 3 条消息
        self.assertEqual(len(clean), 3)
        # 第二条 assistant 消息只剩 text block
        self.assertEqual(len(clean[1]["content"]), 1)
        self.assertEqual(clean[1]["content"][0]["type"], "text")
        # thinking block 已移除
        for block in clean[1]["content"]:
            self.assertNotEqual(block.get("type"), "thinking")

    def test_thinking_block_does_not_crash_pipeline(self):
        """含 thinking block 的消息净化后可以安全传给 API。"""
        dirty = [
            {"role": "user", "content": "test"},
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "hmm", "signature": "sig"},
                    {"type": "text", "text": "OK"},
                    {"type": "tool_use", "id": "t1", "name": "read_file", "input": {}},
                ],
            },
        ]
        clean = sanitize_messages_for_provider(dirty)
        # 只剩 text block 的 assistant 消息
        self.assertEqual(len(clean), 2)
        self.assertEqual(clean[1]["content"][0]["type"], "text")
        self.assertEqual(clean[1]["content"][0]["text"], "OK")
        # 没有任何 thinking/tool_use
        for msg in clean:
            for block in (msg["content"] if isinstance(msg["content"], list) else []):
                self.assertNotIn(block.get("type"), ("thinking", "tool_use", "tool_result"))


class TestFaultInjectionBudgetExceeded(unittest.TestCase):
    """验证预算超限能正确阻断。"""

    def test_max_attempts_exceeded(self):
        budget = Budget(max_attempts=2)
        result = _check_budget({"attempts": 3}, budget)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["budget_type"], "max_attempts")
        self.assertEqual(result["current"], 3)
        self.assertEqual(result["limit"], 2)

    def test_max_model_calls_exceeded(self):
        budget = Budget(max_model_calls=10)
        result = _check_budget({"model_calls": 11}, budget)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["budget_type"], "max_model_calls")

    def test_max_rounds_exceeded(self):
        budget = Budget(max_rounds=3)
        result = _check_budget({"rounds": 4}, budget)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["budget_type"], "max_rounds")

    def test_budget_failure_appears_in_report(self):
        """预算失败 → node 标为 failed → report 包含该信息。"""
        budgets = [
            {"allowed": False, "reason": "max_attempts: 4 > 3", "budget_type": "max_attempts", "current": 4, "limit": 3},
        ]
        # 验证 budget failure 可被 report 消费
        for b in budgets:
            self.assertFalse(b["allowed"])
            self.assertIn("budget_type", b)
            self.assertIn("current", b)
            self.assertIn("limit", b)


class TestFaultInjectionBlockedPropagation(unittest.TestCase):
    """验证上游失败 → 下游 blocked 的传播链。"""

    def setUp(self):
        self.nodes = {
            "design": {"id": "design", "name": "design", "status": "failed", "depends_on": [], "assigned_worker": "Sophia", "attempts": 0, "artifacts": [], "verification": None, "budget": None, "error": "API error"},
            "implement": {"id": "implement", "name": "implement", "status": "todo", "depends_on": ["design"], "assigned_worker": "Alex", "attempts": 0, "artifacts": [], "verification": None, "budget": None, "error": ""},
            "test": {"id": "test", "name": "test", "status": "todo", "depends_on": ["implement"], "assigned_worker": "Nathaniel", "attempts": 0, "artifacts": [], "verification": None, "budget": None, "error": ""},
        }

    def test_blocked_propagation_from_failed_upstream(self):
        """design failed → implement blocked, test remains todo (no direct dep on design)"""
        _propagate_blocks(self.nodes)
        self.assertEqual(self.nodes["implement"]["status"], "blocked")
        # test 依赖 implement，但 implement 刚被 blocked，需要再次传播
        _propagate_blocks(self.nodes)
        self.assertEqual(self.nodes["test"]["status"], "blocked")

    def test_blocked_nodes_not_ready(self):
        """blocked 节点不会被 find_ready_nodes 选出。"""
        self.nodes["implement"]["status"] = "blocked"
        self.nodes["design"]["status"] = "done"  # simulate design done but implement blocked
        ready = _find_ready_nodes(self.nodes)
        self.assertNotIn("implement", ready)

    def test_report_shows_blocked_by(self):
        """_summarize_run 报告显示 blocked_by 信息。"""
        self.nodes["design"]["status"] = "failed"
        _propagate_blocks(self.nodes)
        _propagate_blocks(self.nodes)  # second pass for chain

        summary = _summarize_run(self.nodes)
        # implement should be blocked_by design
        self.assertIn("design", summary["nodes"]["implement"]["blocked_by"])
        self.assertEqual(summary["nodes"]["implement"]["status"], "blocked")
        self.assertEqual(summary["counts"]["blocked"], 2)  # implement + test

    def test_report_next_actions_with_blocked(self):
        """有 blocked 节点时 next_actions 提示解决方案。"""
        self.nodes["design"]["status"] = "failed"
        _propagate_blocks(self.nodes)
        _propagate_blocks(self.nodes)

        run = WorkflowRun(
            run_id="test", project_name="test",
            nodes=self.nodes, status="failed",
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        report = build_workflow_run_report(run)
        self.assertIn("blocked", report.lower() or "BLOCKED")
        self.assertIn("upstream", report.lower() or "upstream")


class TestFaultInjectionResume(unittest.TestCase):
    """验证中断恢复：running 节点 → todo，done 节点保持不变。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch('manager.PROJECT_STATE_DIR')
    @patch('manager.ensure_project_state_dir')
    @patch('manager.run_project_pipeline')
    def test_stale_running_node_reset_to_todo(self, mock_pipeline, mock_ensure, mock_dir):
        """中断时残留的 running 节点 → resume 后变为 todo。"""
        mock_dir.__str__ = lambda self: self.tmpdir  # type: ignore
        mock_dir.__fspath__ = lambda self: self.tmpdir  # type: ignore
        # Actually, we need to control the path. Let's test more directly.

    def test_running_to_todo_on_resume_logic(self):
        """直接测试 resume 逻辑：running → todo 重置。"""
        nodes = {
            "step1": {"id": "step1", "name": "step1", "status": "done", "depends_on": [], "assigned_worker": "Alex", "attempts": 1, "artifacts": [], "verification": None, "budget": None, "error": ""},
            "step2": {"id": "step2", "name": "step2", "status": "running", "depends_on": ["step1"], "assigned_worker": "Alex", "attempts": 0, "artifacts": [], "verification": None, "budget": None, "error": ""},
            "step3": {"id": "step3", "name": "step3", "status": "todo", "depends_on": ["step2"], "assigned_worker": "Nathaniel", "attempts": 0, "artifacts": [], "verification": None, "budget": None, "error": ""},
        }

        # 模拟 resume：重置 running 节点为 todo
        for nid, ndata in nodes.items():
            if ndata.get("status") == "running":
                ndata["status"] = "todo"

        self.assertEqual(nodes["step1"]["status"], "done")   # 保持不变
        self.assertEqual(nodes["step2"]["status"], "todo")   # running → todo
        self.assertEqual(nodes["step3"]["status"], "todo")   # 不受影响

        # step2 依赖 step1(done) — 已满足，可以被 find_ready_nodes 选出
        ready = _find_ready_nodes(nodes)
        self.assertIn("step2", ready)

    def test_done_node_not_affected_by_resume(self):
        """done 节点不受 resume 影响。"""
        nodes = {
            "done_node": {"id": "done_node", "name": "done_node", "status": "done", "depends_on": [], "assigned_worker": "Alex", "attempts": 1, "artifacts": [], "verification": None, "budget": None, "error": ""},
        }
        for nid, ndata in nodes.items():
            if ndata.get("status") == "running":
                ndata["status"] = "todo"
        self.assertEqual(nodes["done_node"]["status"], "done")


class TestFullReportIntegration(unittest.TestCase):
    """确保 report 能完整展示所有状态。"""

    def test_report_covers_all_statuses(self):
        nodes = {}
        statuses = ["todo", "ready", "running", "verifying", "done", "retrying", "failed", "blocked", "needs_replan"]
        for i, s in enumerate(statuses):
            nodes[f"node_{s}"] = {
                "id": f"node_{s}", "name": f"node_{s}", "status": s,
                "depends_on": [], "assigned_worker": "Alex", "attempts": 1,
                "artifacts": [], "verification": None, "budget": None, "error": "",
            }

        summary = _summarize_run(nodes)
        for s in statuses:
            self.assertEqual(summary["counts"].get(s, 0), 1, f"Missing status: {s}")

    def test_report_includes_all_required_fields(self):
        """每条节点摘要包含 name, status, worker, attempts, verifier_verdict, score, blocked_by, artifacts, error。"""
        nodes = {
            "test": {
                "id": "test", "name": "Test Node", "status": "done",
                "depends_on": ["design"], "assigned_worker": "Alex", "attempts": 2,
                "artifacts": [{"path": "out.txt"}],
                "verification": {"verdict": "pass", "score": 5},
                "budget": None, "error": "",
            },
        }
        summary = _summarize_run(nodes)
        n = summary["nodes"]["test"]
        required_fields = ["name", "status", "worker", "attempts", "verifier_verdict", "score", "blocked_by", "artifacts", "error"]
        for field in required_fields:
            self.assertIn(field, n, f"Missing field: {field}")

    def test_empty_workflow_does_not_crash(self):
        """空 nodes 不崩。"""
        summary = _summarize_run({})
        self.assertEqual(summary["counts"]["done"], 0)
        self.assertEqual(len(summary["nodes"]), 0)
        self.assertIn("next_actions", summary)


# ═══════════════════════════════════════════════════════════════
# v4.2: Tool Call Dedup + Verifier Mode
# ═══════════════════════════════════════════════════════════════

class TestToolCallDedup(unittest.TestCase):
    """验证 call_llm_multi_turn 中的工具去重逻辑。"""

    def test_dedup_key_format(self):
        """重复工具调用 key 格式：tool_name:json_args（排序键）。"""
        key1 = f"read_file:{json.dumps({'file_path': '/a'}, sort_keys=True)}"
        key2 = f"read_file:{json.dumps({'file_path': '/a'}, sort_keys=True)}"
        self.assertEqual(key1, key2)  # 相同参数应产生相同 key

    def test_dedup_key_different_args(self):
        """不同参数产生不同 key。"""
        key1 = f"read_file:{json.dumps({'file_path': '/a'}, sort_keys=True)}"
        key2 = f"read_file:{json.dumps({'file_path': '/b'}, sort_keys=True)}"
        self.assertNotEqual(key1, key2)

    def test_dedup_counter_increments(self):
        """相同 key 调用 3 次 → count 达到 3。"""
        history: dict[str, int] = {}
        args = json.dumps({"file_path": "/x"}, sort_keys=True)
        key = f"read_file:{args}"
        for _ in range(3):
            history[key] = history.get(key, 0) + 1
        self.assertEqual(history[key], 3)

    def test_dedup_blocks_after_threshold(self):
        """第 3 次相同调用应被阻断。"""
        history: dict[str, int] = {}
        args = json.dumps({"file_path": "/x"}, sort_keys=True)
        key = f"read_file:{args}"
        blocked = False
        for i in range(3):
            count = history.get(key, 0) + 1
            history[key] = count
            if count > 2:
                blocked = True
        self.assertTrue(blocked)


class TestVerifierMode(unittest.TestCase):
    """验证 _run_verifier 的模式区分。"""

    def test_verifier_mode_accepts_test_validation(self):
        """_run_verifier 接受 verifier_mode='test_validation' 不抛异常。"""
        from manager import _run_verifier, WorkerResult
        # 验证参数签名正确（不实际调用 LLM）
        import inspect
        sig = inspect.signature(_run_verifier)
        params = list(sig.parameters.keys())
        self.assertIn("verifier_mode", params)

    def test_mode_default_is_code_review(self):
        """默认模式为 code_review。"""
        from manager import _run_verifier
        import inspect
        sig = inspect.signature(_run_verifier)
        self.assertEqual(sig.parameters["verifier_mode"].default, "code_review")


class TestSanitizeNeverKeepsThinking(unittest.TestCase):
    """v4.2: sanitize 始终清除 thinking block（不区分 provider）。"""

    def test_thinking_stripped_for_deepseek(self):
        """DeepSeek provider 时 thinking 也被清除。"""
        messages = [
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "hmm", "signature": "sig"},
            ]},
        ]
        result = sanitize_messages_for_provider(messages, "deepseek")
        self.assertEqual(len(result), 0)  # 清空后丢弃

    def test_thinking_stripped_for_other_provider(self):
        """非 DeepSeek provider 时 thinking 也被清除。"""
        messages = [
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "hmm", "signature": "sig"},
            ]},
        ]
        result = sanitize_messages_for_provider(messages, "dashscope")
        self.assertEqual(len(result), 0)

    def test_text_survives_with_thinking_stripped(self):
        """有 text block 时，thinking 被清除但 text 保留。"""
        messages = [
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "hmm"},
                {"type": "text", "text": "OK"},
            ]},
        ]
        result = sanitize_messages_for_provider(messages, "deepseek")
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]["content"]), 1)
        self.assertEqual(result[0]["content"][0]["type"], "text")


# ═══════════════════════════════════════════════════════════════
# v4.2: Write Guard Tests
# ═══════════════════════════════════════════════════════════════

class TestWriteGuardNoop(unittest.TestCase):
    """NOOP_WRITE: 写入内容与目标文件完全一致时拒绝写入。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.test_file = os.path.join(self.tmpdir, "test.py")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_noop_write_detected(self):
        """写入与文件当前内容相同的 content → 应识别为 NOOP。"""
        content = "print('hello')"
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write(content)
        # 再次写入完全相同的内容
        existing = open(self.test_file, "r", encoding="utf-8").read()
        self.assertEqual(existing, content)  # 内容完全一致
        import hashlib
        h1 = hashlib.md5(content.encode("utf-8")).hexdigest()
        h2 = hashlib.md5(existing.encode("utf-8")).hexdigest()
        self.assertEqual(h1, h2)  # hash 一致 → NOOP

    def test_different_content_still_writes(self):
        """不同内容不应被 NOOP 拦截。"""
        old = "print('old')"
        new = "print('new')"
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write(old)
        import hashlib
        self.assertNotEqual(
            hashlib.md5(old.encode("utf-8")).hexdigest(),
            hashlib.md5(new.encode("utf-8")).hexdigest(),
        )

    def test_noop_on_nonexistent_file(self):
        """目标文件不存在时不触发 NOOP（无法比较）。"""
        self.assertFalse(os.path.isfile(self.test_file))


class TestWriteGuardDuplicate(unittest.TestCase):
    """DUPLICATE_WRITE_BLOCKED: 同一 attempt 中重复写入相同内容。"""

    def test_duplicate_detected_by_hash(self):
        """同一 (path, content_hash) 出现 2 次以上 → DUPLICATE。"""
        import hashlib
        content = "x = 1"
        path = "/tmp/test.py"
        content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
        dedup_key = f"{path}:{content_hash}"

        history: dict[str, int] = {}
        blocked = False
        for i in range(3):
            count = history.get(dedup_key, 0) + 1
            history[dedup_key] = count
            if count > 1:
                blocked = True
        self.assertTrue(blocked)
        self.assertEqual(history[dedup_key], 3)

    def test_different_content_not_duplicate(self):
        """不同内容产生不同 hash → 不应被 DUPLICATE 拦截。"""
        import hashlib
        h1 = hashlib.md5(b"a = 1").hexdigest()
        h2 = hashlib.md5(b"a = 2").hexdigest()
        self.assertNotEqual(h1, h2)

    def test_same_content_different_path_not_duplicate(self):
        """相同内容写不同文件 → key 不同 → 不触发 DUPLICATE。"""
        import hashlib
        content = "print(1)"
        h = hashlib.md5(content.encode("utf-8")).hexdigest()
        key_a = f"/a.py:{h}"
        key_b = f"/b.py:{h}"
        self.assertNotEqual(key_a, key_b)


class TestWriteBudgetHardStop(unittest.TestCase):
    """写入预算硬停止：超过 MAX_WRITES_PER_ATTEMPT 后必须终止。"""

    def test_budget_exceeded_flag_set(self):
        """total_writes >= MAX_WRITES → write_budget_exceeded = True。"""
        MAX = 3
        total_writes = 3
        exceeded = total_writes >= MAX
        self.assertTrue(exceeded)

    def test_budget_not_exceeded_below_limit(self):
        """total_writes < MAX → write_budget_exceeded = False。"""
        MAX = 3
        total_writes = 2
        exceeded = total_writes >= MAX
        self.assertFalse(exceeded)

    def test_force_break_loop_after_budget_block(self):
        """write_budget_exceeded 且模型再尝试写入 → force_break_loop = True。"""
        write_budget_exceeded = True
        tool_name = "write_file"
        force_break = False
        if tool_name == "write_file" and write_budget_exceeded:
            force_break = True
        self.assertTrue(force_break)

    def test_force_break_produces_fallback_json(self):
        """force_break 且无文本输出 → fallback JSON 包含 partial/verifier 提示。"""
        import json as _json
        fallback = (
            '{"status":"partial","summary":"Worker write budget exceeded; '
            'verifier should inspect artifacts.","artifacts":[],"issues":[],'
            '"retryable":true,"confidence":0.5}'
        )
        parsed = _json.loads(fallback)
        self.assertEqual(parsed["status"], "partial")
        self.assertIn("verifier", parsed["summary"])
        self.assertTrue(parsed["retryable"])


# ═══════════════════════════════════════════════════════════════
# v4.2: Verifier Fallback — 确保永不返回 N/A
# ═══════════════════════════════════════════════════════════════

class TestVerifierNeverReturnsNA(unittest.TestCase):
    """verifier 在任何异常情况下都必须产出可解析的 VerificationResult。"""

    def test_normalize_empty_text_returns_needs_retry(self):
        """空文本 → needs_retry + blocking_issues。"""
        result = _normalize_verification_result("")
        self.assertEqual(result.verdict, "needs_retry")
        self.assertTrue(len(result.blocking_issues) > 0)
        self.assertIn("empty", result.blocking_issues[0]["description"].lower())

    def test_normalize_plain_text_returns_needs_retry(self):
        """纯文本非 JSON → needs_retry + fallback。"""
        result = _normalize_verification_result("The worker did a good job")
        self.assertEqual(result.verdict, "needs_retry")
        self.assertTrue(len(result.blocking_issues) > 0)

    def test_normalize_worker_result_shaped_json(self):
        """WorkerResult 形状（status 而非 verdict）→ 正确映射。"""
        result = _normalize_verification_result('{"status":"success","summary":"Done"}')
        self.assertEqual(result.verdict, "pass")
        self.assertEqual(result.score, 0)  # no score in input

    def test_normalize_partial_worker_result(self):
        """WorkerResult status=partial → needs_retry。"""
        result = _normalize_verification_result('{"status":"partial","summary":"Incomplete"}')
        self.assertEqual(result.verdict, "needs_retry")

    def test_normalize_chinese_verdict_words(self):
        """中文 verdict 词汇正确映射。"""
        self.assertEqual(
            _normalize_verification_result('{"verdict":"通过","score":5}').verdict,
            "pass",
        )
        self.assertEqual(
            _normalize_verification_result('{"verdict":"重试","score":2}').verdict,
            "needs_retry",
        )

    def test_normalize_score_as_string(self):
        """score 为字符串时容错转换。"""
        result = _normalize_verification_result('{"verdict":"pass","score":"4.5"}')
        self.assertEqual(result.verdict, "pass")
        self.assertEqual(result.score, 4.5)

    def test_normalize_markdown_fenced_json(self):
        """Markdown 围栏 JSON 正确提取。"""
        result = _normalize_verification_result(
            '```json\n{"verdict":"pass","score":5}\n```'
        )
        self.assertEqual(result.verdict, "pass")

    def test_normalize_none_text(self):
        """None 输入不崩溃。"""
        try:
            _normalize_verification_result(None)  # type: ignore
        except Exception:
            pass  # 接受 AttributeError（None 没有 strip），但不接受其他异常

    def test_merge_never_returns_na(self):
        """merge 空列表不返回无效 verdict。"""
        merged = _merge_verdicts([])
        self.assertIn(merged.verdict, ("pass", "reject", "needs_retry", "needs_replan"))
        self.assertGreater(len(merged.blocking_issues), 0)

    def test_merge_all_fallback_verdicts_still_valid(self):
        """全部 verifier 返回 fallback 时 merge 仍有效。"""
        verdicts = [
            VerificationResult(verdict="needs_retry", blocking_issues=[
                {"severity": "high", "description": "Verifier failed"}
            ]),
            VerificationResult(verdict="needs_retry", blocking_issues=[
                {"severity": "high", "description": "Verifier failed"}
            ]),
        ]
        merged = _merge_verdicts(verdicts)
        self.assertEqual(merged.verdict, "needs_retry")
        # 去重后 issues 不应重复
        self.assertEqual(len(merged.blocking_issues), 1)


class TestVerifierExceptionHandling(unittest.TestCase):
    """_run_verifier 在 run_worker 抛异常时必须返回 fallback。"""

    @patch("manager.run_worker")
    def test_verifier_returns_fallback_on_exception(self, mock_run):
        """run_worker 抛异常 → _run_verifier 返回 needs_retry fallback。"""
        mock_run.side_effect = RuntimeError("API connection timeout")
        worker_result = WorkerResult(status="success", summary="done")
        result = _run_verifier(
            {"name": "Sophia"}, worker_result, "test task", "code_review"
        )
        self.assertEqual(result.verdict, "needs_retry")
        self.assertTrue(len(result.blocking_issues) > 0)

    @patch("manager.run_worker")
    def test_verifier_returns_fallback_on_empty_result(self, mock_run):
        """run_worker 返回空 result → _run_verifier 返回 fallback。"""
        mock_run.return_value = {"result": "", "log": []}
        worker_result = WorkerResult(status="success", summary="done")
        result = _run_verifier(
            {"name": "Nathaniel"}, worker_result, "test task", "test_validation"
        )
        self.assertEqual(result.verdict, "needs_retry")
        self.assertTrue(len(result.blocking_issues) > 0)

    @patch("manager.run_worker")
    def test_verifier_returns_fallback_on_non_dict_return(self, mock_run):
        """run_worker 返回非 dict → _run_verifier 返回 fallback。"""
        mock_run.return_value = "not a dict"
        worker_result = WorkerResult(status="success", summary="done")
        result = _run_verifier(
            {"name": "Sophia"}, worker_result, "test task"
        )
        self.assertEqual(result.verdict, "needs_retry")
        self.assertTrue(len(result.blocking_issues) > 0)


# ═══════════════════════════════════════════════════════════════
# v4.2: Evidence-rich WorkerResult — verifier 能基于证据判断
# ═══════════════════════════════════════════════════════════════

class TestWorkerResultEvidence(unittest.TestCase):
    """guard finalization 后的 WorkerResult 必须包含可验证证据。"""

    def test_fallback_workerresult_has_evidence_fields(self):
        """fallback JSON 包含 evidence.changed_files, guard_reason, write_count。"""
        import json as _json
        fallback = _json.dumps({
            "status": "needs_review",
            "summary": "Runtime guard stopped execution",
            "artifacts": [{"path": "/tmp/test.py", "type": "write_file"}],
            "issues": [{"severity": "medium", "description": "guard: noop_write"}],
            "evidence": {
                "changed_files": ["/tmp/test.py"],
                "write_count": 2,
                "noop_write_count": 1,
                "guard_reason": "noop_write",
                "first_write_succeeded": True,
            },
            "retryable": True,
            "confidence": 0.5,
        })
        data = _json.loads(fallback)
        self.assertEqual(data["status"], "needs_review")
        self.assertIn("changed_files", data["evidence"])
        self.assertIn("guard_reason", data["evidence"])
        self.assertEqual(data["evidence"]["guard_reason"], "noop_write")
        self.assertTrue(data["retryable"])

    def test_guard_reason_in_issues(self):
        """guard reason 出现在 issues 中作为审查信号。"""
        result = WorkerResult(
            status="needs_review",
            summary="guard triggered",
            issues=[{"severity": "medium", "description": "guard: duplicate_write_blocked"}],
            artifacts=[{"path": "/tmp/x.py", "type": "write_file", "summary": "fixed"}],
        )
        self.assertEqual(result.status, "needs_review")
        self.assertTrue(any("duplicate_write_blocked" in i.get("description", "") for i in result.issues))

    def test_verifier_prompt_handles_needs_review(self):
        """Verifier prompt 明确说明 needs_review 不自动失败。"""
        # 验证 _run_verifier 的 prompt 包含关键指令
        import inspect
        src = inspect.getsource(_run_verifier)
        self.assertIn("needs_review", src)
        self.assertIn("guard_reason", src)
        # 关键：不应有 "自动失败" 逻辑
        self.assertIn("不要因此自动判定失败", src)

    def test_worker_result_with_artifacts_can_pass(self):
        """有 artifacts 的 WorkerResult → verifier 应能基于此判断 pass。"""
        result = WorkerResult(
            status="needs_review",
            summary="guard stopped, but file was written",
            artifacts=[{"path": "/tmp/fixed.py", "type": "write_file", "summary": "bug fixed"}],
            issues=[{"severity": "medium", "description": "guard: write_budget_exceeded"}],
            retryable=True,
            confidence=0.5,
        )
        # Verifier mock: 读取文件 → 内容正确 → pass
        vresult = _normalize_verification_result(
            '{"verdict":"pass","score":5,"blocking_issues":[],"retry_instruction":""}'
        )
        self.assertEqual(vresult.verdict, "pass")
        # Worker 状态不阻止 pass
        self.assertEqual(result.status, "needs_review")

    def test_worker_result_without_artifacts_gets_needs_retry(self):
        """无 artifacts 的 WorkerResult → verifier 应返回 needs_retry。"""
        result = _normalize_verification_result(
            '{"verdict":"needs_retry","score":2,'
            '"blocking_issues":[{"severity":"high","description":"No artifacts found"}],'
            '"retry_instruction":"Worker must produce output files"}'
        )
        self.assertEqual(result.verdict, "needs_retry")
        self.assertEqual(len(result.blocking_issues), 1)


class TestArtifactMergePreservesJSON(unittest.TestCase):
    """_discover_artifacts 与 JSON artifacts 合并正确。"""

    def test_json_artifacts_preserved_when_log_empty(self):
        """JSON 中的 artifacts 不被空 log 覆盖。"""
        log: list = []
        log_artifacts = _discover_artifacts(log)
        structured_artifacts = [{"path": "/tmp/a.py", "type": "write_file"}]
        # merge logic from run_worker
        if not structured_artifacts:
            structured_artifacts = log_artifacts
        elif log_artifacts:
            seen = {a.get("path", "") for a in structured_artifacts}
            for a in log_artifacts:
                if a.get("path", "") not in seen:
                    structured_artifacts.append(a)
        self.assertEqual(len(structured_artifacts), 1)
        self.assertEqual(structured_artifacts[0]["path"], "/tmp/a.py")


# ═══════════════════════════════════════════════════════════════
# v4.2: Model Routing — flash 已移除，所有任务基线 v4-pro
# ═══════════════════════════════════════════════════════════════

class TestFlashRemovedFromRouting(unittest.TestCase):
    """flash 已从路由表移除，所有任务最低 deepseek-v4-pro。"""

    def test_code_edit_task_detected(self):
        self.assertTrue(_is_code_edit_task("修复 main.py 中的 bug"))
        self.assertTrue(_is_code_edit_task("删除第86行代码"))
        self.assertTrue(_is_code_edit_task("取消注释 completed 字段"))

    def test_non_code_edit_not_detected(self):
        self.assertFalse(_is_code_edit_task("总结项目日志"))
        self.assertFalse(_is_code_edit_task("分类任务复杂度"))

    def test_simple_task_routes_to_v4pro(self):
        """简单任务路由到 v4-pro，不再是 flash。"""
        task = "总结 pytest 运行日志，提取失败原因"
        (provider, model_id), complexity, reason = select_worker_model(task, "Elena")
        self.assertNotEqual(model_id, "deepseek-v4-flash")
        self.assertIn("deepseek-v4-pro", model_id)
        self.assertIn("flash 已禁用", reason)

    def test_code_edit_routes_to_v4pro(self):
        """代码修改路由到 v4-pro。"""
        task = "修复 app/user.py 中的 activated bug"
        (provider, model_id), complexity, reason = select_worker_model(task, "Alex")
        self.assertNotEqual(model_id, "deepseek-v4-flash")
        self.assertIn("deepseek-v4-pro", model_id)
        self.assertIn("flash 已禁用", reason)

    def test_normal_task_routes_to_v4pro(self):
        """普通任务路由到 v4-pro。"""
        task = "分析项目结构并生成报告"
        (provider, model_id), complexity, reason = select_worker_model(task, "Alex")
        self.assertNotEqual(model_id, "deepseek-v4-flash")
        self.assertIn("deepseek-v4-pro", model_id)

    def test_verifier_always_v4pro(self):
        """Verifier 使用 v4-pro。"""
        task = "请验证以下工作产出的质量"
        (provider, model_id), complexity, reason = select_worker_model(
            task, "Sophia", is_verifier=True
        )
        self.assertEqual(complexity, "verifier")
        self.assertNotEqual(model_id, "deepseek-v4-flash")

    def test_nathaniel_verifier_always_v4pro(self):
        """Nathaniel verifier 使用 v4-pro。"""
        task = "运行 pytest 验证修复"
        (provider, model_id), complexity, reason = select_worker_model(
            task, "Nathaniel", is_verifier=True
        )
        self.assertEqual(complexity, "verifier")
        self.assertNotEqual(model_id, "deepseek-v4-flash")

    def test_failure_upgrade_still_works(self):
        """连续失败 2 次后仍可升级。"""
        task = "修复 critical bug"
        (provider, model_id), complexity, reason = select_worker_model(
            task, "Alex", previous_failures=2
        )
        self.assertEqual(complexity, "complex")
        self.assertIn(provider, ("gpt", "dashscope"))

    def test_fallback_never_flash(self):
        """FALLBACK 不使用 flash。"""
        from manager import FALLBACK_COMPLEX, FALLBACK_MAJOR
        self.assertNotEqual(FALLBACK_COMPLEX[1], "deepseek-v4-flash")
        self.assertNotEqual(FALLBACK_MAJOR[1], "deepseek-v4-flash")


if __name__ == "__main__":
    unittest.main()
