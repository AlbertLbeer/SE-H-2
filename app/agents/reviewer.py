"""审查 Agent。

由两个子节点 **并行** 组成：

* `StaticReviewNode`：规则化 AST 扫描，产出结构化 `Finding`，可离线运行；
* `LLMReviewNode`：**复用项目 1 的 `CodeReviewAgent`**（带 read_file / check_python_syntax
  工具调用的 Agent 循环），产出自然语言审查意见。

并行是作业里的“推荐实现”；结构化结论仍以规则扫描为准，保证门控判断可复现。
两个子节点都把结论写进共享状态，并通过消息总线广播各自的结果。
"""

from __future__ import annotations

from typing import Any

from ..agent import CodeReviewAgent, ToolRegistry
from ..tools.file_reader import FileReaderTool
from ..tools.static_rules import scan
from ..workflow.bus import MessageBus
from ..workflow.node import AgentNode, NodeResult, ParallelNode
from ..workflow.state import WorkflowState

REVIEW_REQUEST = "请审查这个 Python 文件，重点检查语法、潜在 Bug、安全问题和可维护性。"


class StaticReviewNode(AgentNode):
    def __init__(self, reader: FileReaderTool):
        super().__init__("review.static", "规则审查", "使用 AST 规则扫描代码，产出结构化问题")
        self.reader = reader
        self.max_retries = 0

    def handle(self, state: WorkflowState, bus: MessageBus) -> NodeResult:
        # 从磁盘重新读取，确保审查的是修复后的真实文件
        source = self.reader.execute(state.work_path)
        findings = scan(source, state.work_path)
        state.replace_findings(findings)
        rules = sorted({finding.rule_id for finding in findings})
        state.log("reviewer", f"规则扫描完成：{len(findings)} 个问题 {rules}")
        bus.publish("reviewer", "review.static.done", to="reviewer", count=len(findings), rules=rules)
        return NodeResult(summary=f"规则扫描发现 {len(findings)} 个问题")


class LLMReviewNode(AgentNode):
    def __init__(self, llm: Any | None, registry: ToolRegistry):
        super().__init__("review.llm", "模型审查", "复用项目 1 的代码审查 Agent 生成自然语言意见")
        self.llm = llm
        self.registry = registry
        self.max_retries = 0

    def handle(self, state: WorkflowState, bus: MessageBus) -> NodeResult:
        if self.llm is None:
            return NodeResult(status="skipped", summary="未配置 LLM，跳过模型审查（规则审查仍然生效）")
        try:
            # 复用项目 1 的 Agent：它自己会调用 read_file / check_python_syntax
            agent = CodeReviewAgent(self.llm, self.registry)
            text = agent.run(f"{REVIEW_REQUEST}\n文件路径：{state.work_path}")
        except Exception as exc:  # 模型不可用时降级，不阻塞整条流程
            state.log("reviewer", f"模型审查不可用：{exc}")
            bus.publish("reviewer", "review.llm.error", to="reviewer", error=str(exc))
            return NodeResult(status="skipped", summary=f"模型审查不可用：{exc}")
        state.llm_review = text
        bus.publish("reviewer", "review.llm.done", to="reviewer", chars=len(text))
        return NodeResult(summary=f"模型审查返回 {len(text)} 字")


class ReviewerAgent(ParallelNode):
    def __init__(self, static_node: StaticReviewNode, llm_node: LLMReviewNode):
        super().__init__(
            "review",
            "审查 Agent",
            [static_node, llm_node],
            "并行执行规则审查与模型审查，合并为一份结构化结论",
        )

    def handle(self, state: WorkflowState, bus: MessageBus) -> NodeResult:
        result = super().handle(state, bus)
        counts = state.count_by_severity()
        state.record_round(code_path=state.work_path, has_llm_review=bool(state.llm_review))
        state.log(
            "reviewer",
            f"第 {state.round} 轮审查完成：高 {counts['high']} / 中 {counts['medium']} / 低 {counts['low']}",
        )
        # 把结论通过总线定向发给门控节点，门控不需要重新计算
        bus.publish(
            "reviewer",
            "review.done",
            to="gate",
            round=state.round,
            counts=counts,
            findings=[finding.to_dict() for finding in state.findings],
        )
        summary = f"第 {state.round} 轮审查：{len(state.findings)} 个问题（高 {counts['high']}）"
        if state.llm_review:
            summary += "，含模型审查意见"
        result.summary = summary
        return result
