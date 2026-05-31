"""
Todo API — v4 Agentic Workflow Runtime 驱动脚本。

DAG: Design → Implement → Test → Docs → Review
- Implement 节点有故意 bug（completed 更新未实现）
- Test 节点运行 pytest，test_update_completed 将失败
- Verifier 检测到测试失败 → needs_retry → Implement 重试修复
- Implement 修复后 Test 重新通过 → Docs → Review

用法：
    python run_v4_pipeline.py              # 完整执行
    python run_v4_pipeline.py --resume     # 从中断恢复
    python run_v4_pipeline.py --status     # 查看工作流状态
"""
import sys
import os
import json
import argparse

# 0. 先加载项目根目录的 .env（manager.py 的 load_dotenv() 需要）
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT_DIR, ".env"))

# 1. manager.py 使用相对路径（workers.json, project_states/ 等），必须在其所在目录运行
MANAGER_DIR = os.path.join(ROOT_DIR, "AI-Agent管理系统")
os.chdir(MANAGER_DIR)
sys.path.insert(0, MANAGER_DIR)

from manager import (
    load_workers, project_setup, run_project_pipeline,
    load_workflow_run, resume_workflow_run, save_workflow_run,
    _summarize_run, PROJECT_STATE_DIR,
)

PROJECT_NAME = "FastAPI Todo API Project"


def _find_project_name() -> str | None:
    """从 project_states/ 中找到最新的 Todo API 项目名。"""
    import glob as _glob
    if not os.path.isdir(PROJECT_STATE_DIR):
        return None
    candidates = [
        f for f in _glob.glob(os.path.join(PROJECT_STATE_DIR, "*_state.json"))
        if "FastAPI" in os.path.basename(f) or "Todo" in os.path.basename(f)
    ]
    if not candidates:
        # fallback: 取最新的 state 文件
        candidates = _glob.glob(os.path.join(PROJECT_STATE_DIR, "*_state.json"))
    if not candidates:
        return None
    # 按修改时间排序，取最新
    candidates.sort(key=os.path.getmtime, reverse=True)
    filename = os.path.basename(candidates[0])
    # 去掉 _state.json 后缀
    return filename.rsplit("_state.json", 1)[0]


def cmd_setup():
    """Phase 1: 生成 Pipeline DAG。"""
    print("=" * 60)
    print("  Todo API — v4 Pipeline Setup")
    print("=" * 60)

    workers = load_workers()
    if not workers:
        print("[FAIL] 无法加载 workers.json")
        return

    description = (
        "项目名称：FastAPI Todo API\n\n"
        "目录：C:\\Users\\YzK12\\Desktop\\my-agent\\todo-api\n\n"
        "main.py 是一个 FastAPI Todo CRUD 应用。第73-74行的 completed 字段更新代码被注释了：\n"
        "  # if body.completed is not None:\n"
        "  #     todo[\"completed\"] = body.completed\n"
        "需要取消这两行的注释（去掉 # 和缩进空格），使 PUT /todos/{id} 能正确更新 completed 字段。\n"
        "test_main.py 有 12 个测试，其中 test_update_completed 因上述 bug 会失败。\n"
        "其他文件（requirements.txt 等）已就绪。\n\n"
        "Pipeline 步骤（仅2步，极简）：\n"
        "1. 【修复实现】只做一件事：用 write_file 保存 main.py，唯一改动是取消第73-74行的注释。不要修改其他任何代码！不要重写文件！只改这两行！\n"
        "2. 【运行测试】在 C:\\Users\\YzK12\\Desktop\\my-agent\\todo-api 执行 python -m pytest test_main.py -v，验证 12 passed。"
    )

    result = project_setup(workers, description)
    print(result)
    # 输出实际项目名供后续步骤使用
    actual_name = _find_project_name()
    if actual_name:
        print(f"\n[Driver] 检测到项目名: {actual_name}")


def cmd_run():
    """Phase 2: 执行 Pipeline。"""
    workers = load_workers()
    if not workers:
        print("[FAIL] 无法加载 workers.json")
        return

    project_name = _find_project_name()
    if not project_name:
        print("[FAIL] 未找到项目状态文件。请先运行 --setup。")
        return

    print("=" * 60)
    print(f"  Todo API — v4 Pipeline Execution: {project_name}")
    print("=" * 60)

    result = run_project_pipeline(project_name, workers)
    print("\n" + "=" * 60)
    print("  Pipeline 执行结果")
    print("=" * 60)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_status():
    """查看工作流状态。"""
    project_name = _find_project_name()
    if not project_name:
        print("未找到项目状态文件。请先运行 --setup。")
        return
    run = load_workflow_run(project_name)
    if run is None:
        print(f"未找到项目 '{PROJECT_NAME}' 的工作流记录。请先运行 --setup 然后 --run。")
        return
    summary = _summarize_run(run.nodes, run.status)
    print("=" * 60)
    print(f"  Workflow Status: {run.run_id}")
    print(f"  Project: {run.project_name}")
    print(f"  Status: {run.status}")
    print(f"  Version: v{run.version}")
    print("=" * 60)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if run.execution_log:
        print(f"\n  执行日志 ({len(run.execution_log)} 条):")
        for entry in run.execution_log[-10:]:
            print(f"    [{entry.get('timestamp', '')}] {entry.get('node', '')} → {entry.get('status', '')}")


def cmd_resume():
    """从中断恢复。"""
    workers = load_workers()
    if not workers:
        print("[FAIL] 无法加载 workers.json")
        return

    project_name = _find_project_name()
    if not project_name:
        print("[FAIL] 未找到项目状态文件。请先运行 --setup。")
        return

    print("=" * 60)
    print(f"  Todo API — v4 Pipeline Resume: {project_name}")
    print("=" * 60)

    result = resume_workflow_run(project_name, workers)
    print("\n" + "=" * 60)
    print("  Resume 结果")
    print("=" * 60)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Todo API v4 Pipeline Driver")
    parser.add_argument("--setup", action="store_true", help="生成 Pipeline DAG")
    parser.add_argument("--run", action="store_true", help="执行 Pipeline")
    parser.add_argument("--status", action="store_true", help="查看工作流状态")
    parser.add_argument("--resume", action="store_true", help="从中断恢复")
    args = parser.parse_args()

    # 默认：完整流程
    if not any([args.setup, args.run, args.status, args.resume]):
        args.setup = True
        args.run = True

    if args.setup:
        cmd_setup()

    if args.run:
        cmd_run()

    if args.status:
        cmd_status()

    if args.resume:
        cmd_resume()


if __name__ == "__main__":
    main()
