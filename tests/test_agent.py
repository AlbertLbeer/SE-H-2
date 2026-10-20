import json
import tempfile
import unittest
from pathlib import Path

from app.agent import CodeReviewAgent, ToolRegistry
from app.llm_client import LLMResponse
from app.tools.file_reader import FileReaderTool


class FakeLLMClient:
    def __init__(self):
        self.calls = []

    def chat(self, messages, tools):
        self.calls.append(list(messages))
        if len(self.calls) == 1:
            return LLMResponse(
                content=None,
                tool_calls=[
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"path": "sample.py"}),
                        },
                    }
                ],
                raw={},
            )
        return LLMResponse(content="发现一个问题。", tool_calls=[], raw={})


class AgentTests(unittest.TestCase):
    def test_agent_executes_tool_then_returns_final_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.py").write_text("print('ok')\n", encoding="utf-8")
            client = FakeLLMClient()
            registry = ToolRegistry({"read_file": FileReaderTool(root)})
            agent = CodeReviewAgent(client, registry)

            answer = agent.run("请审查 sample.py")

            self.assertEqual(answer, "发现一个问题。")
            self.assertEqual(len(client.calls), 2)
            second_turn = client.calls[1]
            self.assertEqual(second_turn[-1]["role"], "tool")
            self.assertIn("print('ok')", second_turn[-1]["content"])


if __name__ == "__main__":
    unittest.main()
