"""
一个简单的 AI Agent —— 它会和你对话，并能在需要时调用工具。
"""
import json
import os
from datetime import datetime

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

client = Anthropic(
    api_key=os.environ["ANTHROPIC_API_KEY"],
    base_url=os.environ.get("ANTHROPIC_BASE_URL"),
)
MODEL = os.environ.get("ANTHROPIC_MODEL", "deepseek-v4-pro")

# ── 工具定义 ──────────────────────────────────────────────
TOOLS = [
    {
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
    {
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
]

# ── 工具执行 ──────────────────────────────────────────────
def run_tool(name: str, args: dict) -> str:
    if name == "get_current_time":
        tz = args.get("timezone", "Asia/Shanghai")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return f"当前时间 ({tz}): {now}"

    if name == "read_file":
        path = args["file_path"]
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return f"文件不存在: {path}"
        except Exception as e:
            return f"读取失败: {e}"

    return f"未知工具: {name}"

# ── 对话循环 ──────────────────────────────────────────────
def main():
    print("=" * 50)
    print(" 我的 Agent 已启动 (输入 /quit 退出)")
    print("=" * 50)

    messages = []
    system_prompt = "你是一个有用的助手，回答问题时请使用中文。"

    while True:
        try:
            user_input = input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if user_input.lower() in ("/quit", "/exit", "/q"):
            print("再见！")
            break
        if not user_input:
            continue

        messages.append({"role": "user", "content": user_input})

        # 调用 API
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=system_prompt,
            tools=TOOLS,
            messages=messages,
        )

        # 处理回复（可能有文本 + 工具调用）
        assistant_content = []

        for block in response.content:
            if block.type == "text":
                print(f"\nAgent: {block.text}")
                assistant_content.append({"type": "text", "text": block.text})

            elif block.type == "thinking":
                assistant_content.append({
                    "type": "thinking",
                    "thinking": block.thinking,
                    "signature": block.signature,
                })

            elif block.type == "tool_use":
                tool_name = block.name
                tool_args = block.input
                print(f"\n[调用工具: {tool_name}({json.dumps(tool_args, ensure_ascii=False)})]")

                result = run_tool(tool_name, tool_args)
                print(f"[工具返回: {result[:200]}]")

                # 把本次回复加入历史
                assistant_content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
                messages.append({"role": "assistant", "content": assistant_content})
                assistant_content = []

                # 把工具结果发给模型
                messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        }
                    ],
                })

                # 继续对话，让模型处理工具结果
                follow_up = client.messages.create(
                    model=MODEL,
                    max_tokens=4096,
                    system=system_prompt,
                    tools=TOOLS,
                    messages=messages,
                )
                for fb in follow_up.content:
                    if fb.type == "text":
                        print(f"\nAgent: {fb.text}")
                        assistant_content.append({"type": "text", "text": fb.text})
                    elif fb.type == "thinking":
                        assistant_content.append({
                            "type": "thinking",
                            "thinking": fb.thinking,
                            "signature": fb.signature,
                        })

        messages.append({"role": "assistant", "content": assistant_content})


if __name__ == "__main__":
    main()
