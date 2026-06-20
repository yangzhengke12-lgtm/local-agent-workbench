import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from manager import sanitize_messages_for_provider


class TestSanitizeMessagesForProvider(unittest.TestCase):

    def test_thinking_block_removed(self) -> None:
        """Message with type='thinking' block in content list: thinking block stripped, only text blocks remain."""
        messages: list[dict] = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "thinking", "thinking": "hmm..."},
                ],
            }
        ]
        result = sanitize_messages_for_provider(messages)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]["content"]), 1)
        self.assertEqual(result[0]["content"][0]["type"], "text")
        self.assertEqual(result[0]["content"][0]["text"], "hello")

    def test_text_block_preserved(self) -> None:
        """Message with type='text' block: preserved with only type and text fields."""
        messages: list[dict] = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello", "extra_field": "should be removed"},
                ],
            }
        ]
        result = sanitize_messages_for_provider(messages)
        self.assertEqual(len(result), 1)
        block = result[0]["content"][0]
        self.assertEqual(block, {"type": "text", "text": "hello"})
        self.assertNotIn("extra_field", block)

    def test_tool_use_removed(self) -> None:
        """Message with tool_use block only: entire message dropped."""
        messages: list[dict] = [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "read_file", "input": {}},
                ],
            }
        ]
        result = sanitize_messages_for_provider(messages)
        self.assertEqual(len(result), 0)

    def test_tool_result_removed(self) -> None:
        """Message with tool_result block only: entire message dropped."""
        messages: list[dict] = [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "abc", "content": "result"},
                ],
            }
        ]
        result = sanitize_messages_for_provider(messages)
        self.assertEqual(len(result), 0)

    def test_string_content_preserved(self) -> None:
        """Content is a plain string: kept as-is."""
        messages: list[dict] = [
            {"role": "user", "content": "hello world"},
        ]
        result = sanitize_messages_for_provider(messages)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], {"role": "user", "content": "hello world"})

    def test_empty_content_dropped(self) -> None:
        """Content list becomes empty after stripping non-text blocks: message dropped."""
        messages: list[dict] = [
            {
                "role": "user",
                "content": [
                    {"type": "thinking", "thinking": "..."},
                ],
            }
        ]
        result = sanitize_messages_for_provider(messages)
        self.assertEqual(len(result), 0)

    def test_original_not_mutated(self) -> None:
        """Input messages list is not modified by the function."""
        messages: list[dict] = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "thinking", "thinking": "hmm..."},
                ],
            }
        ]
        original = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "thinking", "thinking": "hmm..."},
                ],
            }
        ]
        _ = sanitize_messages_for_provider(messages)
        self.assertEqual(messages, original)

    def test_unsafe_top_fields_removed(self) -> None:
        """Message with top-level thinking/signature/cache_control/id fields: those fields removed, only role and content survive."""
        messages: list[dict] = [
            {
                "role": "user",
                "content": "hello",
                "thinking": "...",
                "signature": "...",
                "cache_control": "...",
                "id": "123",
            }
        ]
        result = sanitize_messages_for_provider(messages)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], {"role": "user", "content": "hello"})

    def test_mixed_content_handled(self) -> None:
        """Content has text + thinking + tool_use blocks: only text blocks survive."""
        messages: list[dict] = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "a"},
                    {"type": "thinking", "thinking": "..."},
                    {"type": "tool_use", "name": "x", "input": {}},
                    {"type": "text", "text": "b"},
                ],
            }
        ]
        result = sanitize_messages_for_provider(messages)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]["content"]), 2)
        self.assertEqual(result[0]["content"][0], {"type": "text", "text": "a"})
        self.assertEqual(result[0]["content"][1], {"type": "text", "text": "b"})

    def test_msg_without_role_dropped(self) -> None:
        """Message missing 'role' field: dropped."""
        messages: list[dict] = [
            {"content": "hello"},
        ]
        result = sanitize_messages_for_provider(messages)
        self.assertEqual(len(result), 0)

    def test_msg_without_content_dropped(self) -> None:
        """Message missing 'content' field: dropped."""
        messages: list[dict] = [
            {"role": "user"},
        ]
        result = sanitize_messages_for_provider(messages)
        self.assertEqual(len(result), 0)

    def test_provider_key_accepted(self) -> None:
        """Function accepts provider_key parameter without error."""
        messages: list[dict] = [
            {"role": "user", "content": "hello"},
        ]
        # Should not raise
        result = sanitize_messages_for_provider(messages, provider_key="deepseek")
        self.assertEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()
