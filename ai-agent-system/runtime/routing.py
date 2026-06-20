"""模型路由 —— Worker/Manager 自动选择厂商和模型。

根据任务关键词、历史失败次数、Verifier 角色等信号，
从 MODEL_TIERS 选择合适层级，支持 provider fallback。
"""
from runtime.config import (
    MODEL_TIERS,
    PROVIDERS,
    UPGRADE_TARGET,
    FALLBACK_COMPLEX,
    FALLBACK_MAJOR,
)

# ── 复杂度判断关键词 ─────────────────────────────────────
COMPLEXITY_SIGNALS_COMPLEX = [
    "架构", "设计", "重构", "优化", "协调", "多模块", "跨模块", "冲突",
    "architecture", "design", "refactor", "optimize", "coordinate",
    "cross-module", "conflict", "安全审计", "性能分析",
]
COMPLEXITY_SIGNALS_MAJOR = [
    "重大", "高风险", "数据模型变更", "pipeline 重构", "破坏性",
    "critical", "breaking", "schema change", "pipeline redesign",
    "安全漏洞", "资金", "合规", "用户数据",
]

CODE_EDIT_SIGNALS = [
    "write_file", "replace_text", "apply_patch", "bug fix", "fix",
    "code edit", "refactor", "implementation",
    "修改", "修复", "重构", "实现", "写代码", "编辑", "删除", "取消注释",
]


def _resolve_route(tier_key: str) -> tuple:
    """解析路由，只返回已配置 provider；否则回退到 DeepSeek。"""
    route = MODEL_TIERS.get(tier_key, MODEL_TIERS["normal"])
    if route[0] in PROVIDERS:
        return route

    fallback = FALLBACK_MAJOR if tier_key == "major" else FALLBACK_COMPLEX
    if fallback[0] in PROVIDERS:
        return fallback

    return ("deepseek", "deepseek-v4-pro[1M]")


def _is_code_edit_task(task: str) -> bool:
    """v4.2: 检测任务是否涉及代码修改。"""
    task_lower = task.lower()
    for kw in CODE_EDIT_SIGNALS:
        if kw.lower() in task_lower:
            return True
    return False


def select_worker_model(task: str, worker_name: str,
                         previous_failures: int = 0,
                         is_verifier: bool = False) -> tuple:
    """根据任务复杂度自主选择合适的模型。

    返回 ((provider, model_id), complexity_label, reason)。

    判断逻辑（优先级从高到低）：
    1. Verifier 最低 deepseek-v4-pro
    2. 前次模型两次尝试失败 → 自动升级
    3. 关键词匹配 → complex / major（simple 已禁用 flash，统一走 normal）
    4. 默认 → normal (deepseek-v4-pro[1M])
    """
    task_lower = task.lower()

    # v4.2: Verifier 最低 deepseek-v4-pro
    if is_verifier:
        route = _resolve_route("normal")
        return (route, "verifier",
                f"Verifier {worker_name} 最低使用 [{route[0]}]{route[1]}")

    # 连续失败 → 自动升级到 GPT
    if previous_failures >= 2:
        if UPGRADE_TARGET[0] in PROVIDERS:
            route = UPGRADE_TARGET
        else:
            route = FALLBACK_COMPLEX
        return (route, "complex",
                f"{worker_name} 前次模型连续 {previous_failures} 次失败，自动升级到 [{route[0]}]{route[1]}")

    # 重大信号
    for kw in COMPLEXITY_SIGNALS_MAJOR:
        if kw.lower() in task_lower:
            route = _resolve_route("major")
            return (route, "major",
                    f"任务涉及重大决策（匹配关键词: {kw}），启用 [major] {route[1]}")

    # 复杂信号
    complex_count = sum(1 for kw in COMPLEXITY_SIGNALS_COMPLEX if kw.lower() in task_lower)
    if complex_count >= 1:
        route = _resolve_route("complex")
        return (route, "complex",
                f"任务复杂度高（匹配 {complex_count} 个复杂信号），启用 [complex] {route[1]}")

    # v4.2: flash 已禁用，所有任务基线 deepseek-v4-pro
    route = _resolve_route("normal")
    code_edit = _is_code_edit_task(task)
    if code_edit:
        return (route, "code_edit",
                f"代码修改任务，使用 [{route[0]}]{route[1]}（flash 已禁用）")
    return (route, "normal", f"常规任务，使用 [{route[0]}]{route[1]}（flash 已禁用）")


def select_manager_model(user_input: str, context_complexity: str = "") -> tuple:
    """Manager 模型路由：常态 DeepSeek，复杂 → 升级，重大 → 最强 + 标记。

    返回 (model, should_confirm_with_user)。
    """
    task_lower = user_input.lower()

    # 重大决策信号 → GPT-5.5 + 需要用户确认
    for kw in COMPLEXITY_SIGNALS_MAJOR:
        if kw.lower() in task_lower:
            route = _resolve_route("major")
            return (route, True)

    # 复杂调度信号 → GPT-5.4
    for kw in COMPLEXITY_SIGNALS_COMPLEX:
        if kw.lower() in task_lower:
            route = _resolve_route("complex")
            return (route, False)

    # 上下文传递的复杂度
    if context_complexity == "complex":
        return (_resolve_route("complex"), False)
    if context_complexity == "major":
        return (_resolve_route("major"), True)

    # 默认 → DeepSeek 1M
    return (_resolve_route("normal"), False)
