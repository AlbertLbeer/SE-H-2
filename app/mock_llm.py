from __future__ import annotations

from typing import Any

from .llm_client import LLMResponse


class MockLLMClient:
    """本地流程演示用的假模型，不代表真实审查质量。"""

    def __init__(self):
        self.step = 0

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        path = "examples/buggy.py"
        for message in reversed(messages):
            if message.get("role") == "user" and "文件路径：" in message.get("content", ""):
                path = message["content"].split("文件路径：", 1)[-1].strip() or path
                break
        if self.step == 0:
            self.step += 1
            return LLMResponse(
                content=None,
                tool_calls=[
                    {
                        "id": "mock-read-1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": f'{{"path":"{path}"}}'},
                    }
                ],
                raw={},
            )
        if self.step == 1:
            self.step += 1
            return LLMResponse(
                content=None,
                tool_calls=[
                    {
                        "id": "mock-syntax-1",
                        "type": "function",
                        "function": {"name": "check_python_syntax", "arguments": f'{{"path":"{path}"}}'},
                    }
                ],
                raw={},
            )
        return LLMResponse(
            content=(
                "【Mock 演示】Agent 已完成工具调用。\n"
                "请配置真实 LLM_API_KEY 和 LLM_MODEL 后，再生成真实的代码审查报告。"
            ),
            tool_calls=[],
            raw={},
        )
