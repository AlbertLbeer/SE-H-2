import unittest

from app.workflow.bus import MessageBus
from app.workflow.node import NodeResult, ParallelNode
from app.workflow.state import Finding, WorkflowState, dedupe_findings


class RecordingNode:
    def __init__(self, name, findings=(), delay=0.0):
        self.name = name
        self.title = name
        self._findings = list(findings)
        self.delay = delay

    def run(self, state, bus):
        if self.delay:
            import time

            time.sleep(self.delay)
        state.findings.extend(self._findings)
        state.log(self.name, "完成")
        bus.publish(self.name, "node.done")
        return NodeResult(summary=f"{self.name} 完成")


class MessageBusTests(unittest.TestCase):
    def test_broadcast_and_direct_message(self):
        bus = MessageBus()
        bus.publish("reviewer", "review.done", to="gate", count=1)
        bus.publish("coder", "code.ready")

        gate_inbox = bus.inbox("gate")
        self.assertEqual({m.topic for m in gate_inbox}, {"review.done", "code.ready"})

        fixer_inbox = bus.inbox("fixer")
        self.assertEqual([m.topic for m in fixer_inbox], ["code.ready"])

    def test_latest_message(self):
        bus = MessageBus()
        bus.publish("a", "t", value=1)
        bus.publish("b", "t", value=2)

        latest = bus.latest("t")
        self.assertIsNotNone(latest)
        self.assertEqual(latest.payload["value"], 2)
        self.assertEqual(bus.to_dict()["total"], 2)


class ParallelNodeTests(unittest.TestCase):
    def test_merges_findings_from_children(self):
        first = RecordingNode("first", [Finding("PY004", "high", "eval")])
        second = RecordingNode("second", [Finding("PY003", "medium", "裸 except")])
        node = ParallelNode("review", "审查 Agent", [first, second])

        state = WorkflowState()
        result = node.run(state, MessageBus())

        self.assertEqual(result.status, "ok")
        self.assertEqual({f.rule_id for f in state.findings}, {"PY004", "PY003"})
        self.assertEqual(len(state.events), 2)

    def test_child_failures_are_reported(self):
        class Broken:
            name = "broken"
            title = "broken"

            def run(self, state, bus):
                return NodeResult(status="failed", summary="失败", error="boom")

        node = ParallelNode("review", "审查 Agent", [Broken(), RecordingNode("ok")])

        result = node.run(WorkflowState(), MessageBus())

        self.assertEqual(result.status, "failed")
        self.assertIn("boom", result.error or "")

    def test_previous_findings_not_duplicated(self):
        state = WorkflowState()
        state.replace_findings([Finding("PY001", "high", "旧问题")])
        node = ParallelNode("review", "审查 Agent", [RecordingNode("empty")])

        node.run(state, MessageBus())

        self.assertEqual(state.findings, [])


class StateTests(unittest.TestCase):
    def test_blocking_findings_respects_threshold(self):
        state = WorkflowState(block_severity="medium")
        state.replace_findings(
            [
                Finding("PY001", "high", "高"),
                Finding("PY002", "medium", "中"),
                Finding("PY006", "low", "低"),
            ]
        )

        self.assertEqual(len(state.blocking_findings()), 2)
        self.assertEqual(state.count_by_severity(), {"high": 1, "medium": 1, "low": 1})

    def test_fork_does_not_share_containers(self):
        state = WorkflowState()
        state.replace_findings([Finding("PY001", "high", "问题")])
        fork = state.fork()
        fork.findings.clear()
        fork.artifacts["x"] = 1

        self.assertEqual(len(state.findings), 1)
        self.assertNotIn("x", state.artifacts)

    def test_dedupe_findings(self):
        duplicated = [Finding("PY001", "high", "问题", line=3), Finding("PY001", "high", "问题", line=3)]
        self.assertEqual(len(dedupe_findings(duplicated)), 1)


if __name__ == "__main__":
    unittest.main()
