import unittest

from app.workflow.engine import WorkflowEngine
from app.workflow.graph import Workflow, WorkflowError
from app.workflow.node import AgentNode, NodeResult
from app.workflow.state import WorkflowState


class ScriptedNode(AgentNode):
    """按脚本返回结果的测试节点。"""

    def __init__(self, name, results=None, recorder=None):
        super().__init__(name, name, "测试节点")
        self.results = list(results or [])
        self.calls = 0
        self.recorder = recorder

    def handle(self, state, bus):
        self.calls += 1
        if self.recorder is not None:
            self.recorder.append(self.name)
        if self.results:
            result = self.results[min(self.calls - 1, len(self.results) - 1)]
        else:
            result = NodeResult(summary=f"{self.name} 完成")
        return result


class WorkflowEngineTests(unittest.TestCase):
    def test_sequential_execution(self):
        trace = []
        first = ScriptedNode("first", recorder=trace)
        second = ScriptedNode("second", recorder=trace)
        workflow = Workflow("seq", start="first")
        workflow.add_nodes([first, second])
        workflow.add_edge("first", "second")

        result = WorkflowEngine(workflow).run(WorkflowState())

        self.assertEqual(result.status, "completed")
        self.assertEqual(trace, ["first", "second"])
        self.assertEqual(len(result.steps), 2)
        self.assertEqual(result.steps[1].route, None)

    def test_explicit_route_takes_priority(self):
        branch = ScriptedNode(
            "branch",
            results=[NodeResult(summary="去左边", route="left")],
        )
        left = ScriptedNode("left", recorder=[])
        right = ScriptedNode("right", recorder=[])
        workflow = Workflow("branch", start="branch")
        workflow.add_nodes([branch, left, right])
        workflow.add_edge("branch", "right")
        workflow.add_edge("branch", "left")

        result = WorkflowEngine(workflow).run(WorkflowState())

        self.assertEqual([step.node for step in result.steps], ["branch", "left"])

    def test_condition_edge_selects_branch(self):
        class Gate(AgentNode):
            def handle(self, state, bus):
                return NodeResult(summary="判断")

        gate = Gate("gate", "门控")
        left = ScriptedNode("left")
        right = ScriptedNode("right")
        workflow = Workflow("cond", start="gate")
        workflow.add_nodes([gate, left, right])
        workflow.add_edge("gate", "left", label="命中", condition=lambda state, result: True)
        workflow.add_edge("gate", "right", label="兜底")

        result = WorkflowEngine(workflow).run(WorkflowState())

        self.assertEqual(result.steps[-1].node, "left")

    def test_error_edge_handles_failed_node(self):
        broken = ScriptedNode("broken", results=[NodeResult(status="failed", summary="炸了", error="boom")])
        fallback = ScriptedNode("fallback")
        workflow = Workflow("err", start="broken")
        workflow.add_nodes([broken, fallback])
        workflow.add_edge("broken", "fallback", when="error")

        state = WorkflowState()
        result = WorkflowEngine(workflow).run(state)

        self.assertEqual(result.status, "completed")
        self.assertEqual(state.artifacts["failed_node"], "broken")
        self.assertEqual([step.node for step in result.steps], ["broken", "fallback"])

    def test_failed_node_without_error_edge_stops_run(self):
        broken = ScriptedNode("broken", results=[NodeResult(status="failed", summary="炸了")])
        workflow = Workflow("err", start="broken")
        workflow.add_node(broken)

        result = WorkflowEngine(workflow).run(WorkflowState())

        self.assertEqual(result.status, "failed")

    def test_retry_then_success(self):
        flaky = ScriptedNode(
            "flaky",
            results=[
                NodeResult(status="failed", summary="第一次失败", error="network"),
                NodeResult(summary="第二次成功"),
            ],
        )
        flaky.max_retries = 1
        workflow = Workflow("retry", start="flaky")
        workflow.add_node(flaky)

        result = WorkflowEngine(workflow).run(WorkflowState())

        self.assertEqual(result.status, "completed")
        self.assertEqual(flaky.calls, 2)

    def test_loop_guard_stops_infinite_cycle(self):
        a = ScriptedNode("a")
        b = ScriptedNode("b")
        end = ScriptedNode("end")
        workflow = Workflow("loop", start="a")
        workflow.add_nodes([a, b, end])
        workflow.add_edge("a", "b")
        workflow.add_edge("b", "a")
        # 终止节点只为让图定义合法；b 的第一条边永远命中，实际会一直循环
        workflow.add_edge("b", "end", when="always", condition=lambda state, result: False)

        result = WorkflowEngine(workflow, max_visits=2).run(WorkflowState())

        self.assertEqual(result.status, "aborted")
        self.assertIn("死循环", result.stop_reason)

    def test_max_steps_guard(self):
        a = ScriptedNode("a")
        end = ScriptedNode("end")
        workflow = Workflow("loop", start="a")
        workflow.add_nodes([a, end])
        workflow.add_edge("a", "a")
        workflow.add_edge("a", "end", when="always", condition=lambda state, result: False)

        result = WorkflowEngine(workflow, max_steps=3, max_visits=10).run(WorkflowState())

        self.assertEqual(result.status, "aborted")
        self.assertLessEqual(len(result.steps), 3)

    def test_validation_rejects_unknown_edge_target(self):
        workflow = Workflow("bad", start="a")
        workflow.add_node(ScriptedNode("a"))
        workflow.add_edge("a", "missing")

        with self.assertRaises(WorkflowError):
            WorkflowEngine(workflow)

    def test_validation_rejects_unreachable_node(self):
        workflow = Workflow("bad", start="a")
        workflow.add_nodes([ScriptedNode("a"), ScriptedNode("b")])
        workflow.add_edge("a", "a")
        # b 没有任何入边

        with self.assertRaises(WorkflowError):
            WorkflowEngine(workflow)

    def test_mermaid_contains_node_and_edge_labels(self):
        workflow = Workflow("g", start="a")
        workflow.add_nodes([ScriptedNode("a"), ScriptedNode("b")])
        workflow.add_edge("a", "b", label="继续")
        mermaid = workflow.to_mermaid()

        self.assertIn("flowchart TD", mermaid)
        self.assertIn("继续", mermaid)
        self.assertEqual(workflow.problems(), [])


if __name__ == "__main__":
    unittest.main()
