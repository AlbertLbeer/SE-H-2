"""代码审查 Workflow（编码 → 审查 → 修复）。

注意：这里只做“核心层”的即时导入。`pipeline` 依赖 `app.agents`，而 `app.agents`
又依赖 `app.tools`，`app.tools` 反过来要读 `Finding`；如果在包初始化时就导入 pipeline，
会形成循环导入。因此 pipeline 的导出改成按需加载（PEP 562）。
"""

from .bus import Message, MessageBus
from .engine import ABORTED, COMPLETED, FAILED, RunResult, StepRecord, WorkflowEngine
from .graph import Edge, Workflow, WorkflowError
from .node import AgentNode, NodeResult, ParallelNode
from .state import Event, Finding, WorkflowState
from .trace import NullTrace, TraceRecorder

__all__ = [
    "ABORTED",
    "COMPLETED",
    "FAILED",
    "AgentNode",
    "Edge",
    "Event",
    "Finding",
    "Message",
    "MessageBus",
    "NodeResult",
    "NullTrace",
    "ParallelNode",
    "RunResult",
    "StepRecord",
    "TraceRecorder",
    "Workflow",
    "WorkflowEngine",
    "WorkflowError",
    "WorkflowRun",
    "WorkflowState",
    "build_workflow",
    "create_run_dir",
    "run_workflow",
]

_LAZY = {"WorkflowRun", "build_workflow", "create_run_dir", "run_workflow"}


def __getattr__(name: str):
    if name in _LAZY:
        from . import pipeline

        return getattr(pipeline, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
