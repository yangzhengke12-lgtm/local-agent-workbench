"""消息 sanitize —— 移除不安全字段，确保跨厂商兼容。

依赖：无（纯数据转换）
"""

# 会被 sanitize 移除的字段（非标准、厂商特有、或回传危险）
_UNSAFE_MESSAGE_FIELDS = {
    "thinking", "signature", "cache_control", "id",
    "stop_reason", "stop_sequence", "usage", "model",
    "tool_calls",    # OpenAI 格式的 tool_calls（不同于 Anthropic tool_use）
}


def sanitize_messages_for_provider(messages: list[dict], provider_key: str = "") -> list[dict]:
    """移除厂商不安全字段，返回安全的 messages 副本。

    规则：
    - 顶层保留：role, content
    - content 为 str：保留
    - content 为 list：只保留 type=="text" 的 block
    - 移除 thinking, tool_use, tool_result block（不持久化，不跨厂商传递）
    - 消息清理后 content 为空：丢弃该消息
    - 不修改原始 messages，返回新 list
    """
    cleaned: list[dict] = []

    for msg in messages:
        safe_msg: dict = {}
        if "role" in msg:
            safe_msg["role"] = msg["role"]
        else:
            continue

        raw_content = msg.get("content")

        if isinstance(raw_content, str):
            safe_msg["content"] = raw_content
        elif isinstance(raw_content, list):
            kept_blocks: list[dict] = []
            for block in raw_content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    kept_blocks.append({
                        "type": "text",
                        "text": block.get("text", ""),
                    })
                # thinking / tool_use / tool_result 全部丢弃
            if kept_blocks:
                safe_msg["content"] = kept_blocks
            else:
                continue  # content list 清空后丢弃整条消息
        else:
            continue

        # 移除所有不安全顶层字段
        for field in _UNSAFE_MESSAGE_FIELDS:
            safe_msg.pop(field, None)
        extra_keys = set(safe_msg.keys()) - {"role", "content"}
        for key in extra_keys:
            safe_msg.pop(key, None)

        cleaned.append(safe_msg)

    return cleaned
