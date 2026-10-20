from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from .prompts import SYSTEM_PROMPT


class ChatClient(Protocol):
    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]): ...


@dataclass
class ToolRegistry:
    tools: dict[str, Any]

    def schemas(self) -> list[dict[str, Any]]:
        # 只把工具的公开 schema 交给模型，具体实现仍由本地程序掌控。
        return [tool.schema() for tool in self.tools.values()]

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        if name not in self.tools:
            raise ValueError(f"未知工具：{name}")
        return self.tools[name].execute(**arguments)


class CodeReviewAgent:
    def __init__(self, client: ChatClient, registry: ToolRegistry, max_iterations: int = 6, max_history: int = 12):
        self.client = client
        self.registry = registry
        self.max_iterations = max_iterations
        self.max_history = max_history
        self.history: list[dict[str, Any]] = []

    def run(self, user_input: str) -> str:
        # 每一轮都带上系统规则、有限历史和本次用户请求。
        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(self.history[-self.max_history :])
        messages.append({"role": "user", "content": user_input})

        for _ in range(self.max_iterations):
            # 模型可以直接回答，也可以先返回一个或多个工具调用。
            response = self.client.chat(messages, self.registry.schemas())
            assistant_message: dict[str, Any] = {"role": "assistant", "content": response.content}
            if response.tool_calls:
                assistant_message["tool_calls"] = response.tool_calls
            messages.append(assistant_message)

            if not response.tool_calls:
                # 没有工具调用就认为模型已经给出最终审查报告。
                answer = response.content or "模型没有返回文字结果。"
                self.history.extend([
                    {"role": "user", "content": user_input},
                    {"role": "assistant", "content": answer},
                ])
                return answer

            for tool_call in response.tool_calls:
                # 工具参数来自模型，必须解析并交给本地注册表校验和执行。
                function = tool_call.get("function", {})
                name = function.get("name", "")
                call_id = tool_call.get("id", name)
                try:
                    arguments = json.loads(function.get("arguments", "{}"))
                    result = self.registry.execute(name, arguments)
                except (json.JSONDecodeError, TypeError, ValueError, FileNotFoundError) as exc:
                    # 工具失败也作为上下文返回给模型，让模型决定如何解释或继续。
                    result = f"工具调用失败：{exc}"
                messages.append({"role": "tool", "tool_call_id": call_id, "name": name, "content": result})

        return "Agent 达到最大推理轮数，未能生成最终报告。请缩小代码范围后重试。"
