"""工具层 —— ALL_TOOLS 定义 + execute_tool + 全部工具实现。

包含：
- truncate / _safe_walk_files / _format_file_results / _normalize_command_for_platform
- ALL_TOOLS 工具定义字典
- execute_tool 工具执行分发
- get_dashboard / track_api_call / track_failure 监控面板

ask_coworker 使用回调注入解决循环依赖:
  在 manager.py 中调用 runtime.tools.set_coworker_executor(run_worker)
"""
import json
import os
import sys
import subprocess
import fnmatch
import threading
import urllib.error
import urllib.request
from datetime import datetime

from runtime.business_connectors import database_query, internal_api_request

from runtime.persistence import (
    _workers_config,
    _load_json,
    TASK_BOARD_FILE,
    SCORE_FILE,
    KNOWLEDGE_FILE,
    search_knowledge,
)

# ── 常量 ────────────────────────────────────────────────
MAX_OUTPUT_CHARS = 6000
MAX_SEARCH_RESULTS = 50

print_lock = threading.Lock()

# ── ask_coworker 回调注入 ────────────────────────────────
# 由 manager.py 在初始化时设置，避免 runtime.tools ↔ runtime.workers 循环 import
_coworker_executor = None


def set_coworker_executor(fn):
    """注入 ask_coworker 用的 Worker 执行函数。fn(worker_cfg, question) -> dict。"""
    global _coworker_executor
    _coworker_executor = fn


# ── 截断保护 ────────────────────────────────────────────

def truncate(text: str, max_chars: int = MAX_OUTPUT_CHARS) -> str:
    """截断过长输出，防止 token 爆炸。"""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n... (截断，原输出共 {len(text)} 字符)"


def _safe_walk_files(search_path: str = ".") -> list[str]:
    """跨平台列出可搜索文件，避开常见依赖/缓存目录。"""
    root = os.path.abspath(search_path or ".")
    excluded_dirs = {".git", "__pycache__", "node_modules", ".venv", "venv", ".pytest_cache", ".codegraph"}
    files: list[str] = []
    for current_root, dirs, filenames in os.walk(root):
        dirs[:] = [d for d in dirs if d not in excluded_dirs]
        for filename in filenames:
            if filename.endswith((".pyc", ".pyo")):
                continue
            full_path = os.path.join(current_root, filename)
            try:
                rel_path = os.path.relpath(full_path, os.getcwd())
            except ValueError:
                rel_path = full_path
            files.append(rel_path)
    return files


def _format_file_results(paths: list[str], empty_message: str, max_chars: int = 3000) -> str:
    if not paths:
        return empty_message
    paths = sorted(paths)
    total = len(paths)
    shown = paths[:MAX_SEARCH_RESULTS]
    output = "\n".join(shown)
    if total > MAX_SEARCH_RESULTS:
        output += f"\n\n... (共 {total} 个文件，仅显示前 {MAX_SEARCH_RESULTS} 个。请缩小搜索范围)"
    return truncate(output, max_chars)


def _normalize_command_for_platform(cmd: str) -> str:
    """兼容 Worker 常输出的 Unix 命令，避免 Windows 终端直接失败。"""
    stripped = cmd.strip()
    if sys.platform == "win32" and stripped in {"ls -la", "ls -al", "ls -l", "ls"}:
        return "dir"
    return cmd


# ── 监控面板 ────────────────────────────────────────────
_worker_api_calls: dict[str, int] = {}
_worker_failures: dict[str, int] = {}


def track_api_call(worker_name: str):
    _worker_api_calls[worker_name] = _worker_api_calls.get(worker_name, 0) + 1


def track_failure(worker_name: str):
    _worker_failures[worker_name] = _worker_failures.get(worker_name, 0) + 1


def get_dashboard() -> str:
    """获取团队状态面板（含趋势和绩效）。"""
    tasks = _load_json(TASK_BOARD_FILE, [])
    scores = _load_json(SCORE_FILE, [])
    knowledge = _load_json(KNOWLEDGE_FILE, [])

    status_counts = {"todo": 0, "in_progress": 0, "done": 0, "failed": 0}
    priority_counts = {}
    for t in tasks:
        status_counts[t["status"]] = status_counts.get(t["status"], 0) + 1
        p = t.get("priority", "unknown")
        priority_counts[p] = priority_counts.get(p, 0) + 1

    total_tasks = len(tasks)
    done_rate = f"{status_counts['done'] / total_tasks * 100:.0f}%" if total_tasks > 0 else "N/A"

    lines = [
        "=" * 55,
        "  📊 团队状态面板",
        "=" * 55,
        f"  任务: {total_tasks} 个 | 完成率: {done_rate}",
        f"  待办 {status_counts['todo']} | 进行中 {status_counts['in_progress']} | 完成 {status_counts['done']} | 失败 {status_counts['failed']}",
        f"  优先级分布: {priority_counts}",
        f"  知识库: {len(knowledge)} 条 | 评分记录: {len(scores)} 条",
    ]

    if scores and _workers_config:
        worker_scores_summary = {}
        for s in scores:
            name = s.get("worker_name", "unknown")
            if name not in worker_scores_summary:
                worker_scores_summary[name] = {"count": 0, "total": 0, "scores": []}
            worker_scores_summary[name]["count"] += 1
            worker_scores_summary[name]["total"] += s.get("total", 0)
            worker_scores_summary[name]["scores"].append(s.get("total", 0))

        lines.append("  --- 员工绩效 ---")
        for name in _workers_config:
            if name in worker_scores_summary:
                ws = worker_scores_summary[name]
                avg = ws["total"] / ws["count"]
                recent = ws["scores"][-3:] if len(ws["scores"]) >= 3 else ws["scores"]
                trend = "📈" if len(recent) >= 2 and recent[-1] > recent[0] else ("📉" if len(recent) >= 2 and recent[-1] < recent[0] else "➡️")
                lines.append(f"  {name}: {ws['count']}次评分 | 均分 {avg:.1f}/15 | 趋势 {trend}")
            else:
                lines.append(f"  {name}: 暂无评分")

    if _workers_config:
        lines.append("  --- 用量统计 ---")
        for name in _workers_config:
            calls = _worker_api_calls.get(name, 0)
            fails = _worker_failures.get(name, 0)
            rate = f"{(1 - fails/max(calls,1))*100:.0f}%" if calls > 0 else "N/A"
            lines.append(f"  {name}: {calls} 次调用 | {fails} 次失败 | 成功率 {rate}")

    return "\n".join(lines)


# ── 全部工具定义 ──────────────────────────────────────────
ALL_TOOLS = {
    "get_current_time": {
        "name": "get_current_time",
        "description": "获取当前日期和时间",
        "input_schema": {
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": "时区，如 Asia/Shanghai，默认为 Asia/Shanghai",
                }
            },
        },
    },
    "read_file": {
        "name": "read_file",
        "description": "读取本地文件的内容",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "要读取的文件路径",
                }
            },
            "required": ["file_path"],
        },
    },
    "write_file": {
        "name": "write_file",
        "description": "将内容写入本地文件（会覆盖已有文件）",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "要写入的文件路径",
                },
                "content": {
                    "type": "string",
                    "description": "要写入的文件内容",
                },
            },
            "required": ["file_path", "content"],
        },
    },
    "run_command": {
        "name": "run_command",
        "description": "执行一条 shell 命令并返回输出",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令"}
            },
            "required": ["command"],
        },
    },
    "fetch_url": {
        "name": "fetch_url",
        "description": "获取一个 URL 的内容（HTTP GET），可用于查阅在线文档、API 参考等",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要获取的 URL（https://...）"},
            },
            "required": ["url"],
        },
    },
    "search_code": {
        "name": "search_code",
        "description": (
            "在项目文件中搜索指定的文本或正则表达式，返回匹配的文件路径和行内容。"
            "用于快速定位函数定义、变量引用、TODO 标记、潜在 bug 等。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "搜索的文本或正则表达式，如 'def divide'、'eval('、'TODO'",
                },
                "path": {
                    "type": "string",
                    "description": "搜索目录，默认为当前目录。如 'src/'、'.'",
                },
                "file_types": {
                    "type": "string",
                    "description": "文件类型过滤，如 '*.py'、'*.js'、'*.{py,js}'，留空则搜所有文本文件",
                },
            },
            "required": ["pattern"],
        },
    },
    "ask_coworker": {
        "name": "ask_coworker",
        "description": (
            "向另一位员工求助——把你的问题发给他，他会用他的工具和专长帮你解决，"
            "然后把答案返回给你。用于遇到困难时找更专业的同事帮忙，或者需要分工协作。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "worker_name": {
                    "type": "string",
                    "description": "求助的员工名称，如「亚历克斯」「索菲亚」",
                },
                "question": {
                    "type": "string",
                    "description": "你的问题或请求，越具体越好。可以附上当前遇到的上下文。",
                },
            },
            "required": ["worker_name", "question"],
        },
    },
    "search_knowledge": {
        "name": "search_knowledge",
        "description": "搜索团队共享知识库，查找之前记录的经验、决策和最佳实践",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词，如 'calculator bug'、'deployment'"},
            },
            "required": ["query"],
        },
    },
    "convert_document": {
        "name": "convert_document",
        "description": "将 PDF/Word/PPT/Excel 等文件转换为 Markdown 文本，方便阅读和分析",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "要转换的文件路径"},
            },
            "required": ["file_path"],
        },
    },
    "github_create_pr": {
        "name": "github_create_pr",
        "description": "在 GitHub 上创建 Pull Request",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "PR 标题"},
                "body": {"type": "string", "description": "PR 描述"},
                "base_branch": {"type": "string", "description": "目标分支，默认 main"},
            },
            "required": ["title", "body"],
        },
    },
    "github_list_issues": {
        "name": "github_list_issues",
        "description": "列出 GitHub Issues",
        "input_schema": {
            "type": "object",
            "properties": {
                "state": {"type": "string", "enum": ["open", "closed", "all"], "description": "状态过滤，默认 open"},
            },
        },
    },
    "save_template": {
        "name": "save_template",
        "description": "将当前的工作流程或经验沉淀为可复用的模板文件，供团队后续参考",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "模板名称"},
                "content": {"type": "string", "description": "模板内容（Markdown 格式）"},
            },
            "required": ["name", "content"],
        },
    },
    "find_files": {
        "name": "find_files",
        "description": (
            "按文件名模式查找文件，返回匹配的文件路径列表。"
            "用于快速找到特定名称的文件，如 'test_*.py'、'*.json'。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "文件名匹配模式，如 'test_*.py'、'*.json'、'*.md'",
                },
                "path": {
                    "type": "string",
                    "description": "搜索目录，默认为当前目录。如 'src/'、'.'",
                },
            },
            "required": ["pattern"],
        },
    },
    "database_query": {
        "name": "database_query",
        "description": (
            "查询本地 demo 业务 SQLite 数据库。只允许 SELECT/WITH 只读查询，"
            "用于演示订单、客户、工单等业务数据接入。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "只读 SQL，例如 SELECT * FROM tickets WHERE status = 'open'",
                },
                "max_rows": {
                    "type": "integer",
                    "description": "最多返回行数，默认 20，最大 50",
                },
            },
            "required": ["query"],
        },
    },
    "internal_api_request": {
        "name": "internal_api_request",
        "description": (
            "调用受控的公司内部 API demo。只允许 GET 白名单路径，例如 "
            "/tickets/ticket_9001、/orders/ord_1001、/customers/cust_001、/metrics/daily。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "白名单 API 路径"},
                "params": {"type": "object", "description": "可选 query 参数"},
            },
            "required": ["path"],
        },
    },
}


# ── 工具执行 ──────────────────────────────────────────────

def execute_tool(name: str, args: dict) -> str:
    if args is None:
        args = {}
    if name == "get_current_time":
        tz = args.get("timezone", "Asia/Shanghai")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return f"当前时间 ({tz}): {now}"

    if name == "read_file":
        path = args["file_path"]
        try:
            with open(path, encoding="utf-8") as f:
                return truncate(f.read())
        except FileNotFoundError:
            return f"文件不存在: {path}"
        except Exception as e:
            return f"读取失败: {e}"

    if name == "write_file":
        path = args["file_path"]
        content = args["content"]
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"文件写入成功: {path} ({len(content)} 字符)"
        except Exception as e:
            return f"写入失败: {e}"

    if name == "run_command":
        cmd = _normalize_command_for_platform(args["command"])
        try:
            env = os.environ.copy()
            env.setdefault("PYTHONUTF8", "1")
            env.setdefault("PYTHONIOENCODING", "utf-8")
            if sys.platform == "win32":
                env.setdefault("PYTHONLEGACYWINDOWSSTDIO", "0")
            result = subprocess.run(
                cmd, shell=True, capture_output=True, encoding="utf-8", errors="replace",
                timeout=30, cwd=os.getcwd(), env=env,
            )
            stdout = (result.stdout or "").strip()
            stderr = (result.stderr or "").strip()
            output = stdout or stderr
            if result.returncode != 0 and not output:
                output = f"命令退出码 {result.returncode}，无输出"
            return truncate(output) if output else "(无输出)"
        except subprocess.TimeoutExpired:
            return "命令超时（30秒）"
        except Exception as e:
            return f"命令执行失败: {e}"

    if name == "fetch_url":
        url = args["url"]
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "MultiAgent/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                status = resp.status
                return truncate(f"HTTP {status}\n{body}", 10000)
        except urllib.error.URLError as e:
            return f"请求失败: {e}"
        except Exception as e:
            return f"获取失败: {e}"

    if name == "search_code":
        pattern = args["pattern"]
        search_path = args.get("path", ".")
        file_types = args.get("file_types", "")

        include_patterns = [p.strip() for p in file_types.split(",") if p.strip()] if file_types else ["*"]
        matches: list[str] = []
        try:
            for file_path in _safe_walk_files(search_path):
                filename = os.path.basename(file_path)
                if include_patterns != ["*"] and not any(fnmatch.fnmatch(filename, p) for p in include_patterns):
                    continue
                try:
                    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                        for line_no, line in enumerate(f, start=1):
                            if pattern in line:
                                matches.append(f"{file_path}:{line_no}: {line.rstrip()}")
                                break
                except (OSError, UnicodeError):
                    continue
            if not matches:
                return f"未找到匹配「{pattern}」的结果"
            total = len(matches)
            output = "\n".join(matches[:MAX_SEARCH_RESULTS])
            if total > MAX_SEARCH_RESULTS:
                output += f"\n\n... (共 {total} 条匹配，仅显示前 {MAX_SEARCH_RESULTS} 条。请缩小搜索范围)"
            return truncate(output)
        except Exception as e:
            return f"搜索失败: {e}"

    if name == "find_files":
        pattern = args["pattern"]
        search_path = args.get("path", ".")

        try:
            matched = [
                path for path in _safe_walk_files(search_path)
                if fnmatch.fnmatch(os.path.basename(path), pattern)
                or fnmatch.fnmatch(path.replace("\\", "/"), pattern)
            ]
            return _format_file_results(matched, f"未找到匹配「{pattern}」的文件", 3000)
        except Exception as e:
            return f"查找失败: {e}"

    if name == "convert_document":
        path = args["file_path"]
        try:
            from markitdown import MarkItDown
            md = MarkItDown()
            result = md.convert(path)
            return truncate(result.text_content)
        except ImportError:
            return "markitdown 未安装，请运行: pip install markitdown"
        except FileNotFoundError:
            return f"文件不存在: {path}"
        except Exception as e:
            return f"文档转换失败: {e}"

    if name == "github_create_pr":
        title = args["title"]
        body = args["body"]
        base = args.get("base_branch", "main")
        try:
            result = subprocess.run(
                f'gh pr create --title "{title}" --body "{body}" --base {base}',
                shell=True, capture_output=True,
                encoding="utf-8", errors="replace", timeout=30,
                cwd=os.getcwd(),
            )
            output = (result.stdout or "").strip() or (result.stderr or "").strip()
            return output if output else "PR 创建失败（请确认 gh CLI 已登录且当前在 git 仓库中）"
        except Exception as e:
            return f"GitHub 操作失败: {e}"

    if name == "github_list_issues":
        state = args.get("state", "open")
        try:
            result = subprocess.run(
                f"gh issue list --state {state} --limit 20",
                shell=True, capture_output=True,
                encoding="utf-8", errors="replace", timeout=20,
                cwd=os.getcwd(),
            )
            output = (result.stdout or "").strip()
            return output if output else "没有找到 Issues。"
        except Exception as e:
            return f"GitHub 操作失败: {e}"

    if name == "save_template":
        name = args["name"]
        content = args["content"]
        filepath = f"templates/{name}.md"
        try:
            os.makedirs("templates", exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            return f"模板已保存: {filepath}"
        except Exception as e:
            return f"保存模板失败: {e}"

    if name == "search_knowledge":
        return search_knowledge(args["query"])

    if name == "database_query":
        try:
            return json.dumps(
                database_query(args["query"], args.get("max_rows", 20)),
                ensure_ascii=False,
                indent=2,
            )
        except Exception as e:
            return f"database_query 失败: {e}"

    if name == "internal_api_request":
        try:
            return json.dumps(
                internal_api_request("GET", args["path"], args.get("params") or {}),
                ensure_ascii=False,
                indent=2,
            )
        except Exception as e:
            return f"internal_api_request 失败: {e}"

    if name == "ask_coworker":
        coworker_name = args["worker_name"]
        question = args["question"]

        if not _workers_config:
            return "错误：员工配置未加载，无法求助"

        if coworker_name not in _workers_config:
            available = ", ".join(_workers_config.keys())
            return f"找不到员工「{coworker_name}」。可选: {available}"

        cfg = _workers_config[coworker_name]

        if _coworker_executor is None:
            return "错误：ask_coworker 未初始化（请调用 set_coworker_executor）"

        with print_lock:
            print(f"\n      🤝 向 Worker-{coworker_name}（{cfg['role']}）求助...\n")

        coworker_result = _coworker_executor(cfg, question)

        answer = coworker_result["result"]
        with print_lock:
            print(f"      🤝 Worker-{coworker_name} 已回复\n")
        return f"[Worker-{coworker_name}（{cfg['role']}）的回复]\n{answer}"

    return f"未知工具: {name}"
