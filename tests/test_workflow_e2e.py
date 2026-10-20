"""端到端测试：不调用任何模型，完整跑一遍 编码 → 审查 → 修复 → 报告。"""

import json
import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.workflow.pipeline import run_workflow

OFF_BY_ONE = """def print_squares(nums):
    for i in range(len(nums) + 1):
        print(nums[i] * nums[i])

my_list = [2, 4, 6, 8]
print_squares(my_list)
"""

UNFIXABLE = """def parse(text):
    try:
        return eval(text)
    except:
        return None
"""


def make_settings(root: Path) -> Settings:
    return Settings(
        base_url="http://127.0.0.1/unused",
        api_key="",
        model="unused",
        timeout_s=5,
        max_retries=0,
        max_iterations=3,
        max_history=6,
        workspace_root=root.resolve(),
        max_file_bytes=200_000,
    )


class WorkflowEndToEndTests(unittest.TestCase):
    def test_offline_workflow_fixes_off_by_one_and_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.py"
            source.write_text(OFF_BY_ONE, encoding="utf-8")

            run = run_workflow(make_settings(root), source_path="sample.py", max_rounds=2, echo=False)

            self.assertEqual(run.result.status, "completed")
            self.assertTrue(run.ok)
            self.assertEqual(run.state.findings, [])
            # 编码 + 审查 + 门控 + 修复 + 审查 + 门控 + 报告
            self.assertEqual([step.node for step in run.result.steps],
                             ["coder", "review", "gate", "fixer", "review", "gate", "report"])
            self.assertEqual(run.result.visits("review"), 2)
            self.assertEqual([step.route for step in run.result.steps if step.node == "gate"], ["fixer", "report"])

            fixed = run.state.artifacts["versions"][-1]["path"]
            self.assertIn("range(len(nums))", (root / fixed).read_text(encoding="utf-8"))
            # 原始输入文件不允许被改动
            self.assertEqual(source.read_text(encoding="utf-8"), OFF_BY_ONE)

    def test_artifacts_are_written(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.py").write_text(OFF_BY_ONE, encoding="utf-8")

            run = run_workflow(make_settings(root), source_path="sample.py", echo=False)

            for name in ("report.md", "state.json", "run.json", "messages.json", "workflow.mmd", "trace.jsonl"):
                self.assertTrue((run.run_dir / name).exists(), f"缺少产物 {name}")

            report = (run.run_dir / "report.md").read_text(encoding="utf-8")
            self.assertIn("Workflow 拓扑", report)
            self.assertIn("```mermaid", report)
            self.assertIn("执行记录", report)
            self.assertIn("range(len(nums))", report)

            state = json.loads((run.run_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["round"], 2)

            events = [
                json.loads(line)
                for line in (run.run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            kinds = {event["kind"] for event in events}
            self.assertTrue({"workflow.start", "node.start", "node.done", "workflow.end"} <= kinds)

    def test_messages_flow_between_agents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.py").write_text(OFF_BY_ONE, encoding="utf-8")

            run = run_workflow(make_settings(root), source_path="sample.py", echo=False)
            messages = run.bus.messages()
            topics = [message.topic for message in messages]

            self.assertIn("code.ready", topics)
            self.assertIn("review.done", topics)
            self.assertIn("gate.decision", topics)
            self.assertIn("fix.done", topics)
            self.assertIn("report.written", topics)

            review_done = run.bus.latest("review.done")
            self.assertEqual(review_done.recipient, "gate")
            self.assertIn("findings", review_done.payload)

    def test_unfixable_issue_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.py").write_text(UNFIXABLE, encoding="utf-8")

            run = run_workflow(make_settings(root), source_path="sample.py", max_rounds=2, echo=False)

            self.assertEqual(run.result.status, "completed")
            # eval 无法自动修复，第二轮结束后仍有阻塞问题 → 报告里要提示人工处理
            self.assertTrue(run.state.findings)
            self.assertTrue(any(f.rule_id == "PY004" for f in run.state.findings))
            report = run.report_path.read_text(encoding="utf-8")
            self.assertIn("遗留问题", report)
            self.assertIn("PY004", report)

    def test_bare_except_is_fixed_but_eval_survives(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.py").write_text(UNFIXABLE, encoding="utf-8")

            run = run_workflow(make_settings(root), source_path="sample.py", echo=False)

            fixes = run.state.artifacts["fixes"]
            self.assertEqual(len(fixes), 1)
            self.assertEqual([item["rule_id"] for item in fixes[0]["applied"]], ["PY003"])
            self.assertIn("except Exception:", fixes[0]["diff"])

    def test_missing_source_routes_to_error_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            run = run_workflow(make_settings(root), source_path="missing.py", echo=False)

            self.assertEqual(run.result.status, "completed")
            self.assertFalse(run.ok)
            self.assertEqual(run.state.artifacts.get("failed_node"), "coder")
            self.assertEqual(run.result.steps[0].node, "coder")
            self.assertEqual(run.result.steps[0].status, "failed")
            self.assertEqual(run.result.steps[-1].node, "report")

    def test_max_rounds_one_stops_after_first_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.py").write_text(OFF_BY_ONE, encoding="utf-8")

            run = run_workflow(make_settings(root), source_path="sample.py", max_rounds=1, echo=False)

            self.assertEqual([step.node for step in run.result.steps], ["coder", "review", "gate", "report"])
            self.assertTrue(run.state.findings)

    def test_fixer_without_fixable_work_goes_straight_to_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # 第一次修复已经把 PY003 修完，第二轮只剩 eval → 修复 Agent 无活可干，不能再回到审查
            (root / "sample.py").write_text(UNFIXABLE, encoding="utf-8")

            run = run_workflow(make_settings(root), source_path="sample.py", max_rounds=5, echo=False)

            nodes = [step.node for step in run.result.steps]
            self.assertEqual(nodes, ["coder", "review", "gate", "fixer", "review", "gate", "fixer", "report"])
            self.assertEqual(run.result.status, "completed")
            self.assertEqual(run.result.steps[-2].status, "skipped")

    def test_block_severity_low_blocks_on_style_issues(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.py").write_text("def f(value):\n    if value == None:\n        return 1\n    return 0\n", encoding="utf-8")

            run = run_workflow(
                make_settings(root),
                source_path="sample.py",
                max_rounds=2,
                block_severity="low",
                echo=False,
            )

            # 低级别问题也会触发修复，修完清零后通过
            self.assertEqual(run.state.findings, [])
            self.assertEqual([step.node for step in run.result.steps],
                             ["coder", "review", "gate", "fixer", "review", "gate", "report"])


if __name__ == "__main__":
    unittest.main()
