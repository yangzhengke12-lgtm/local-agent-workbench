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
    _summarize_run,
)

PROJECT_NAME = "FastAPI Todo API Project"


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
        "创建一个 FastAPI Todo API 项目，目录为 C:\\Users\\YzK12\\Desktop\\my-agent\\todo-api。\n\n"
        "项目已包含以下文件（不要覆盖，只能修改）：\n"
        "- main.py: FastAPI 应用，包含 CRUD 但 completed 字段更新逻辑有 bug（被注释掉了）\n"
        "- test_main.py: pytest 测试，12 个测试用例，其中 test_update_completed 会失败\n"
        "- requirements.txt: 依赖包\n\n"
        "Pipeline 步骤：\n"
        "1. 【设计审查】阅读 main.py 和 test_main.py，确认项目结构\n"
        "2. 【修复实现】取消 main.py 中 completed 字段更新的注释（第 ~63-65 行），使 test_update_completed 通过\n"
        "3. 【运行测试】在 todo-api 目录下运行 pytest，确认 12/12 全部通过\n"
        "4. 【编写文档】创建 README.md，说明启动方式、API 端点、测试方法\n"
        "5. 【代码审查】Sophia 审查最终代码质量"
    )

    result = project_setup(workers, description)
    print(result)


def cmd_run():
    """Phase 2: 执行 Pipeline。"""
    workers = load_workers()
    if not workers:
        print("[FAIL] 无法加载 workers.json")
        return

    print("=" * 60)
    print("  Todo API — v4 Pipeline Execution")
    print("=" * 60)

    result = run_project_pipeline(PROJECT_NAME, workers)
    print("\n" + "=" * 60)
    print("  Pipeline 执行结果")
    print("=" * 60)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_status():
    """查看工作流状态。"""
    run = load_workflow_run(PROJECT_NAME)
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

    print("=" * 60)
    print("  Todo API — v4 Pipeline Resume")
    print("=" * 60)

    result = resume_workflow_run(PROJECT_NAME, workers)
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
