"""门控节点：Workflow 里的条件分支。

它不产生新内容，只回答一个问题——“还需要再修一轮吗？”
判断依据有两层，缺一不可：

* 结构化问题里是否还有达到阻塞阈值（默认 high）的项；
* 当前轮次是否已经用满 `max_rounds`（循环的退出条件）。

返回的 `route` 会被引擎优先采用，因此分支逻辑集中在节点里，图定义保持声明式。
"""

from __future__ import annotations

from ..workflow.bus import MessageBus
from ..workflow.node import AgentNode, NodeResult
from ..workflow.state import SEVERITY_LABEL, WorkflowState


class GateNode(AgentNode):
    def __init__(self, block_severity: str = "high"):
        super().__init__("gate", "门控（条件分支）", "根据阻塞问题与剩余轮次选择继续修复或直接出报告")
        self.block_severity = block_severity

    def handle(self, state: WorkflowState, bus: MessageBus) -> NodeResult:
        blocking = state.blocking_findings()
        threshold = SEVERITY_LABEL.get(state.block_severity, state.block_severity)

        if not blocking:
            decision, reason = "report", f"没有达到「{threshold}」级的问题，审查通过"
        elif state.round >= state.max_rounds:
            decision = "report"
            reason = (
                f"仍有 {len(blocking)} 个「{threshold}」级问题，但已用完 {state.max_rounds} 轮，"
                "转为输出报告并提示人工处理"
            )
        else:
            decision = "fixer"
            reason = f"仍有 {len(blocking)} 个「{threshold}」级问题，进入第 {state.round + 1} 轮修复"

        bus.publish(
            "gate",
            "gate.decision",
            decision=decision,
            reason=reason,
            blocking=len(blocking),
            round=state.round,
        )
        state.log("gate", reason)
        return NodeResult(summary=reason, route=decision, data={"decision": decision, "blocking": len(blocking)})
