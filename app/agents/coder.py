"""编码 Agent：Workflow 的第一个节点。

两种输入形态：
* 给了 `--file`：把已有代码读进来，作为“待审查的代码版本”；
* 给了 `--requirement` 且配置了 LLM：调用模型按需求生成代码。

无论哪种方式，代码都会落盘成一个版本文件（`work/v1_xxx.py`），
下游的审查 Agent 从磁盘重新读取，保证“端到端”不是内存里的假流程。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..prompts import CODER_PROMPT
from ..tools.file_reader import FileReaderTool
from ..tools.file_writer import FileWriterTool
from ..workflow.bus import MessageBus
from ..workflow.node import AgentNode, NodeResult
from ..workflow.state import WorkflowState


class CoderAgent(AgentNode):
    def __init__(
        self,
        reader: FileReaderTool,
        writer: FileWriterTool,
        work_dir: str,
        llm: Any | None = None,
    ):
        super().__init__("coder", "编码 Agent", "根据需求生成代码，或把已有代码作为流程输入")
        self.reader = reader
        self.writer = writer
        self.work_dir = work_dir
        self.llm = llm

    def handle(self, state: WorkflowState, bus: MessageBus) -> NodeResult:
        bus.publish(
            "coder",
            "coder.start",
            requirement=state.requirement,
            source=state.source_path,
        )

        if state.source_path:
            code = self.reader.execute(state.source_path)
            origin = "读取已有代码"
            base_name = Path(state.source_path).name
        elif state.requirement and self.llm is not None:
            code = self._generate(state.requirement)
            origin = "根据需求生成代码"
            base_name = "generated.py"
        elif state.requirement:
            return NodeResult(
                status="failed",
                summary="未配置 LLM，无法根据需求生成代码；请改用 --file 指定已有代码",
            )
        else:
            return NodeResult(status="failed", summary="既没有 --file 也没有 --requirement，无法开始")

        if not code.strip():
            return NodeResult(status="failed", summary=f"代码内容为空（{origin}）")

        code = code.replace("\r\n", "\n")
        if not code.endswith("\n"):
            code += "\n"

        state.original_code = code
        state.code = code
        state.round = 1
        path = self._write_version(state, base_name, code, "init")
        line_count = len(code.splitlines())
        state.log("coder", f"{origin}，共 {line_count} 行，版本文件 {path}")
        bus.publish("coder", "code.ready", to="reviewer", path=path, lines=line_count, round=state.round)
        return NodeResult(
            summary=f"{origin}，共 {line_count} 行 → {path}",
            data={"path": path, "lines": line_count},
        )

    # --- 内部 ---

    def _write_version(self, state: WorkflowState, base_name: str, code: str, action: str) -> str:
        path = f"{self.work_dir}/v{state.round}_{base_name}"
        self.writer.write(path, code)
        state.work_path = path
        versions = state.artifacts.setdefault("versions", [])
        versions.append({"round": state.round, "path": path, "action": action, "lines": len(code.splitlines())})
        return path

    def _generate(self, requirement: str) -> str:
        response = self.llm.chat(
            [
                {"role": "system", "content": CODER_PROMPT},
                {"role": "user", "content": requirement},
            ],
            [],
        )
        text = (response.content or "").strip()
        code = extract_code(text)
        if not code.strip():
            raise RuntimeError("模型没有返回代码内容")
        return code


def extract_code(text: str) -> str:
    """从模型回复里取出代码：优先取第一个 ``` 代码块，否则整段当作代码。"""
    if "```" not in text:
        return text
    match = re.search(r"```[a-zA-Z0-9_+-]*\n(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1)
    return text
