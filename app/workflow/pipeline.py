"""把节点接线成工作流，并负责一次运行的准备与收尾。

`build_workflow` 是唯一描述“谁接谁”的地方：想加 Agent 或改流程，只需要改这里的边，
引擎、节点、报告都不需要动（这是作业“可扩展性”得分的落点）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..agent import ToolRegistry
from ..agents import (
    CoderAgent,
    FixerAgent,
    GateNode,
    LLMReviewNode,
    ReporterAgent,
    ReviewerAgent,
    StaticReviewNode,
)
from ..config import Settings
from ..tools.file_reader import FileReaderTool
from ..tools.file_writer import FileWriterTool
from ..tools.python_syntax import PythonSyntaxTool
from .bus import MessageBus
from .engine import WorkflowEngine
from .graph import Workflow
from .state import WorkflowState
from .trace import TraceRecorder

WORKFLOW_NAME = "code-review-workflow"


@dataclass
class WorkflowRun:
    workflow: Workflow
    state: WorkflowState
    bus: MessageBus
    result: RunResult
    run_dir: Path
    run_rel: str
    report_path: Path | None

    @property
    def ok(self) -> bool:
        return self.result.status == "completed" and not self.state.artifacts.get("failed_node")

    def summary(self) -> str:
        counts = self.state.count_by_severity()
        applied = sum(len(item.get("applied", [])) for item in self.state.artifacts.get("fixes", []))
        return (
            f"状态 {self.result.status}｜{len(self.result.steps)} 步｜"
            f"审查轮次 {self.state.round}/{self.state.max_rounds}｜"
            f"遗留问题 {len(self.state.findings)}（高 {counts['high']}）｜自动修复 {applied} 条"
        )


def build_workflow(
    settings: Settings,
    llm: Any | None,
    run_rel: str,
    block_severity: str = "high",
) -> tuple[Workflow, MessageBus]:
    reader = FileReaderTool(settings.workspace_root, settings.max_file_bytes)
    writer = FileWriterTool(settings.workspace_root, settings.max_file_bytes)
    # 项目 1 的工具注册表原样复用给审查 Agent
    registry = ToolRegistry(
        {
            "read_file": reader,
            "check_python_syntax": PythonSyntaxTool(reader),
        }
    )
    work_dir = f"{run_rel}/work"

    coder = CoderAgent(reader, writer, work_dir, llm=llm)
    reviewer = ReviewerAgent(
        StaticReviewNode(reader),
        LLMReviewNode(llm, registry),
    )
    gate = GateNode(block_severity)
    fixer = FixerAgent(reader, writer, work_dir)
    reporter = ReporterAgent(writer, run_rel)

    workflow = Workflow(
        WORKFLOW_NAME,
        start="coder",
        description="编码 → 审查 → 门控 → 修复（循环）→ 报告",
    )
    workflow.add_nodes([coder, reviewer, gate, fixer, reporter])
    workflow.add_edge("coder", "review", label="编码完成")
    workflow.add_edge("coder", "report", label="编码失败", when="error")
    workflow.add_edge("review", "gate", label="审查完成")
    workflow.add_edge("review", "report", label="审查失败", when="error")
    # 门控节点用显式 route 决定走哪条分支，这两条边同时作为图定义与兜底路径
    workflow.add_edge("gate", "fixer", label="需要修复")
    workflow.add_edge("gate", "report", label="通过 / 轮次用尽")
    workflow.add_edge("fixer", "review", label="修复完成")
    workflow.add_edge("fixer", "report", label="修复失败", when="error")

    return workflow, MessageBus()


def create_run_dir(workspace_root: Path, run_root: str = "runs") -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = Path(workspace_root) / run_root
    candidate = base / stamp
    suffix = 1
    while candidate.exists():
        suffix += 1
        candidate = base / f"{stamp}-{suffix}"
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def run_workflow(
    settings: Settings,
    source_path: str = "",
    requirement: str = "",
    max_rounds: int = 2,
    block_severity: str = "high",
    llm: Any | None = None,
    echo: bool = True,
    run_root: str = "runs",
) -> WorkflowRun:
    if max_rounds < 1:
        raise ValueError("max_rounds 必须大于 0")
    run_dir = create_run_dir(settings.workspace_root, run_root)
    run_rel = run_dir.relative_to(settings.workspace_root).as_posix()

    workflow, bus = build_workflow(settings, llm, run_rel, block_severity)
    state = WorkflowState(
        requirement=requirement,
        source_path=source_path,
        block_severity=block_severity,
        max_rounds=max_rounds,
    )
    state.artifacts["workflow"] = workflow.to_dict()
    tracer = TraceRecorder(run_dir, echo=echo)
    engine = WorkflowEngine(workflow, bus, tracer)
    try:
        result = engine.run(state)
    except BaseException:
        tracer.event("workflow.error", "工作流执行异常中断", run_dir=run_rel)
        finalize(run_dir, workflow, state, bus, tracer)
        raise
    report_path = finalize(run_dir, workflow, state, bus, tracer)
    return WorkflowRun(
        workflow=workflow,
        state=state,
        bus=bus,
        result=result,
        run_dir=run_dir,
        run_rel=run_rel,
        report_path=report_path,
    )


def finalize(
    run_dir: Path,
    workflow: Workflow,
    state: WorkflowState,
    bus: MessageBus,
    tracer: TraceRecorder,
) -> Path:
    """落盘所有产物：状态、消息、拓扑、报告。"""
    result = state.artifacts.get("run", {})
    payload = {
        "workflow": workflow.to_dict(),
        "run": result,
        "state": state.to_dict(),
        "messages": bus.to_dict(),
    }
    (run_dir / "state.json").write_text(
        json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "messages.json").write_text(
        json.dumps(bus.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "run.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "workflow.mmd").write_text(workflow.to_mermaid(), encoding="utf-8")

    report = run_dir / "report.md"
    body = report.read_text(encoding="utf-8").rstrip() if report.exists() else "# 代码审查 Workflow 报告\n"
    report.write_text(body + "\n\n" + render_run_section(result, state, bus, tracer, body), encoding="utf-8")
    tracer.close()
    return report


def render_run_section(
    result: dict[str, Any],
    state: WorkflowState,
    bus: MessageBus,
    tracer: TraceRecorder,
    report_body: str = "",
) -> str:
    # 报告正文的章节数由报告 Agent 决定，这里接着编号，避免出现跳号
    section_no = sum(1 for line in report_body.splitlines() if line.startswith("## ")) + 1
    lines = [f"## {section_no}. 执行记录", ""]
    lines.append("| 步 | 节点 | 状态 | 耗时(ms) | 说明 | 下一步 |")
    lines.append("|----|------|------|----------|------|--------|")
    for step in result.get("steps", []):
        summary = str(step.get("summary", "")).replace("|", "\\|")
        lines.append(
            f"| {step.get('step')} | {step.get('title')} | {step.get('status')} | "
            f"{step.get('duration_ms')} | {summary} | {step.get('route') or '-'} |"
        )
    lines.append("")
    lines.append("### 运行结论")
    lines.append("")
    lines.append(f"- 状态：**{result.get('status', 'unknown')}**")
    lines.append(f"- 停止原因：{result.get('stop_reason', '-')}")
    lines.append(f"- 总耗时：{round(float(result.get('duration_s', 0.0)), 2)} 秒")
    lines.append(f"- 步骤数：{len(result.get('steps', []))}")
    lines.append(f"- 总线消息：{len(bus.messages())} 条")
    lines.append(f"- 追踪事件：{len(tracer.events)} 条（见 `trace.jsonl`）")
    lines.append("")
    return "\n".join(lines)
