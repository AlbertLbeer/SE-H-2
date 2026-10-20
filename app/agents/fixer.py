"""修复 Agent。

工作方式：先从总线上读取审查结论，再对代码做 **逐条、可验证** 的自动修复：

1. 每修一条之前重新扫描一次，保证 `ast` 位置信息始终对应当前源码；
2. 每次修复后都重新 `ast.parse`，失败则放弃该条并记入失败列表；
3. 修完把新版本落盘，生成 diff，把结果广播回审查节点。

无法自动修复的问题不会强行改写，而是原样保留并附上建议，交给报告节点提示人工处理。
"""

from __future__ import annotations

from pathlib import Path

from ..tools.autofix import apply_fix
from ..tools.diffing import changed_line_count, unified_diff
from ..tools.file_reader import FileReaderTool
from ..tools.file_writer import FileWriterTool
from ..tools.static_rules import scan
from ..workflow.bus import MessageBus
from ..workflow.node import AgentNode, NodeResult
from ..workflow.state import WorkflowState


class FixerAgent(AgentNode):
    def __init__(self, reader: FileReaderTool, writer: FileWriterTool, work_dir: str):
        super().__init__("fixer", "修复 Agent", "按规则自动修复可修复问题，并给出其余问题的人工建议")
        self.reader = reader
        self.writer = writer
        self.work_dir = work_dir

    def handle(self, state: WorkflowState, bus: MessageBus) -> NodeResult:
        review_message = bus.latest("review.done")
        inbox = bus.inbox("fixer")
        state.log(
            "fixer",
            f"收到 {len(inbox)} 条总线消息，最近一次审查结论来自 "
            f"{review_message.sender if review_message else '无'}",
        )

        before_path = state.work_path
        before_source = self.reader.execute(before_path)
        current = before_source
        applied: list[dict] = []
        failed: list[dict] = []
        blocked: set[str] = set()
        attempts = 0
        limit = max(6, len(state.fixable_findings()) * 2 + 4)

        while attempts < limit:
            attempts += 1
            fresh = scan(current, before_path)
            candidates = [f for f in fresh if f.auto_fixable and f.rule_id not in blocked]
            if not candidates:
                break
            finding = candidates[0]
            new_source, note = apply_fix(current, finding)
            if new_source is None:
                failed.append(_record(finding, note))
                blocked.add(finding.rule_id)
                continue
            applied.append(_record(finding, note))
            current = new_source

        if not applied:
            summary = "没有可自动修复的问题" if not failed else "自动修复未生效，问题需人工处理"
            state.log("fixer", summary)
            bus.publish("fixer", "fix.done", to="reviewer", applied=0, failed=len(failed), round=state.round)
            # 没有产生新版本就不要回到审查节点，否则会在“修不动 → 复审 → 再修”之间空转
            return NodeResult(
                status="skipped",
                summary=f"{summary}，直接出报告",
                route="report",
                data={"failed": failed},
            )

        state.round += 1
        state.code = current
        path = f"{self.work_dir}/v{state.round}_{Path(before_path).name.split('_', 1)[-1]}"
        self.writer.write(path, current)
        state.work_path = path
        diff = unified_diff(before_source, current, before_path, path)
        remaining = scan(current, path)
        record = {
            "round": state.round,
            "from_path": before_path,
            "path": path,
            "applied": applied,
            "failed": failed,
            "diff": diff,
            "changed_lines": changed_line_count(diff),
            "remaining": [finding.to_dict() for finding in remaining],
        }
        state.artifacts.setdefault("fixes", []).append(record)
        state.artifacts.setdefault("versions", []).append(
            {"round": state.round, "path": path, "action": "fix", "lines": len(current.splitlines())}
        )
        state.log("fixer", f"第 {state.round} 轮修复：{len(applied)} 条已修复，{len(failed)} 条失败 → {path}")
        bus.publish(
            "fixer",
            "fix.done",
            to="reviewer",
            round=state.round,
            applied=len(applied),
            failed=len(failed),
            path=path,
            remaining=len(remaining),
        )
        return NodeResult(
            summary=f"第 {state.round} 轮修复：{len(applied)} 条已修复，{len(failed)} 条需人工",
            data={"path": path, "applied": len(applied), "failed": len(failed)},
        )


def _record(finding, note: str) -> dict:
    return {
        "rule_id": finding.rule_id,
        "severity": finding.severity,
        "line": finding.line,
        "message": finding.message,
        "note": note,
    }
