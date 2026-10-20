"""Workflow 图定义：节点 + 条件边。

支持的能力（对应作业“必须实现 / 推荐实现”）：

* 顺序执行：单出边、无条件的边；
* 条件分支：`condition` 回调 + `route` 显式跳转；
* 循环：边可以指回上游节点，由引擎的访问次数上限兜底；
* 错误分支：`when="error"` 的边只在节点失败时生效；
* 可扩展：新增节点只需 `add_node` + `add_edge`，不用改引擎。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from .node import AgentNode, Node, NodeResult
from .state import WorkflowState

Condition = Callable[[WorkflowState, NodeResult], bool]

VALID_WHEN = ("success", "error", "always")


class WorkflowError(ValueError):
    """工作流定义不合法。"""


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    label: str = ""
    when: str = "success"
    condition: Condition | None = None

    def matches(self, state: WorkflowState, result: NodeResult) -> bool:
        failed = result.status == "failed"
        if self.when == "always":
            pass
        elif self.when == "error":
            if not failed:
                return False
        elif failed:
            return False
        return True if self.condition is None else bool(self.condition(state, result))

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "source": self.source,
            "target": self.target,
            "label": self.label,
            "when": self.when,
            "conditional": self.condition is not None,
        }


class Workflow:
    def __init__(self, name: str, start: str, description: str = ""):
        self.name = name
        self.start = start
        self.description = description
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []

    # --- 组装 ---

    def add_node(self, node: Node) -> Node:
        if not getattr(node, "name", ""):
            raise WorkflowError("节点必须有 name")
        if node.name in self.nodes:
            raise WorkflowError(f"节点重名：{node.name}")
        self.nodes[node.name] = node
        return node

    def add_nodes(self, nodes: Iterable[Node]) -> None:
        for node in nodes:
            self.add_node(node)

    def add_edge(
        self,
        source: str,
        target: str,
        label: str = "",
        when: str = "success",
        condition: Condition | None = None,
    ) -> Edge:
        if when not in VALID_WHEN:
            raise WorkflowError(f"边 {source}->{target} 的 when 只能是 {VALID_WHEN}")
        edge = Edge(source=source, target=target, label=label, when=when, condition=condition)
        self.edges.append(edge)
        return edge

    # --- 查询 ---

    def outgoing(self, name: str) -> list[Edge]:
        return [edge for edge in self.edges if edge.source == name]

    def is_terminal(self, name: str) -> bool:
        return not self.outgoing(name)

    def terminal_nodes(self) -> list[str]:
        return [name for name in self.nodes if self.is_terminal(name)]

    def next_node(
        self,
        source: str,
        state: WorkflowState,
        result: NodeResult,
    ) -> str | None:
        """按定义顺序取第一条命中的边；都不命中则返回 None（结束）。"""
        for edge in self.outgoing(source):
            if edge.matches(state, result):
                return edge.target
        return None

    # --- 校验 ---

    def validate(self) -> None:
        problems = self.problems()
        if problems:
            raise WorkflowError("工作流定义有问题：" + "；".join(problems))

    def problems(self) -> list[str]:
        problems: list[str] = []
        if not self.nodes:
            problems.append("没有任何节点")
            return problems
        if self.start not in self.nodes:
            problems.append(f"起始节点不存在：{self.start}")
        for edge in self.edges:
            if edge.source not in self.nodes:
                problems.append(f"边引用了未知源节点：{edge.source}")
            if edge.target not in self.nodes:
                problems.append(f"边引用了未知目标节点：{edge.target}")
            if edge.when not in VALID_WHEN:
                problems.append(f"边 {edge.source}->{edge.target} 的 when 非法：{edge.when}")
        if not any(self.is_terminal(name) for name in self.nodes):
            problems.append("没有终止节点，流程会无限执行")
        if self.start in self.nodes:
            reachable = self.reachable()
            missing = sorted(set(self.nodes) - reachable)
            if missing:
                problems.append("存在不可达节点：" + "、".join(missing))
        for name, node in self.nodes.items():
            if not isinstance(node, AgentNode) and not hasattr(node, "run"):
                problems.append(f"节点缺少 run 方法：{name}")
        return problems

    def reachable(self) -> set[str]:
        seen: set[str] = set()
        stack = [self.start]
        while stack:
            current = stack.pop()
            if current in seen or current not in self.nodes:
                continue
            seen.add(current)
            stack.extend(edge.target for edge in self.outgoing(current))
        return seen

    # --- 导出 ---

    def to_mermaid(self) -> str:
        lines = ["flowchart TD"]
        for name, node in self.nodes.items():
            title = getattr(node, "title", name)
            lines.append(f'    {_mid(name)}["{title}"]')
        for edge in self.edges:
            label = edge.label or ("失败" if edge.when == "error" else "")
            arrow = f"-->|{label}|" if label else "-->"
            lines.append(f"    {_mid(edge.source)} {arrow} {_mid(edge.target)}")
        return "\n".join(lines)

    def to_text(self) -> str:
        """给命令行看的邻接表，顺序稳定便于测试。"""
        lines = [f"工作流：{self.name}（入口 {self.start}）"]
        for name, node in self.nodes.items():
            title = getattr(node, "title", name)
            edges = self.outgoing(name)
            if not edges:
                lines.append(f"  {name}({title}) → [结束]")
                continue
            for edge in edges:
                label = edge.label or ("失败" if edge.when == "error" else "")
                suffix = f" [{label}]" if label else ""
                lines.append(f"  {name}({title}) → {edge.target}{suffix}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "start": self.start,
            "description": self.description,
            "nodes": [
                {"name": n, "title": getattr(node, "title", n), "description": getattr(node, "description", "")}
                for n, node in self.nodes.items()
            ],
            "edges": [edge.to_dict() for edge in self.edges],
            "mermaid": self.to_mermaid(),
        }


def _mid(name: str) -> str:
    """mermaid 节点 id 用安全字符。"""
    return "".join(ch if ch.isalnum() else "_" for ch in name)
