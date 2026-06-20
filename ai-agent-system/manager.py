"""
Multi-Agent 层级管理系统 v3
老板（用户）→ 管理者（Manager Agent）→ 员工（Worker Agent）
- 并行 Worker 执行
- Worker 多轮对话记忆
- 联网搜索能力
- 多厂商模型自适应（DeepSeek / 千问 / MiniMax / GPT）

员工角色和权限由 workers.json 配置
"""
import json
import os
import sys
import io
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# Windows UTF-8 控制台修复（Pitfall #4: GBK 编码无法输出 emoji）
# 仅在真实终端下启用，跳过 pytest/重定向等场景
if sys.platform == "win32" and "pytest" not in sys.modules:
    try:
        os.environ.setdefault("PYTHONUTF8", "1")
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        elif hasattr(sys.stdout, "buffer") and (sys.stdout.encoding or "").lower() != "utf-8":
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        elif hasattr(sys.stderr, "buffer") and (sys.stderr.encoding or "").lower() != "utf-8":
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except (AttributeError, OSError, ValueError):
        pass

# ── 配置已迁移到 runtime.config ──
from runtime.config import (  # noqa: F401 — 向后兼容 re-export
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    MINIMAX_API_KEY,
    MINIMAX_BASE_URL,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    PROVIDERS,
    MODEL_TIERS,
    UPGRADE_TARGET,
    FALLBACK_COMPLEX,
    FALLBACK_MAJOR,
    MANAGER_DEFAULT_MODEL,
    MANAGER_COMPLEX_MODEL,
    MANAGER_MAJOR_MODEL,
    DEFAULT_MODEL,
    DEFAULT_API_KEY,
    DEFAULT_BASE_URL,
    APP_VERSION,
    APP_RUNTIME_NAME,
    _init_providers,
)

# 初始化默认 provider（DeepSeek 总是可用）
_init_providers()
default_client = PROVIDERS["deepseek"]["client"]

# ═══════════════════════════════════════════════════════════════
# v4 Data Contracts — 已迁移到 runtime.contracts
# ═══════════════════════════════════════════════════════════════
from runtime.contracts import (  # noqa: F401 — 向后兼容 re-export
    TaskNodeStatus,
    VALID_TRANSITIONS,
    WorkerResult,
    VerificationResult,
    TaskNode,
    Budget,
    WorkflowRun,
)


# ── v4 纯函数 ── 已迁移到 runtime.pure_functions
from runtime.pure_functions import (  # noqa: F401 — 向后兼容 re-export
    _transition_node,
    _extract_json_from_text,
    _normalize_worker_result,
    _normalize_verification_result,
    _merge_verdicts,
    _check_budget,
    _discover_artifacts,
)


# ═══════════════════════════════════════════════════════════════
# v4.1 消息净化层 — 防止 thinking block 等多轮污染
# ═══════════════════════════════════════════════════════════════

from runtime.sanitize import sanitize_messages_for_provider  # noqa: F401 — 向后兼容 re-export


# ── 持久化层已迁移到 runtime.persistence ──
from runtime.persistence import (  # noqa: F401 — 向后兼容 re-export
    _workers_config,
    _deputy_config,
    worker_sessions,
    SESSION_FILE,
    SCORE_FILE,
    TASK_BOARD_FILE,
    KNOWLEDGE_FILE,
    PROJECT_STATE_DIR,
    save_sessions,
    load_sessions,
    _load_json,
    _save_json,
    create_task,
    list_tasks,
    update_task,
    record_knowledge,
    search_knowledge,
    get_system_metadata,
    ensure_project_state_dir,
    load_project_state,
    save_project_state,
    log_model_usage,
    update_project_step,
    save_workflow_run,
    _save_workflow_run,
    load_workflow_run,
)


# ═══════════════════════════════════════════════════════════════
# v4.1 Session 生命周期策略
# ═══════════════════════════════════════════════════════════════


def should_use_fresh_session(task: str, mode: str = "") -> bool:
    """判断是否应使用全新 session（不复用历史记忆）。

    默认策略：
    - delegate_with_verification: 始终 fresh（每次 attempt 独立）
    - run_project_pipeline: 始终 fresh（每个 TaskNode 独立）
    - run_convergence_loop: 保留 loop 内上下文但每轮 sanitize
    - delegate_task / 聊天: 保留记忆（向后兼容）
    """
    if mode in ("verified", "pipeline", "fresh"):
        return True
    if mode == "convergence":
        return False  # keep context within loop, sanitize per round
    # 普通 delegate_task 保留记忆
    return False


# ═══════════════════════════════════════════════════════════════
# v4.1 统一模型调用出口 — 已迁移到 runtime.llm
# ═══════════════════════════════════════════════════════════════
from runtime.llm import (  # noqa: F401 — 向后兼容 re-export
    call_llm_once,
    anthropic_tools_to_openai,
    anthropic_messages_to_openai,
    openai_response_to_anthropic_blocks,
    call_llm,
    call_llm_multi_turn,
)


# (LLM functions imported above — old inline definitions removed)

# ── 模型路由已迁移到 runtime.routing ──
from runtime.routing import (  # noqa: F401 — 向后兼容 re-export
    COMPLEXITY_SIGNALS_COMPLEX,
    COMPLEXITY_SIGNALS_MAJOR,
    CODE_EDIT_SIGNALS,
    _resolve_route,
    _is_code_edit_task,
    select_worker_model,
    select_manager_model,
)

# ── 工具层已迁移到 runtime.tools ──
from runtime.tools import (  # noqa: F401 — 向后兼容 re-export
    print_lock,
    truncate,
    _safe_walk_files,
    _format_file_results,
    _normalize_command_for_platform,
    get_dashboard,
    track_api_call,
    track_failure,
    ALL_TOOLS,
    execute_tool,
    set_coworker_executor,
)


# ── 项目启动已迁移到 runtime.pipeline ──
from runtime.pipeline import project_setup  # noqa: F401 — 向后兼容 re-export


# ── Worker 执行层已迁移到 runtime.workers ──
from runtime.workers import (  # noqa: F401 — 向后兼容 re-export
    load_workers,
    run_worker,
    run_deputy,
)

# 注入 ask_coworker 回调（避免 runtime.tools ↔ runtime.workers 循环 import）
set_coworker_executor(run_worker)


# ── 验证闭环已迁移到 runtime.verification ──
from runtime.verification import (  # noqa: F401 — 向后兼容 re-export
    _run_verifier,
    delegate_with_verification,
)

# ── DAG Pipeline 引擎已迁移到 runtime.pipeline ──
from runtime.pipeline import (  # noqa: F401 — 向后兼容 re-export
    _topological_sort,
    _find_ready_nodes,
    _propagate_blocks,
    _resolve_worker_for_step,
    _build_node_task,
    _summarize_run,
    build_workflow_run_report,
    run_project_pipeline,
    run_convergence_loop,
    resume_workflow_run,
    request_replan,
    request_human_approval,
)


# ── Manager 工具定义已迁移到 runtime.manager_tools ──
from runtime.manager_tools import build_manager_tools  # noqa: F401 — 向后兼容 re-export


def delegate_task(workers: dict, worker_name: str, task: str) -> str:
    """管理者调用此函数来指派 Worker 执行任务。"""
    if worker_name not in workers:
        available = ", ".join(workers.keys())
        return f"错误：没有名为「{worker_name}」的员工。可选员工: {available}"

    cfg = workers[worker_name]
    with print_lock:
        print(f"\n  >>> 指派给 Worker-{worker_name}（{cfg['role']}）: {task[:80]}...")
        print("  " + "-" * 40)

    result = run_worker(cfg, task, fresh_session=True, session_scope="delegate")

    with print_lock:
        print("  " + "-" * 40)
        print(f"  <<< Worker-{worker_name} 完成任务\n")

    return f"Worker-{worker_name}（{cfg['role']}）执行结果:\n{result['result']}"


def clear_worker_memory(workers: dict, worker_name: str) -> str:
    """清除指定 Worker 的对话记忆。"""
    if worker_name not in workers:
        return f"错误：没有名为「{worker_name}」的员工。"

    cfg = workers[worker_name]
    session_key = f"{worker_name}|{cfg['role']}"
    if session_key in worker_sessions:
        del worker_sessions[session_key]
        return f"已清除 Worker-{worker_name} 的对话记忆。"
    return f"Worker-{worker_name} 当前没有对话记忆。"


def relay_to_worker(workers: dict, worker_name: str, message: str) -> str:
    """将信息注入到指定 Worker 的对话上下文（用于 Worker 间间接通信）。"""
    if worker_name not in workers:
        return f"错误：没有名为「{worker_name}」的员工。"

    cfg = workers[worker_name]
    session_key = f"{worker_name}|{cfg['role']}"
    if session_key not in worker_sessions:
        worker_sessions[session_key] = []

    worker_sessions[session_key].append({
        "role": "user",
        "content": f"[来自 Manager 的上下文信息]\n{message}",
    })
    save_sessions()
    return f"已向 Worker-{worker_name} 传递上下文信息。"


def evaluate_result(workers: dict, worker_name: str,
                    correctness: int, completeness: int, quality: int,
                    comment: str = "", verdict: str = "") -> str:
    """对 Worker 交付结果进行结构化评分并持久化。

    v4 新增可选参数 verdict（"pass"|"needs_retry"|"reject"），向后兼容。
    """
    if worker_name not in workers:
        return f"错误：没有名为「{worker_name}」的员工。"

    score = {
        "worker_name": worker_name,
        "correctness": correctness,
        "completeness": completeness,
        "quality": quality,
        "total": correctness + completeness + quality,
        "comment": comment,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if verdict:
        score["verdict"] = verdict

    # 加载历史评分
    all_scores = []
    if os.path.exists(SCORE_FILE):
        try:
            with open(SCORE_FILE, "r", encoding="utf-8") as f:
                all_scores = json.load(f)
        except Exception:
            pass

    all_scores.append(score)

    with open(SCORE_FILE, "w", encoding="utf-8") as f:
        json.dump(all_scores, f, ensure_ascii=False, indent=2)

    avg = score["total"] / 3
    return (
        f"评分已保存。Worker-{worker_name} 本次评分：\n"
        f"  正确性: {correctness}/5 | 完整性: {completeness}/5 | 质量: {quality}/5\n"
        f"  综合: {avg:.1f}/5"
        + (f"\n  评语: {comment}" if comment else "")
    )


def roundtable_discuss(workers: dict, topic: str, participants: list) -> str:
    """发起圆桌讨论：多 Worker 并行发言 + 交叉回应 + 汇总共识。"""
    if len(participants) < 2:
        return "圆桌讨论至少需要 2 名参与者。"

    with print_lock:
        print(f"\n  🏛️ 圆桌讨论: {topic}")
        print(f"  参与者: {', '.join(participants)}")
        print("  " + "=" * 50)

    # 第一轮：并行提问
    round1_results = {}
    round1_tasks = {}
    with ThreadPoolExecutor(max_workers=len(participants)) as executor:
        for name in participants:
            cfg = workers[name]
            prompt = (
                f"[圆桌讨论 - 第 1 轮]\n话题: {topic}\n"
                "请发表你的专业意见，从你的职能角度分析问题，给出具体建议。"
            )
            future = executor.submit(run_worker, cfg, prompt, use_memory=True)
            round1_tasks[future] = name

        for future in as_completed(round1_tasks):
            name = round1_tasks[future]
            try:
                result = future.result()
                round1_results[name] = result["result"]
            except Exception as e:
                round1_results[name] = f"发言失败: {e}"

    with print_lock:
        print("\n  --- 第 1 轮发言汇总 ---")
        for name, opinion in round1_results.items():
            print(f"  [{name}]: {opinion[:200]}...")
        print("  " + "-" * 40)

    # 第二轮：交叉回应
    round2_results = {}
    round2_tasks = {}
    others_summary = "\n".join(f"[{n}]: {o[:300]}" for n, o in round1_results.items())

    with ThreadPoolExecutor(max_workers=len(participants)) as executor:
        for name in participants:
            cfg = workers[name]
            prompt = (
                f"[圆桌讨论 - 第 2 轮]\n话题: {topic}\n\n"
                f"以下是所有参与者的第一轮意见:\n{others_summary}\n\n"
                "请阅读其他人的意见后，补充或修正你的观点。如果同意别人的意见请明确表示，"
                "如果有不同看法请具体说明理由。"
            )
            future = executor.submit(run_worker, cfg, prompt, use_memory=True)
            round2_tasks[future] = name

        for future in as_completed(round2_tasks):
            name = round2_tasks[future]
            try:
                result = future.result()
                round2_results[name] = result["result"]
            except Exception as e:
                round2_results[name] = f"发言失败: {e}"

    with print_lock:
        print("\n  --- 第 2 轮补充意见 ---")
        for name, opinion in round2_results.items():
            print(f"  [{name}]: {opinion[:200]}...")
        print("  " + "=" * 50)

    # 汇总
    summary_parts = []
    for name in participants:
        summary_parts.append(f"### {name}\n第1轮: {round1_results.get(name, '')[:300]}\n第2轮: {round2_results.get(name, '')[:300]}")
    return "圆桌讨论完成。\n" + "\n\n".join(summary_parts)


def consult_deputy(question: str) -> str:
    """向副经理咨询意见。"""
    if not _deputy_config:
        return "错误：未配置副经理。请在 workers.json 中添加 is_deputy: true 的员工。"

    # 需要延迟引用 MANAGER_TOOLS
    manager_tools = build_manager_tools(_workers_config)

    with print_lock:
        print(f"\n  🎩 咨询副经理「{_deputy_config['name']}」...")
        print("  " + "-" * 40)

    result = run_deputy(question, manager_tools, execute_manager_tool_fn=execute_manager_tool)

    with print_lock:
        print("  " + "-" * 40)
        print(f"  🎩 副经理已回复\n")

    return f"副经理「{_deputy_config['name']}」的意见:\n{result['result']}"


def request_decision_review(decision_summary: str) -> str:
    """请求副经理复核决策。"""
    if not _deputy_config:
        return "错误：未配置副经理。"

    prompt = (
        f"[决策复核请求]\n"
        f"正经理做了以下决策，请你复核:\n{decision_summary}\n\n"
        "请逐一检查：\n"
        "1. 指派给的人选是否合适（职能匹配吗？权限够吗？）\n"
        "2. 优先级是否合理\n"
        "3. 是否遗漏了关键步骤（比如没先审查就写代码）\n"
        "4. 评分是否公允（如果涉及评分）\n\n"
        "如果发现任何问题，明确指出来并提出改进建议。如果都合理，也要明确表示同意。"
    )
    return consult_deputy(prompt)


def execute_manager_tool(name: str, args: dict, workers: dict) -> str:
    if name == "delegate_task":
        return delegate_task(workers, args["worker_name"], args["task"])
    if name == "clear_worker_memory":
        return clear_worker_memory(workers, args["worker_name"])
    if name == "relay_to_worker":
        return relay_to_worker(workers, args["worker_name"], args["message"])
    if name == "evaluate_result":
        return evaluate_result(
            workers, args["worker_name"],
            args["correctness"], args["completeness"], args["quality"],
            args.get("comment", ""),
        )
    if name == "create_task":
        return create_task(
            args["description"],
            args.get("priority", "medium"),
            args.get("assigned_worker", ""),
        )
    if name == "list_tasks":
        return list_tasks(args.get("status_filter", ""))
    if name == "update_task":
        return update_task(
            args["task_id"],
            args.get("status", ""),
            args.get("assigned_worker", ""),
        )
    if name == "record_knowledge":
        return record_knowledge(
            args["topic"], args["content"],
            args.get("author", ""),
        )
    if name == "roundtable_discuss":
        return roundtable_discuss(workers, args["topic"], args["participants"])
    if name == "get_dashboard":
        return get_dashboard()
    if name == "get_system_metadata":
        return get_system_metadata()
    if name == "consult_deputy":
        return consult_deputy(args["question"])
    if name == "request_decision_review":
        return request_decision_review(args["decision_summary"])
    if name == "project_setup":
        return project_setup(workers, args["project_description"])
    if name == "update_project_step":
        return update_project_step(
            args["project_name"], args["step_name"], args["status"],
            args.get("output_path", ""), args.get("worker", ""),
        ) or f"项目步骤已更新: {args['project_name']}/{args['step_name']} → {args['status']}"

    # ── v4 新工具 ──
    if name == "delegate_with_verification":
        result = delegate_with_verification(
            workers, args["worker_name"], args["task"],
            verifier_names=args.get("verifier_names"),
            max_retries=args.get("max_retries", 3),
        )
        return json.dumps(result, ensure_ascii=False, indent=2)

    if name == "run_project_pipeline":
        result = run_project_pipeline(args["project_name"], workers)
        return json.dumps(result, ensure_ascii=False, indent=2)

    if name == "run_convergence_loop":
        result = run_convergence_loop(
            args["task"], args["worker_name"], workers,
            stable_rounds=args.get("stable_rounds", 2),
            max_rounds=args.get("max_rounds", 5),
        )
        return json.dumps(result, ensure_ascii=False, indent=2)

    if name == "show_workflow_status":
        run = load_workflow_run(args["project_name"])
        if run is None:
            return f"未找到项目 '{args['project_name']}' 的工作流记录。"
        return json.dumps(_summarize_run(run.nodes, run.status), ensure_ascii=False, indent=2)

    if name == "request_replan":
        return json.dumps(
            request_replan(args["project_name"], args["failed_node_id"], workers),
            ensure_ascii=False, indent=2,
        )

    return f"未知工具: {name}"


# ── Manager 对话循环 ───────────────────────────────────────
def main():
    workers = load_workers()
    if not workers:
        print("无法加载员工配置，系统退出。请在 workers.json 中配置员工。")
        return

    load_sessions()
    if worker_sessions:
        names = {k.split("|")[0] for k in worker_sessions}
        print(f"  [已恢复 {len(worker_sessions)} 个 Worker 会话: {', '.join(names)}]")

    MANAGER_TOOLS = build_manager_tools(workers)

    roster = "\n".join(
        f"  - 「{w['name']}」{w['role']}：{w['description']}（工具: {', '.join(w['tool_names'])}）"
        for w in workers.values()
    )

    print("=" * 60)
    print(f"  Multi-Agent 层级管理系统 {APP_VERSION} — {APP_RUNTIME_NAME}")
    print("  老板（你）→ 管理者（AI,自适应模型）→ 员工（AI Worker,自适应模型）")
    print("=" * 60)
    print(f"\n  [员工花名册]")
    print(roster)
    if _deputy_config:
        print(f"  [管理层] 正经理 + 副经理「{_deputy_config['name']}」（{_deputy_config['role']}）")
    print(f"\n  [v4 特性] DAG Pipeline 引擎 | 验证闭环 | 收敛模式 | 结构化合约 | Workspace 隔离接口")
    print(f"  [v3 特性] 项目动态分工 | 多厂商模型路由 | 任务看板 | 圆桌讨论 | 知识库 | 副经理把关")
    print(f"  [厂商] DeepSeek(主力) | GPT-5.5(重大决策) | 千问/MiniMax(备用)")
    print(f"  [策略] 优先 DeepSeek 1M，搞不定自动升级 GPT-5.4，重大决策直接用 GPT-5.5")
    print(f"  输入 /quit 退出")
    print("=" * 60)

    messages = []
    manager_system = (
        "你是一个技术管理者（Manager）。老板会给你发布任务，你需要：\n"
        "1. 分析任务，判断需要指派给哪位员工\n"
        "2. 根据员工的职能和权限选择合适的员工\n"
        "3. 使用 delegate_task 或 delegate_with_verification 指派任务给员工执行\n"
        "4. 可以同时指派多名员工——他们会并行工作，互不干扰\n"
        "5. 审核员工的执行结果，确保质量\n"
        "6. 汇总结果，向老板做最终汇报\n\n"
        "你的团队成员：\n"
        f"{roster}\n\n"
        "规则：\n"
        "- 【v4】对关键任务使用 delegate_with_verification 而非 delegate_task，这样会自动验证和重试\n"
        "- 【v4】所有项目 pipeline 通过 run_project_pipeline 执行，不要在 project_setup 后逐个 delegate_task\n"
        "- 【v4】发现结果不稳定时，用 run_convergence_loop 迭代到收敛\n"
        "- 【v4】节点状态可以通过 show_workflow_status 查看，中断后可以恢复\n"
        "- 【v4】节点 needs_replan 时用 request_replan 重新规划\n"
        "- 【项目模式】接到新项目/大任务时，先用 project_setup 分析需求并动态分配领域，再进行具体工作\n"
        "- 系统事实查询（版本号、日志/会话保存位置、运行目录、已配置厂商）优先调用 get_system_metadata，不要指派 Worker 猜测\n"
        "- 如果工具没有找到证据，必须明确说未找到/无法确认；禁止把启动横幅、系统提示或常识包装成确定答案\n"
        "- 收到任务后先用 create_task 在看板中记录，做完后用 update_task 更新状态\n"
        "- 需要实际操作时，必须指派给有相应权限的员工\n"
        "- 同一轮回复中返回多个 delegate_task 可以让员工并行工作\n"
        "- 每个员工交付结果后，用 evaluate_result 进行三维评分\n"
        "- 员工会自动根据任务复杂度选择最佳厂商+模型（基线 deepseek-v4-pro，重大决策→GPT-5.5）\n"
        "- 遇到需要多方意见的复杂问题时，用 roundtable_discuss 发起圆桌讨论\n"
        "- 重要的经验教训用 record_knowledge 记录到共享知识库\n"
        "- 定期用 get_dashboard 查看团队状态\n"
        "- 遇到重大决策（高风险任务、跨部门协作、对员工评分无把握时）用 consult_deputy 咨询副经理\n"
        "- 做完重要决策后用 request_decision_review 让副经理复核\n"
        "- 最终汇报要清晰、完整"
    )

    while True:
        try:
            user_input = input("\n[老板] ").strip()
        except (EOFError, KeyboardInterrupt):
            save_sessions()
            print("\n系统关闭。（会话已保存）")
            break

        if user_input.lower() in ("/quit", "/exit", "/q"):
            save_sessions()
            print("系统关闭。（会话已保存）")
            break
        if not user_input:
            continue

        print(f"\n{'=' * 60}")
        print(f" 老板任务: {user_input}")
        print(f"{'=' * 60}")

        messages.append({"role": "user", "content": user_input})

        # ── Manager 模型路由（多厂商） ──
        (manager_provider, manager_model_id), needs_confirm = select_manager_model(user_input)
        if (manager_provider, manager_model_id) != MANAGER_DEFAULT_MODEL:
            print(f"\n  [*] Manager 模型路由: [{manager_provider}] {manager_model_id}")
        if needs_confirm:
            print("  [!]  检测到重大决策信号。")

        for _manager_turn in range(5):
            blocks = call_llm(
                provider_key=manager_provider,
                model_id=manager_model_id,
                messages=messages,
                system_prompt=manager_system,
                tools=MANAGER_TOOLS,
                disable_thinking=False,  # v4.2: Manager 策略思考不禁用 thinking
            )

            assistant_content = []
            tool_use_blocks = []

            # 收集 blocks（plain dict，来自 call_llm）
            for block in blocks:
                if block["type"] == "text":
                    print(f"\n[Manager] {block['text']}")
                    assistant_content.append(block)

                elif block["type"] == "thinking":
                    assistant_content.append(block)

                elif block["type"] == "tool_use":
                    tool_use_blocks.append(block)

            if not tool_use_blocks:
                if assistant_content:
                    messages.append({"role": "assistant", "content": assistant_content})
                break

            # 有工具调用：将所有 tool_use 加入 assistant 消息
            for block in tool_use_blocks:
                assistant_content.append(block)

            if assistant_content:
                    messages.append({"role": "assistant", "content": assistant_content})
            assistant_content = []

            # 并行执行所有工具调用
            if len(tool_use_blocks) == 1:
                block = tool_use_blocks[0]
                result = execute_manager_tool(block["name"], block["input"], workers)
                tool_results = [(block, result)]
            else:
                with print_lock:
                    print(f"\n  [||] 并行执行 {len(tool_use_blocks)} 个任务...\n")

                def run_tool(block):
                    return execute_manager_tool(block["name"], block["input"], workers)

                tool_results = []
                with ThreadPoolExecutor(max_workers=len(tool_use_blocks)) as executor:
                    future_to_block = {
                        executor.submit(run_tool, block): block
                        for block in tool_use_blocks
                    }
                    for future in as_completed(future_to_block):
                        block = future_to_block[future]
                        try:
                            result = future.result()
                        except Exception as e:
                            result = f"任务执行异常: {e}"
                        tool_results.append((block, result))

            # 发送所有工具结果
            tool_result_content = []
            for block, result in tool_results:
                tool_result_content.append({
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": result,
                })

            messages.append({
                "role": "user",
                "content": tool_result_content,
            })

            # 获取后续回复（多厂商）
            follow_up_blocks = call_llm(
                provider_key=manager_provider,
                model_id=manager_model_id,
                messages=messages,
                system_prompt=manager_system,
                tools=MANAGER_TOOLS,
                disable_thinking=False,  # v4.2: Manager 策略思考不禁用 thinking
            )
            for fb in follow_up_blocks:
                if fb["type"] == "text":
                    print(f"\n[Manager] {fb['text']}")
                    assistant_content.append(fb)
                elif fb["type"] == "thinking":
                    assistant_content.append(fb)

            if assistant_content:
                    messages.append({"role": "assistant", "content": assistant_content})

            # 一次老板任务默认只执行一批 Manager 工具；后续追问由老板显式发起。
            # 这能避免 Manager 在已经汇报后继续自行追加无关工具调用。
            break

        print(f"\n{'=' * 60}")
        print(" 任务处理完毕，等待下一个任务")
        print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
