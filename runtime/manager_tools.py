"""Manager 工具定义 — build_manager_tools。

生成 Manager 可用的所有工具 schema（Anthropic 格式）。
纯数据函数：不调用任何服务，不依赖 manager.py 其他函数。
"""
from runtime.tools import ALL_TOOLS
from runtime.config import DEFAULT_MODEL


def build_manager_tools(workers: dict) -> list:
    """构建 delegate_task + clear_memory 等全部 Manager 工具定义。"""
    worker_names = list(workers.keys())
    worker_descriptions = [
        f"「{w['name']}」- {w['role']}：{w['description']}（工具: {', '.join(w['tool_names'])}）"
        for w in workers.values()
    ]

    return [
        {
            "name": "delegate_task",
            "description": (
                "将任务指派给一名员工独立执行。你可以同时指派多名员工并行工作。\n"
                "员工列表：\n"
                + "\n".join(worker_descriptions)
                + "\n\n根据任务需求选择合适的员工。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "worker_name": {
                        "type": "string",
                        "enum": worker_names,
                        "description": "员工名称",
                    },
                    "task": {
                        "type": "string",
                        "description": "任务描述，越具体越好。",
                    },
                },
                "required": ["worker_name", "task"],
            },
        },
        {
            "name": "clear_worker_memory",
            "description": "清除指定员工的对话记忆。当员工开始全新任务、或者上下文混乱时使用。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "worker_name": {
                        "type": "string",
                        "enum": worker_names,
                        "description": "要清除记忆的员工名称",
                    },
                },
                "required": ["worker_name"],
            },
        },
        {
            "name": "relay_to_worker",
            "description": (
                "将一段信息传递给指定员工，注入到该员工的对话上下文中。"
                "用于员工之间的间接协作——比如把亚历克斯的输出告诉索菲亚让她审查时更有上下文。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "worker_name": {
                        "type": "string",
                        "enum": worker_names,
                        "description": "接收信息的员工名称",
                    },
                    "message": {
                        "type": "string",
                        "description": "要传递的信息，会被注入到该员工的对话上下文中",
                    },
                },
                "required": ["worker_name", "message"],
            },
        },
        {
            "name": "evaluate_result",
            "description": (
                "对员工的交付结果进行结构化评分。用于质量管控和事后复盘。"
                "评分后结果会保存到磁盘。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "worker_name": {
                        "type": "string",
                        "enum": worker_names,
                        "description": "被评分的员工名称",
                    },
                    "correctness": {
                        "type": "integer",
                        "description": "正确性评分 1-5（结果是否正确、无 bug）",
                    },
                    "completeness": {
                        "type": "integer",
                        "description": "完整性评分 1-5（是否覆盖所有需求）",
                    },
                    "quality": {
                        "type": "integer",
                        "description": "代码/文档质量评分 1-5（可读性、规范性、设计）",
                    },
                    "comment": {
                        "type": "string",
                        "description": "简短评语",
                    },
                },
                "required": ["worker_name", "correctness", "completeness", "quality"],
            },
        },
        {
            "name": "create_task",
            "description": "在任务看板中创建一个任务项，用于追踪工作进度。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "任务描述"},
                    "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"], "description": "优先级"},
                    "assigned_worker": {"type": "string", "enum": worker_names, "description": "指派给哪位员工（可选）"},
                },
                "required": ["description"],
            },
        },
        {
            "name": "list_tasks",
            "description": "查看任务看板，了解所有任务的进度状态。可选按状态过滤。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "status_filter": {"type": "string", "enum": ["", "todo", "in_progress", "done", "failed"], "description": "按状态过滤，留空则展示全部"},
                },
            },
        },
        {
            "name": "update_task",
            "description": "更新任务状态或指派人。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "任务 ID"},
                    "status": {"type": "string", "enum": ["todo", "in_progress", "done", "failed"], "description": "新状态"},
                    "assigned_worker": {"type": "string", "enum": worker_names, "description": "新指派人（可选）"},
                },
                "required": ["task_id"],
            },
        },
        {
            "name": "record_knowledge",
            "description": "将经验、决策或最佳实践记录到团队共享知识库。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "知识主题"},
                    "content": {"type": "string", "description": "知识内容"},
                    "author": {"type": "string", "description": "贡献者（可选）"},
                },
                "required": ["topic", "content"],
            },
        },
        {
            "name": "roundtable_discuss",
            "description": (
                "发起一次圆桌讨论：邀请多位员工就一个话题各自发表意见，"
                "然后让他们看到彼此的意见后再次补充，最后汇总共识。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "讨论话题"},
                    "participants": {
                        "type": "array",
                        "items": {"type": "string", "enum": worker_names},
                        "description": "参与讨论的员工名单（2-4人）",
                    },
                },
                "required": ["topic", "participants"],
            },
        },
        {
            "name": "get_dashboard",
            "description": "查看团队状态面板：任务概况、Worker 用量、成功率、知识库状态。",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "get_system_metadata",
            "description": "确定性查询当前系统版本、运行目录、持久化文件位置和已配置模型厂商。回答版本号/日志位置/存储位置等系统事实问题时优先使用。",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "consult_deputy",
            "description": (
                "咨询副经理维克托的意见。遇到重大决策、不确定的指派、或者需要第二意见时使用。"
                "副经理会独立分析并给出建议，可能会指出你忽略的问题。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "要咨询的问题。描述清楚背景、你已经做的决策、以及你担心的地方。",
                    },
                },
                "required": ["question"],
            },
        },
        {
            "name": "request_decision_review",
            "description": (
                "请求副经理复核你已经做的决策（如任务指派、评分、审核结论）。"
                "副经理会检查是否有失当之处，同意或提出异议。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "decision_summary": {
                        "type": "string",
                        "description": "已做决策的摘要：做了什么决定、为什么这么决定、涉及哪些员工。",
                    },
                },
                "required": ["decision_summary"],
            },
        },
        {
            "name": "project_setup",
            "description": (
                "【项目启动】接到新项目后，分析需求并为每个成员动态分配本项目的临时领域。\n"
                "这会生成 project_state.json，后续所有任务都基于此状态推进。\n"
                "仅在项目启动时使用一次。分配后通过 delegate_task 指派具体工作。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "project_description": {
                        "type": "string",
                        "description": "项目需求描述，越详细越好。包括目标、数据源、预期产出等。",
                    },
                },
                "required": ["project_description"],
            },
        },
        {
            "name": "update_project_step",
            "description": (
                "更新项目 pipeline 中某个步骤的状态。"
                "Worker 完成任务后，用此工具标记步骤进度。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "project_name": {
                        "type": "string",
                        "description": "项目名称（与 project_setup 中的一致）",
                    },
                    "step_name": {
                        "type": "string",
                        "description": "步骤名称",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["todo", "in_progress", "done", "failed"],
                        "description": "新状态",
                    },
                    "output_path": {
                        "type": "string",
                        "description": "产出文件路径（可选）",
                    },
                    "worker": {
                        "type": "string",
                        "description": "执行的 Worker 名称（可选）",
                    },
                },
                "required": ["project_name", "step_name", "status"],
            },
        },
        # ── v4 新工具定义 ──
        {
            "name": "delegate_with_verification",
            "description": (
                "【v4】将任务指派给员工，然后自动让 Sophia（审查）和 Nathaniel（验证）复核结果。"
                "不通过会自动重试。比 delegate_task 多了验证闭环。"
                "用于需要质量保证的关键任务。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "worker_name": {"type": "string", "enum": worker_names},
                    "task": {"type": "string", "description": "任务描述"},
                    "verifier_names": {
                        "type": "array",
                        "items": {"type": "string", "enum": worker_names},
                        "description": "验证者列表，默认 Sophia + Nathaniel",
                    },
                    "max_retries": {"type": "integer", "description": "最大重试次数，默认 3"},
                },
                "required": ["worker_name", "task"],
            },
        },
        {
            "name": "run_project_pipeline",
            "description": (
                "【v4】按 DAG 拓扑序执行项目 pipeline：找 ready 节点、并行执行、验证、失败阻断下游。"
                "中断后可恢复。是 v4 的核心执行引擎。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "project_name": {"type": "string", "description": "项目名称（与 project_setup 一致）"},
                },
                "required": ["project_name"],
            },
        },
        {
            "name": "run_convergence_loop",
            "description": (
                "【v4】迭代执行直到连续 N 轮无新问题。适用于持续审查、持续测试、持续改进文档。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "迭代任务描述"},
                    "worker_name": {"type": "string", "enum": worker_names},
                    "stable_rounds": {"type": "integer", "description": "需要连续多少轮无新问题才算收敛，默认 2"},
                    "max_rounds": {"type": "integer", "description": "最多执行多少轮，默认 5"},
                },
                "required": ["task", "worker_name"],
            },
        },
        {
            "name": "show_workflow_status",
            "description": "【v4】显示当前工作流的节点状态、执行日志和预算消耗。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "project_name": {"type": "string", "description": "项目名称"},
                },
                "required": ["project_name"],
            },
        },
        {
            "name": "request_replan",
            "description": "【v4】当 pipeline 节点标记为 needs_replan 时，请求 AI 重新生成项目规划。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "project_name": {"type": "string"},
                    "failed_node_id": {"type": "string"},
                },
                "required": ["project_name", "failed_node_id"],
            },
        },
    ]
