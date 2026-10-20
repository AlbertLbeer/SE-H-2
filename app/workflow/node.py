"""Workflow 节点抽象。

一个节点就是一个可执行的 Agent 步骤。节点只负责：
1. 读取 `WorkflowState`；
2. 通过 `MessageBus` 收发消息；
3. 返回 `NodeResult`（状态 + 可选路由）。

节点不关心“自己下一步是谁”，路由由 `graph` 中的条件边决定，
因此新增 Agent 或调整流程都不需要改已有节点的代码。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .bus import MessageBus
from .state import WorkflowState

OK = "ok"
FAILED = "failed"
SKIPPED = "skipped"


@dataclass
class NodeResult:
    status: str = OK
    summary: str = ""
    route: str | None = None
    error: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == OK

    @property
    def failed(self) -> bool:
        return self.status == FAILED

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "summary": self.summary,
            "route": self.route,
            "error": self.error,
            "data": self.data,
        }


@runtime_checkable
class Node(Protocol):
    name: str
    title: str

    def run(self, state: WorkflowState, bus: MessageBus) -> NodeResult: ...


class AgentNode:
    """Agent 节点的模板基类：统一异常处理，子类只实现 `handle`。"""

    name: str = "node"
    title: str = ""
    description: str = ""
    # 失败后允许引擎自动重试的次数
    max_retries: int = 1

    def __init__(self, name: str | None = None, title: str | None = None, description: str | None = None):
        if name:
            self.name = name
        if title:
            self.title = title
        if description is not None:
            self.description = description
        if not self.title:
            self.title = self.name

    # 子类实现
    def handle(self, state: WorkflowState, bus: MessageBus) -> NodeResult:
        raise NotImplementedError

    def run(self, state: WorkflowState, bus: MessageBus) -> NodeResult:
        try:
            result = self.handle(state, bus)
        except Exception as exc:  # 节点异常转为失败结果，交给引擎的错误分支处理
            return NodeResult(
                status=FAILED,
                summary=f"{self.title}执行失败：{exc}",
                error=f"{type(exc).__name__}: {exc}",
            )
        if result.status not in (OK, FAILED, SKIPPED):
            return NodeResult(status=FAILED, summary=f"{self.title}返回了非法状态：{result.status}")
        return result

    def describe(self) -> dict[str, str]:
        return {"name": self.name, "title": self.title, "description": self.description}

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<{type(self).__name__} {self.name}>"


class ParallelNode(AgentNode):
    """把多个子节点并行执行，然后合并结果。

    并行是作业里的“推荐实现”。子节点拿到的是状态的副本，
    合并时按子节点声明顺序汇总，保证结果可复现（不依赖线程完成顺序）。
    """

    def __init__(
        self,
        name: str,
        title: str,
        children: list[Node],
        description: str = "",
        inherit_findings: bool = False,
    ):
        super().__init__(name=name, title=title, description=description)
        if not children:
            raise ValueError("ParallelNode 至少需要一个子节点")
        self.children = list(children)
        # 默认让子节点从“空问题列表”开始：并行汇总是各子节点自己发现的问题，
        # 而不是把上一轮的问题再带一遍（否则「修复后重审」永远看不到问题清零）。
        self.inherit_findings = inherit_findings

    def handle(self, state: WorkflowState, bus: MessageBus) -> NodeResult:
        forks = [state.fork() for _ in self.children]
        if not self.inherit_findings:
            for fork in forks:
                fork.findings = []
        with ThreadPoolExecutor(max_workers=len(self.children)) as pool:
            futures = [
                pool.submit(child.run, fork, bus)
                for child, fork in zip(self.children, forks)
            ]
            results: list[NodeResult] = []
            for child, future in zip(self.children, futures):
                try:
                    results.append(future.result())
                except Exception as exc:  # 兜底：子节点线程内的意外异常
                    results.append(
                        NodeResult(status=FAILED, summary=f"{child.title}执行失败：{exc}", error=str(exc))
                    )

        merged_findings = []
        for fork in forks:
            merged_findings.extend(fork.findings)
        state.replace_findings(merged_findings)
        for fork in forks:
            # 子节点产生的工件、事件按声明顺序合并
            for key, value in fork.artifacts.items():
                state.artifacts[key] = value
            state.events.extend(fork.events)
        if any(fork.llm_review for fork in forks):
            state.llm_review = next(fork.llm_review for fork in forks if fork.llm_review)

        summaries = [r.summary for r in results if r.summary]
        failed = [r for r in results if r.failed]
        if failed:
            status = FAILED
        elif all(r.status == SKIPPED for r in results):
            status = SKIPPED
        else:
            status = OK
        return NodeResult(
            status=status,
            summary="；".join(summaries),
            error="；".join(filter(None, (r.error for r in failed))) or None,
        )
