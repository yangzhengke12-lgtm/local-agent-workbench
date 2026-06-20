"""
单个 Worker 单次执行脚本
供 Manager（Claude Code 窗口模式）调度使用

用法: python worker_run.py <员工名> <任务描述>
示例: python worker_run.py "索菲亚" "审查 calculator.py 的安全性"
"""
import sys
from manager import load_workers, run_worker


def main():
    if len(sys.argv) < 3:
        print("用法: python worker_run.py <员工名> <任务描述>")
        print("示例: python worker_run.py 索菲亚 审查 calculator.py")
        sys.exit(1)

    worker_name = sys.argv[1]
    task = sys.argv[2]

    workers = load_workers()
    if worker_name not in workers:
        available = ", ".join(workers.keys())
        print(f"错误: 未知员工「{worker_name}」。可选: {available}")
        sys.exit(1)

    cfg = workers[worker_name]
    print(f"🔧 Worker-{worker_name}（{cfg['role']}）已就绪，开始执行任务...\n")

    result = run_worker(cfg, task, use_memory=False)

    print(f"\n✅ Worker-{worker_name} 任务完成。")
    return result


if __name__ == "__main__":
    main()
