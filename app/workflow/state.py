"""Workflow 共享状态与结构化数据。

本模块只描述“数据”，不包含任何执行逻辑：
`WorkflowState` 是整条 Workflow 唯一的事实来源，所有 Agent 节点读写同一份状态，
这就是作业要求里的“状态管理 / 状态共享”。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# 严重程度由低到高，用于比较“是否达到阻塞阈值”。
SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3}
SEVERITY_LABEL = {"low": "低", "medium": "中", "high": "高"}


def severity_rank(value: str) -> int:
    return SEVERITY_ORDER.get(value, 0)


@dataclass
class Finding:
    """一条结构化的审查结论。

    与“给大模型看的自然语言报告”不同，这里的字段是给程序用的：
    门控节点按 `severity` 判断是否继续修复，修复 Agent 按 `rule_id` 选择自动修复器，
    报告节点再把它渲染成表格。
    """

    rule_id: str
    severity: str
    message: str
    line: int | None = None
    column: int | None = None
    suggestion: str = ""
    auto_fixable: bool = False
    source: str = "static"
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def severity_label(self) -> str:
        return SEVERITY_LABEL.get(self.severity, self.severity)

    @property
    def location(self) -> str:
        if self.line is None:
            return "-"
        if self.column is None:
            return f"第 {self.line} 行"
        return f"第 {self.line} 行第 {self.column} 列"

    def key(self) -> tuple:
        # 同一轮里去重用，避免并行分析把同一个问题报两次。
        return (self.rule_id, self.line, self.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "severity_label": self.severity_label,
            "message": self.message,
            "line": self.line,
            "column": self.column,
            "suggestion": self.suggestion,
            "auto_fixable": self.auto_fixable,
            "source": self.source,
            "meta": self.meta,
        }


@dataclass
class Event:
    """状态变更记录，用于执行日志与调试回放。"""

    ts: str
    node: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"ts": self.ts, "node": self.node, "message": self.message, "data": self.data}


def now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class WorkflowState:
    """所有 Agent 共享的可变状态。"""

    # 输入
    requirement: str = ""
    source_path: str = ""
    block_severity: str = "high"
    max_rounds: int = 2

    # 代码版本：code 是当前版本，original_code 是进入流程时的版本
    code: str = ""
    original_code: str = ""
    work_path: str = ""

    # 审查结果：每一轮都会被整体替换，避免旧问题干扰新判断
    findings: list[Finding] = field(default_factory=list)
    llm_review: str = ""

    round: int = 1
    history: list[dict[str, Any]] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)

    # --- 查询 ---

    def blocking_findings(self) -> list[Finding]:
        threshold = severity_rank(self.block_severity)
        return [f for f in self.findings if severity_rank(f.severity) >= threshold]

    def fixable_findings(self) -> list[Finding]:
        return [f for f in self.findings if f.auto_fixable]

    def unfixable_blocking(self) -> list[Finding]:
        return [f for f in self.blocking_findings() if not f.auto_fixable]

    def count_by_severity(self) -> dict[str, int]:
        counts = {"high": 0, "medium": 0, "low": 0}
        for finding in self.findings:
            counts[finding.severity] = counts.get(finding.severity, 0) + 1
        return counts

    # --- 变更 ---

    def log(self, node: str, message: str, **data: Any) -> None:
        self.events.append(Event(ts=now_text(), node=node, message=message, data=data))

    def replace_findings(self, findings: list[Finding]) -> None:
        self.findings = dedupe_findings(findings)

    def record_round(self, **data: Any) -> None:
        """记录一轮审查结果，供报告节点回放。"""
        record: dict[str, Any] = {
            "round": self.round,
            "findings": [f.to_dict() for f in self.findings],
            "counts": self.count_by_severity(),
        }
        record.update(data)
        self.history.append(record)

    def fork(self) -> "WorkflowState":
        """并行节点使用的浅拷贝：容器复制一份，避免多线程互相覆盖。"""
        clone = copy.copy(self)
        clone.findings = list(self.findings)
        clone.history = list(self.history)
        clone.artifacts = dict(self.artifacts)
        clone.events = list(self.events)
        return clone

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement": self.requirement,
            "source_path": self.source_path,
            "work_path": self.work_path,
            "block_severity": self.block_severity,
            "max_rounds": self.max_rounds,
            "round": self.round,
            "counts": self.count_by_severity(),
            "findings": [f.to_dict() for f in self.findings],
            "history": self.history,
            "artifacts": self.artifacts,
            "events": [e.to_dict() for e in self.events],
            "code_lines": len(self.code.splitlines()),
            "original_lines": len(self.original_code.splitlines()),
        }


def dedupe_findings(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple] = set()
    result: list[Finding] = []
    for finding in sorted(findings, key=lambda f: (f.line or 0, f.rule_id)):
        key = finding.key()
        if key in seen:
            continue
        seen.add(key)
        result.append(finding)
    return result
