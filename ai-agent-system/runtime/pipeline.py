"""DAG Pipeline 引擎 —— 拓扑排序、依赖检查、并行执行、收敛循环。

包含项目初始化的 project_setup（从自然语言生成 Pipeline + 分工表）。
"""
import json
import os
import re as _re2
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict

from runtime.contracts import TaskNode, WorkflowRun, Budget, TaskNodeStatus
from runtime.config import _init_providers
from runtime.llm import call_llm_once
from runtime.persistence import (
    PROJECT_STATE_DIR,
    load_project_state,
    save_project_state,
    update_project_step,
    save_workflow_run,
    _save_workflow_run,
    load_workflow_run,
)
from runtime.tools import print_lock
from runtime.verification import delegate_with_verification


# ── DAG 核心算法 ──────────────────────────────────────────

def _topological_sort(nodes: dict[str, dict]) -> list[str]:
    """Kahn 算法拓扑排序。检测循环，有环抛 ValueError。"""
    in_degree: dict[str, int] = {}
    adjacency: dict[str, set[str]] = {}

    for nid in nodes:
        in_degree[nid] = in_degree.get(nid, 0)
        adjacency[nid] = adjacency.get(nid, set())

    for nid, ndata in nodes.items():
        deps = ndata.get("depends_on", []) if isinstance(ndata, dict) else getattr(ndata, "depends_on", [])
        for dep in deps:
            adjacency.setdefault(dep, set()).add(nid)
            in_degree[nid] = in_degree.get(nid, 0) + 1

    queue = [nid for nid, deg in in_degree.items() if deg == 0]
    result: list[str] = []

    while queue:
        current = queue.pop(0)
        result.append(current)
        for neighbor in adjacency.get(current, set()):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(result) != len(nodes):
        raise ValueError(
            f"Cycle detected in DAG. Sorted {len(result)} of {len(nodes)} nodes. "
            f"Unsorted: {set(nodes.keys()) - set(result)}"
        )
    return result


def _find_ready_nodes(nodes: dict[str, dict]) -> list[str]:
    """找出所有就绪节点：上游全部 done，且自身为 todo。"""
    ready: list[str] = []
    for nid, ndata in nodes.items():
        status = ndata.get("status", "todo") if isinstance(ndata, dict) else getattr(ndata, "status", "todo")
        if status != "todo":
            continue
        deps = ndata.get("depends_on", []) if isinstance(ndata, dict) else getattr(ndata, "depends_on", [])
        all_deps_done = True
        for dep in deps:
            dep_status = nodes[dep].get("status", "todo") if isinstance(nodes[dep], dict) else getattr(nodes[dep], "status", "todo")
            if dep_status != "done":
                all_deps_done = False
                break
        if all_deps_done:
            ready.append(nid)
    return ready


def _propagate_blocks(nodes: dict[str, dict]) -> int:
    """迭代标记：上游 failed/blocked/needs_replan → 下游 blocked。返回标记数量。"""
    changed = 0
    for nid, ndata in nodes.items():
        status = ndata.get("status", "todo") if isinstance(ndata, dict) else getattr(ndata, "status", "todo")
        if status in ("done", "running", "retrying", "verifying"):
            continue
        deps = ndata.get("depends_on", []) if isinstance(ndata, dict) else getattr(ndata, "depends_on", [])
        for dep in deps:
            if dep not in nodes:
                continue
            dep_node = nodes[dep]
            dep_status = dep_node.get("status", "todo") if isinstance(dep_node, dict) else getattr(dep_node, "status", "todo")
            if dep_status in ("failed", "blocked", "needs_replan"):
                if status != "blocked":
                    if isinstance(ndata, dict):
                        ndata["status"] = "blocked"
                    else:
                        ndata.status = "blocked"
                    changed += 1
                break
    return changed


def _resolve_worker_for_step(step: dict, assignments: dict, workers: dict) -> str:
    """从 project_setup 的分配表匹配 Worker。"""
    step_name = step.get("name", "")
    step_desc = step.get("description", "")

    combined = step_name + step_desc
    keyword_map = {
        "Sophia": ("审查", "review", "检查", "审计"),
        "Alex": ("修复", "实现", "开发", "编写", "code", "develop", "fix"),
        "Nathaniel": ("测试", "test", "验证", "validate", "pytest"),
        "Elena": ("文档", "document", "readme", "说明"),
        "Marcus": ("部署", "deploy", "ci", "cd", "运维", "环境"),
    }
    for wname, keywords in keyword_map.items():
        for kw in keywords:
            if kw.lower() in combined.lower():
                if wname in workers:
                    return wname

    for wname, winfo in assignments.items():
        worker_tasks = winfo.get("tasks", [])
        for t in worker_tasks:
            if step_name in t:
                if wname in workers:
                    return wname

    desc_prefix = step_desc[:40]
    for wname, winfo in assignments.items():
        worker_tasks = winfo.get("tasks", [])
        for t in worker_tasks:
            if desc_prefix in t:
                if wname in workers:
                    return wname

    if "Alex" in workers:
        return "Alex"
    return next(iter(workers.keys()), "")


def _build_node_task(ndata: dict, state: dict) -> str:
    """根据节点信息构建 Worker 任务描述。v4.2: 预加载项目文件内容。"""
    name = ndata.get("name", "")
    desc = ndata.get("description", "")
    deps = ndata.get("depends_on", [])

    project_desc = state.get("project_description", "")
    project_dir_hint = ""
    file_context = ""
    if "C:\\" in project_desc or "/" in project_desc:
        paths = _re2.findall(r'[A-Z]:\\[^\s\n]+', project_desc)
        if paths:
            project_dir = paths[0]
            project_dir_hint = (
                f"\n⚠️ 项目目录：{project_dir}"
                f"\n所有文件操作使用绝对路径。"
            )
            for fname in ("main.py", "test_main.py"):
                fpath = os.path.join(project_dir, fname)
                if os.path.isfile(fpath):
                    try:
                        with open(fpath, "r", encoding="utf-8") as _pf:
                            content = _pf.read()
                        file_context += f"\n\n── {fname} 内容 ──\n{content}\n── {fname} 结束 ──"
                    except Exception:
                        pass

    dep_context = ""
    if deps:
        dep_context = (
            f"\n依赖的上游节点（已完成）: {', '.join(deps)}\n"
            "上游产出已保存在项目目录中，如需确认请用 read_file。"
        )

    review_plan = state.get("review_plan", {})
    review_hint = ""
    if review_plan:
        review_hint = (
            f"\n注意：你的产出将由 {review_plan.get('reviewer', 'Sophia')} 审查、"
            f"{review_plan.get('validator', 'Nathaniel')} 验证。请确保质量。"
        )

    return (
        f"[项目节点: {name}]\n"
        f"{desc}\n"
        f"{project_dir_hint}"
        f"{file_context}"
        f"{dep_context}"
        f"{review_hint}"
        f"\n\n⚠️ 重要：以上已提供了所有需要的文件内容，请直接完成任务，不要再用 read_file 读取！"
        f"\n完成后用 write_file 保存修改，并用 JSON 格式总结："
        f'{{"status": "success|partial|failed|needs_review", '
        f'"summary": "...", '
        f'"artifacts": [{{"path": "...", "type": "...", "summary": "..."}}], '
        f'"issues": [], "retryable": true, "confidence": 0.8}}'
    )


def _summarize_run(nodes: dict[str, dict], status: str = "") -> dict:
    """生成可审计的 pipeline 执行摘要。"""
    node_summaries = {}
    counts = {
        "todo": 0, "ready": 0, "running": 0, "verifying": 0,
        "done": 0, "retrying": 0, "failed": 0, "blocked": 0,
        "needs_replan": 0,
    }

    for nid, ndata in nodes.items():
        s = ndata.get("status", "todo") if isinstance(ndata, dict) else getattr(ndata, "status", "todo")
        if s in counts:
            counts[s] += 1

        deps = ndata.get("depends_on", []) if isinstance(ndata, dict) else getattr(ndata, "depends_on", [])
        blocked_by = []
        for dep in deps:
            if dep in nodes:
                dep_s = nodes[dep].get("status", "todo") if isinstance(nodes[dep], dict) else getattr(nodes[dep], "status", "todo")
                if dep_s in ("failed", "blocked", "needs_replan"):
                    blocked_by.append(dep)

        verification = ndata.get("verification") if isinstance(ndata, dict) else getattr(ndata, "verification", None)
        verifier_verdict = verification.get("verdict", "") if isinstance(verification, dict) else ""
        verifier_score = verification.get("score", 0) if isinstance(verification, dict) else 0

        node_summaries[nid] = {
            "name": ndata.get("name", nid) if isinstance(ndata, dict) else getattr(ndata, "name", nid),
            "status": s,
            "worker": ndata.get("assigned_worker", "") if isinstance(ndata, dict) else getattr(ndata, "assigned_worker", ""),
            "attempts": ndata.get("attempts", 0) if isinstance(ndata, dict) else getattr(ndata, "attempts", 0),
            "verifier_verdict": verifier_verdict,
            "score": verifier_score,
            "blocked_by": blocked_by,
            "artifacts": ndata.get("artifacts", []) if isinstance(ndata, dict) else getattr(ndata, "artifacts", []),
            "error": ndata.get("error", "") if isinstance(ndata, dict) else getattr(ndata, "error", ""),
        }

    if status:
        overall = status
    elif counts["failed"] > 0 or counts["blocked"] > 0 or counts["needs_replan"] > 0:
        overall = "failed" if counts["failed"] > 0 else "incomplete"
    elif counts["done"] == len(nodes):
        overall = "completed"
    elif counts["running"] > 0 or counts["todo"] > 0:
        overall = "in_progress"
    else:
        overall = "unknown"

    next_actions: list[str] = []
    if counts["needs_replan"] > 0:
        next_actions.append(f"{counts['needs_replan']} node(s) need replan — run request_replan")
    if counts["failed"] > 0:
        next_actions.append(f"{counts['failed']} node(s) failed — check error details and retry or replan")
    if counts["blocked"] > 0:
        next_actions.append(f"{counts['blocked']} node(s) blocked — resolve upstream failures first")
    if overall == "completed":
        next_actions.append("All nodes completed successfully. Run is done.")
    elif overall == "in_progress":
        next_actions.append("Run is still in progress. Use --resume to continue.")

    return {
        "overall_status": overall,
        "counts": counts,
        "nodes": node_summaries,
        "next_actions": next_actions,
    }


def build_workflow_run_report(workflow_run: dict | WorkflowRun) -> str:
    """生成标准化的 WorkflowRun 人类可读报告。"""
    nodes = workflow_run.nodes if isinstance(workflow_run, WorkflowRun) else workflow_run.get("nodes", {})
    status = workflow_run.status if isinstance(workflow_run, WorkflowRun) else workflow_run.get("status", "")
    run_id = workflow_run.run_id if isinstance(workflow_run, WorkflowRun) else workflow_run.get("run_id", "")
    project = workflow_run.project_name if isinstance(workflow_run, WorkflowRun) else workflow_run.get("project_name", "")
    created = workflow_run.created_at if isinstance(workflow_run, WorkflowRun) else workflow_run.get("created_at", "")
    updated = workflow_run.updated_at if isinstance(workflow_run, WorkflowRun) else workflow_run.get("updated_at", "")

    summary = _summarize_run(nodes, status)

    lines = [
        "=" * 65,
        f"  Workflow Run Report",
        "=" * 65,
        f"  Run ID:      {run_id}",
        f"  Project:     {project}",
        f"  Status:      {status}",
        f"  Started:     {created}",
        f"  Updated:     {updated}",
        f"  Total Nodes: {len(nodes)}",
        "  ── Counts ──",
    ]
    for state, count in summary["counts"].items():
        if count > 0:
            lines.append(f"    {state}: {count}")

    lines.append("  ── Nodes ──")
    for nid, ndata in summary["nodes"].items():
        marker = {"done": "✅", "failed": "❌", "blocked": "🚫", "todo": "⏳",
                  "running": "🔄", "retrying": "🔁", "needs_replan": "🔧"}.get(ndata["status"], "❓")
        lines.append(
            f"    {marker} {nid}: {ndata['status']} "
            f"(worker={ndata['worker']}, attempts={ndata['attempts']}, "
            f"verdict={ndata['verifier_verdict'] or 'N/A'}, score={ndata['score']})"
        )
        if ndata.get("blocked_by"):
            lines.append(f"       blocked_by: {ndata['blocked_by']}")
        if ndata.get("error"):
            error_text = str(ndata["error"])[:120]
            lines.append(f"       error: {error_text}")
        if ndata.get("artifacts"):
            lines.append(f"       artifacts: {len(ndata['artifacts'])} file(s)")

    lines.append("  ── Next Actions ──")
    for action in summary.get("next_actions", []):
        lines.append(f"    → {action}")
    lines.append("=" * 65)

    return "\n".join(lines)


# ── Pipeline 执行引擎 ─────────────────────────────────────

def run_project_pipeline(project_name: str, workers: dict,
                         auto_resume: bool = True) -> dict:
    """【v4 P1】DAG 感知的项目 Pipeline 执行引擎。"""
    state = load_project_state(project_name)
    steps = state.get("pipeline_steps", [])
    if not steps:
        return {"error": "没有 Pipeline 步骤。请先运行 project_setup。"}

    run = load_workflow_run(project_name)
    if run is None:
        run_id = f"{project_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        run = WorkflowRun(
            run_id=run_id,
            project_name=project_name,
            status="pending",
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

    if run.status == "running" and auto_resume:
        for nid, ndata in run.nodes.items():
            if isinstance(ndata, dict) and ndata.get("status") == "running":
                ndata["status"] = "todo"

    assignments = state.get("assignments", {})
    for step in steps:
        node_id = step["name"]
        if node_id not in run.nodes:
            run.nodes[node_id] = asdict(TaskNode(
                id=node_id,
                name=node_id,
                description=step.get("description", ""),
                depends_on=step.get("depends_on", []),
                assigned_worker=_resolve_worker_for_step(step, assignments, workers),
                status=step.get("status", "todo"),
                max_retries=3,
                created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ))

    try:
        _topological_sort(run.nodes)
    except ValueError as e:
        run.execution_log.append({"level": "warn", "msg": str(e)})

    run.status = "running"
    _save_workflow_run(run)

    MAX_ROUNDS = 20

    for round_num in range(MAX_ROUNDS):
        _propagate_blocks(run.nodes)

        ready_ids = _find_ready_nodes(run.nodes)

        if not ready_ids:
            pending = sum(
                1 for n in run.nodes.values()
                if n.get("status", "todo") not in ("done", "failed", "blocked", "needs_replan")
            )
            if pending == 0:
                all_done = all(n["status"] == "done" for n in run.nodes.values())
                run.status = "completed" if all_done else "failed"
            _save_workflow_run(run)
            break

        print(f"\n  [Pipeline R{round_num + 1}] 就绪节点: {len(ready_ids)} → {ready_ids}")

        with ThreadPoolExecutor(max_workers=min(len(ready_ids), 6)) as executor:
            futures = {}
            for nid in ready_ids:
                ndata = run.nodes[nid]
                ndata["status"] = "running"
                _save_workflow_run(run)

                node_task = _build_node_task(ndata, state)
                future = executor.submit(
                    delegate_with_verification,
                    workers, ndata["assigned_worker"], node_task,
                    verifier_names=["Sophia", "Nathaniel"],
                    max_retries=ndata.get("max_retries", 3),
                    project_name=project_name,
                )
                futures[future] = nid

            for future in as_completed(futures):
                nid = futures[future]
                ndata = run.nodes[nid]
                try:
                    result = future.result()
                    fs = result.get("final_status", "failed")
                    ndata["status"] = {
                        "done": "done", "success": "done",
                    }.get(fs, fs)
                    ndata["verification"] = result.get("verification")
                    ndata["attempts"] = len(result.get("attempts", []))

                    update_project_step(project_name, ndata["name"], ndata["status"],
                                        worker=ndata["assigned_worker"])
                    run.execution_log.append({
                        "node": nid, "round": round_num + 1,
                        "status": ndata["status"],
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    })
                except Exception as e:
                    ndata["status"] = "failed"
                    ndata["error"] = str(e)[:500]
                    run.execution_log.append({
                        "node": nid, "round": round_num + 1,
                        "status": "failed", "error": str(e)[:200],
                    })

                _save_workflow_run(run)

    _propagate_blocks(run.nodes)
    _save_workflow_run(run)

    report = build_workflow_run_report(run)
    print(report)
    return _summarize_run(run.nodes, run.status)


# ── 收敛循环 ──────────────────────────────────────────────

def run_convergence_loop(task: str, worker_name: str, workers: dict,
                          verifier_names: list | None = None,
                          stable_rounds: int = 2, max_rounds: int = 5,
                          budget: Budget | None = None) -> dict:
    """【v4 P1】迭代执行直到收敛。"""
    budget = budget or Budget()
    verifier_names = verifier_names or ["Sophia", "Nathaniel"]

    round_log: list[dict] = []
    stable_count = 0
    current_task = task
    final_result = None

    for r in range(max_rounds):
        result = delegate_with_verification(
            workers, worker_name, current_task,
            verifier_names=verifier_names, max_retries=2, budget=budget,
        )

        verification = result.get("verification", {}) or {}
        blocking_count = len(verification.get("blocking_issues", []))

        round_log.append({
            "round": r + 1,
            "status": result["final_status"],
            "blocking_issues": blocking_count,
        })

        if result["final_status"] in ("failed", "needs_replan"):
            stable_count = 0
            final_result = result
            break

        if blocking_count == 0:
            stable_count += 1
            final_result = result
            if stable_count >= stable_rounds:
                break
        else:
            stable_count = 0
            retry_instr = verification.get("retry_instruction", "继续改进。")
            current_task = (
                f"{task}\n\n[收敛反馈 R{r + 1}]\n{retry_instr}"
            )

    return {
        "rounds": round_log,
        "final_result": final_result,
        "stable_rounds_achieved": stable_count,
    }


# ── 恢复与重规划 ──────────────────────────────────────────

def resume_workflow_run(project_name: str, workers: dict) -> dict:
    """从保存的状态恢复中断的工作流。"""
    run = load_workflow_run(project_name)
    if run is None:
        return {"error": f"未找到项目 '{project_name}' 的工作流记录。"}
    if run.status == "completed":
        return {"status": "already_completed", "message": "该工作流已完成。"}

    for nid, ndata in run.nodes.items():
        if isinstance(ndata, dict) and ndata.get("status") == "running":
            ndata["status"] = "todo"
    _save_workflow_run(run)

    return run_project_pipeline(project_name, workers, auto_resume=False)


# ── 项目初始化 ────────────────────────────────────────────

def project_setup(workers: dict, project_description: str) -> str:
    """Manager 接到项目后，用 AI 分析需求并动态分配领域。

    由 Manager 的 AI 生成分工表，写入 project_state.json。"""
    roster = "\n".join(
        f"  - 「{w['name']}」{w['role']}：{w['description']}（工具: {', '.join(w['tool_names'])}）"
        for w in workers.values()
    )

    system_prompt = (
        "你是一个技术项目经理。现在接到一个新项目，你需要：\n"
        "1. 分析项目需求，拆解为 3-6 个可并行/串行的 Pipeline 步骤\n"
        "2. 根据每个成员的职能和工具，分配合适的临时领域和 scope\n"
        "3. 输出 JSON 格式的《项目分工表》\n\n"
        "团队成员：\n"
        f"{roster}\n\n"
        "输出格式（严格 JSON）：\n"
        "{\n"
        '  "project": "项目名称",\n'
        '  "pipeline_steps": [\n'
        '    {"name": "步骤名", "description": "...", "depends_on": [], "status": "todo"}\n'
        '  ],\n'
        '  "assignments": {\n'
        '    "Worker名": {"domain": "临时领域", "scope": "操作范围", "tasks": ["具体任务1", "具体任务2"]}\n'
        '  },\n'
        '  "review_plan": {"reviewer": "Sophia", "validator": "Nathaniel", "final_check": "Victor"}\n'
        "}\n\n"
        "规则：\n"
        "- 不要给成员永久添加领域，这只是本项目内的临时分工\n"
        "- 根据项目实际需求匹配成员，不需要每个成员都参与\n"
        "- Pipeline 步骤要具体，有明确的输出\n"
        "- 确保审核链：Sophia(质量审核) → Nathaniel(数据验证) → Victor(最终复核)"
    )

    final_result = ""
    retry_context = ""

    for attempt in range(3):
        prompt = f"[新项目]\n{project_description}{retry_context}"
        final_result = call_llm_once(
            prompt=prompt,
            system_prompt=system_prompt,
            tier="complex",
            max_tokens=4096,
        )

        try:
            text = final_result
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]
            plan = json.loads(text.strip())

            project_name = plan.get("project", "unnamed")
            state = load_project_state(project_name)
            state["pipeline_steps"] = plan.get("pipeline_steps", [])
            state["assignments"] = plan.get("assignments", {})
            state["review_plan"] = plan.get("review_plan", {})
            state["project_description"] = project_description
            save_project_state(project_name, state)

            lines = [
                f"\n{'=' * 55}",
                f"  项目分工表: {project_name}",
                f"{'=' * 55}",
            ]
            lines.append("\n[Pipeline 步骤]")
            for step in state["pipeline_steps"]:
                deps = f" (依赖: {', '.join(step.get('depends_on', []))})" if step.get("depends_on") else ""
                lines.append(f"  [ ] {step['name']}: {step.get('description', '')[:60]}{deps}")

            lines.append("\n[成员分工]")
            for name, assign in state["assignments"].items():
                lines.append(f"  「{name}」→ {assign.get('domain', '')}")
                lines.append(f"      scope: {assign.get('scope', '')}")
                tasks = assign.get("tasks", [])
                for t in tasks:
                    lines.append(f"        - {t}")

            lines.append(f"\n[审核链] {state.get('review_plan', {})}")
            lines.append(f"\n状态文件: {PROJECT_STATE_DIR}/{project_name}_state.json")
            lines.append(f"{'=' * 55}")

            return "\n".join(lines)

        except (json.JSONDecodeError, KeyError) as e:
            retry_context = f"\n\n[上一轮 JSON 格式不正确: {e}] 请严格按 JSON 格式重新输出。"
            continue

    return f"项目规划失败：AI 未能输出有效 JSON。原始回复:\n{final_result[:500]}"


def request_replan(project_name: str, failed_node_id: str, workers: dict) -> dict:
    """【v4 P2】当节点 needs_replan 时，请求 LLM 重新规划 pipeline。"""
    state = load_project_state(project_name)
    run = load_workflow_run(project_name)

    failed_node = {}
    if run and failed_node_id in run.nodes:
        failed_node = run.nodes[failed_node_id]

    prompt = (
        f"[需要重新规划]\n"
        f"项目: {project_name}\n"
        f"失败节点: {failed_node_id}\n"
        f"错误: {failed_node.get('error', 'unknown')}\n"
        f"当前 pipeline 状态: {json.dumps({nid: n.get('status') for nid, n in (run.nodes.items() if run else {})}, ensure_ascii=False)}\n\n"
        f"请重新分析并输出新的 pipeline JSON。已完成(done)的节点保持不变。"
    )

    return {
        "replan_request": project_setup(workers, prompt),
        "failed_node": failed_node_id,
    }


def request_human_approval(node_id: str, proposal: str) -> dict:
    """【v4 P2】人工审批门。"""
    with print_lock:
        print(f"\n  {'!' * 3} 需要人工审批: {node_id}")
        print(f"  提案: {proposal}")
        print(f"  请输入 approve / reject / replan: ", end="")
    choice = input().strip().lower()
    return {"node_id": node_id, "decision": choice}
