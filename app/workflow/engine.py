"""Workflow 执行引擎。

职责很小但很关键：
1. 从 `Workflow.start` 开始，按图逐节点执行；
2. 节点返回 `route` 时优先走该路由，否则按顺序匹配条件边；
3. 节点失败时先按 `max_retries` 重试，再匹配 `when="error"` 的边；
4. 用步数上限和单节点访问次数上限兜住循环，防止死循环；
5. 记录每一步的耗时、状态和路由，产出 `RunResult`。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .bus import MessageBus
from .graph import Workflow
from .node import NodeResult
from .state import WorkflowState
from .trace import NullTrace, TraceRecorder

COMPLETED = "completed"
FAILED = "failed"
ABORTED = "aborted"


@dataclass
class StepRecord:
    step: int
    node: str
    title: str
    status: str
    duration_ms: int
    summary: str
    route: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "node": self.node,
            "title": self.title,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "summary": self.summary,
            "route": self.route,
            "error": self.error,
        }


@dataclass
class RunResult:
    status: str
    stop_reason: str
    steps: list[StepRecord] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    duration_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == COMPLETED

    def route_of(self, node: str) -> str | None:
        for step in self.steps:
            if step.node == node and step.route:
                return step.route
        return None

    def visits(self, node: str) -> int:
        return sum(1 for step in self.steps if step.node == node)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "stop_reason": self.stop_reason,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": round(self.duration_s, 3),
            "step_count": len(self.steps),
            "steps": [s.to_dict() for s in self.steps],
        }


class WorkflowEngine:
    def __init__(
        self,
        workflow: Workflow,
        bus: MessageBus | None = None,
        tracer: TraceRecorder | None = None,
        max_steps: int = 40,
        max_visits: int = 8,
    ):
        self.workflow = workflow
        self.bus = bus or MessageBus()
        self.tracer = tracer or NullTrace()
        self.max_steps = max_steps
        self.max_visits = max_visits
        self.workflow.validate()

    # --- 主循环 ---

    def run(self, state: WorkflowState) -> RunResult:
        started = datetime.now()
        self.tracer.event(
            "workflow.start",
            f"开始执行工作流「{self.workflow.name}」，入口节点 {self.workflow.start}",
            nodes=len(self.workflow.nodes),
            max_steps=self.max_steps,
            max_visits=self.max_visits,
        )

        steps: list[StepRecord] = []
        visits: dict[str, int] = {}
        current: str | None = self.workflow.start
        status = COMPLETED
        stop_reason = "工作流执行完成"

        while current is not None:
            if len(steps) >= self.max_steps:
                status, stop_reason = ABORTED, f"超过最大步数 {self.max_steps}，疑似死循环"
                self.tracer.event("workflow.abort", stop_reason)
                break

            node = self.workflow.nodes[current]
            visits[current] = visits.get(current, 0) + 1
            if visits[current] > self.max_visits:
                status = ABORTED
                stop_reason = f"节点 {current} 被访问超过 {self.max_visits} 次，已阻止死循环"
                self.tracer.event("workflow.abort", stop_reason, node=current)
                break

            result, duration_ms = self._execute(node, state, visits[current])
            if result.status == "failed":
                state.artifacts.setdefault("failed_node", current)
            next_node = self._resolve_next(current, state, result)
            steps.append(
                StepRecord(
                    step=len(steps) + 1,
                    node=current,
                    title=getattr(node, "title", current),
                    status=result.status,
                    duration_ms=duration_ms,
                    summary=result.summary,
                    route=next_node,
                    error=result.error,
                )
            )
            self.tracer.event(
                "node.done",
                f"{getattr(node, 'title', current)} → {next_node or '[结束]'}｜{result.summary}",
                node=current,
                status=result.status,
                duration_ms=duration_ms,
                route=next_node,
            )

            if next_node is None:
                if result.status == "failed":
                    status, stop_reason = FAILED, f"节点 {current} 失败且没有可用的错误分支"
                else:
                    status, stop_reason = COMPLETED, f"在节点 {current} 结束（无后续边）"
                break
            current = next_node

        finished = datetime.now()
        run = RunResult(
            status=status,
            stop_reason=stop_reason,
            steps=steps,
            started_at=started.isoformat(timespec="seconds"),
            finished_at=finished.isoformat(timespec="seconds"),
            duration_s=(finished - started).total_seconds(),
        )
        self.tracer.event(
            "workflow.end",
            f"工作流结束：{status}（{stop_reason}），共 {len(steps)} 步",
            status=status,
            steps=len(steps),
            duration_s=round(run.duration_s, 3),
        )
        state.artifacts["run"] = run.to_dict()
        return run

    # --- 内部 ---

    def _execute(self, node: Any, state: WorkflowState, visit: int) -> tuple[NodeResult, int]:
        retries = int(getattr(node, "max_retries", 0) or 0)
        total_ms = 0
        attempt = 0
        while True:
            self.tracer.event(
                "node.start",
                f"{getattr(node, 'title', node.name)} 开始执行（第 {visit} 次访问）",
                node=node.name,
                attempt=attempt + 1,
            )
            started = time.perf_counter()
            result = node.run(state, self.bus)
            duration_ms = int((time.perf_counter() - started) * 1000)
            total_ms += duration_ms
            if result.status != "failed" or attempt >= retries:
                return result, total_ms
            attempt += 1
            self.tracer.event(
                "node.retry",
                f"{getattr(node, 'title', node.name)} 执行失败，第 {attempt} 次重试：{result.error}",
                node=node.name,
            )

    def _resolve_next(self, current: str, state: WorkflowState, result: NodeResult) -> str | None:
        if result.route:
            if result.route not in self.workflow.nodes:
                raise ValueError(f"节点 {current} 路由到不存在的节点：{result.route}")
            self.tracer.event(
                "route.explicit",
                f"{current} 显式路由到 {result.route}",
                node=current,
                route=result.route,
            )
            return result.route
        return self.workflow.next_node(current, state, result)
