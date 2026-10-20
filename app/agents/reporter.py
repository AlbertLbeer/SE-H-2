"""报告 Agent：把整条流程的结果整理成 Markdown。

报告是作业的“文档”得分点，所以内容按评审维度组织：
流程拓扑、每轮审查、每次修复的 diff、遗留问题、最终代码。
章节编号按实际出现的章节自动生成，避免“模型审查”章节缺失时编号跳号。
"""

from __future__ import annotations

from ..tools.file_writer import FileWriterTool
from ..tools.static_rules import rule_title
from ..workflow.bus import MessageBus
from ..workflow.node import AgentNode, NodeResult
from ..workflow.state import SEVERITY_LABEL, WorkflowState


class ReporterAgent(AgentNode):
    def __init__(self, writer: FileWriterTool, run_rel: str):
        super().__init__("report", "报告 Agent", "汇总审查与修复结果，生成 Markdown 报告")
        self.writer = writer
        self.run_rel = run_rel

    def handle(self, state: WorkflowState, bus: MessageBus) -> NodeResult:
        markdown = render_report(state)
        path = self.writer.write(f"{self.run_rel}/report.md", markdown)
        state.artifacts["report_path"] = path
        state.log("reporter", f"报告已写入 {path}")
        bus.publish("report", "report.written", to="user", path=path, chars=len(markdown))
        return NodeResult(summary=f"报告已生成：{path}", data={"path": path})


def render_report(state: WorkflowState) -> str:
    workflow = state.artifacts.get("workflow", {})
    fixes = state.artifacts.get("fixes", [])
    versions = state.artifacts.get("versions", [])
    counts = state.count_by_severity()
    applied_total = sum(len(item.get("applied", [])) for item in fixes)

    sections: list[tuple[str, list[str]]] = []

    # 概览
    sections.append((
        "概览",
        [
            "| 指标 | 数值 |",
            "|------|------|",
            f"| 审查轮次 | {len([h for h in state.history if 'round' in h])} |",
            f"| 问题总数 | {len(state.findings)}（高 {counts['high']} / 中 {counts['medium']} / 低 {counts['low']}） |",
            f"| 自动修复条数 | {applied_total} |",
            f"| 代码版本数 | {len(versions)} |",
        ],
    ))

    # 拓扑
    sections.append((
        "Workflow 拓扑",
        ["```mermaid", workflow.get("mermaid", "flowchart TD"), "```"],
    ))

    # 过程
    process: list[str] = []
    if not state.history:
        process.append("> 本次运行没有产生审查记录。")
        process.append("")
    for record in state.history:
        round_no = record.get("round", "?")
        findings = record.get("findings", [])
        process.append(f"### 第 {round_no} 轮审查（{len(findings)} 个问题）")
        process.append("")
        if findings:
            process.extend(_findings_table(findings))
        else:
            process.append("未发现问题。")
        process.append("")
        for fix in _fixes_for_round(fixes, round_no + 1):
            process.append(
                f"**第 {fix['round']} 轮修复**：{len(fix.get('applied', []))} 条已修复，"
                f"{len(fix.get('failed', []))} 条需人工处理；版本 `{fix.get('path', '-')}`"
            )
            process.append("")
            if fix.get("diff"):
                process.append("```diff")
                process.append(fix["diff"].rstrip("\n"))
                process.append("```")
                process.append("")
    sections.append(("审查与修复过程", process))

    # 遗留问题
    leftover: list[str] = []
    blocking = state.blocking_findings()
    if not state.findings:
        leftover.append("无遗留问题。")
    else:
        if blocking:
            leftover.append(f"仍有 {len(blocking)} 个达到阻塞阈值的问题：")
            leftover.append("")
        leftover.extend(_findings_table([finding.to_dict() for finding in state.findings]))
    sections.append(("遗留问题（需人工处理）", leftover))

    # 模型审查意见
    if state.llm_review:
        sections.append(("模型审查意见（复用项目 1 Agent 输出）", [state.llm_review.strip()]))

    # 最终代码
    sections.append(("最终代码", [f"```python\n{state.code.rstrip()}\n```"]))

    lines: list[str] = ["# 代码审查 Workflow 报告", ""]
    lines.append(f"- 工作流：`{workflow.get('name', 'code-review-workflow')}`")
    lines.append(f"- 输入：`{state.source_path or '（由需求生成）'}`")
    lines.append(f"- 当前代码版本：`{state.work_path or '-'}`")
    lines.append(
        f"- 轮次：{state.round} / {state.max_rounds}"
        f"（阻塞阈值：{SEVERITY_LABEL.get(state.block_severity, state.block_severity)}）"
    )
    lines.append("")
    for index, (title, body) in enumerate(sections, 1):
        lines.append(f"## {index}. {title}")
        lines.append("")
        lines.extend(body)
        lines.append("")
    return "\n".join(lines)


def _fixes_for_round(fixes: list[dict], round_no: int) -> list[dict]:
    return [item for item in fixes if item.get("round") == round_no]


def _findings_table(findings: list[dict]) -> list[str]:
    lines = ["| 级别 | 规则 | 位置 | 问题 | 建议 |", "|------|------|------|------|------|"]
    for finding in findings:
        severity = SEVERITY_LABEL.get(finding.get("severity", ""), finding.get("severity", ""))
        rule_id = finding.get("rule_id", "")
        message = _cell(finding.get("message", ""))
        suggestion = _cell(finding.get("suggestion", ""))
        fixable = "（可自动修复）" if finding.get("auto_fixable") else ""
        lines.append(
            f"| {severity} | {rule_id} {rule_title(rule_id)} | {_location(finding)} | "
            f"{message}{fixable} | {suggestion} |"
        )
    return lines


def _location(finding: dict) -> str:
    line = finding.get("line")
    column = finding.get("column")
    if line is None:
        return "-"
    if column is None:
        return f"第 {line} 行"
    return f"第 {line} 行第 {column} 列"


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")
