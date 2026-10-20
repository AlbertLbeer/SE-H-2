"""`python -m app.main workflow ...` 的命令行入口。"""

from __future__ import annotations

import argparse

from ..config import Settings
from ..llm_client import LLMClient
from ..mock_llm import MockLLMClient
from .graph import WorkflowError
from .pipeline import run_workflow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.main workflow",
        description="代码审查 Workflow：编码 Agent → 审查 Agent → 门控 → 修复 Agent → 报告 Agent",
    )
    parser.add_argument("--file", help="已有代码文件（相对工作区），作为流程输入")
    parser.add_argument("--requirement", help="需求描述，由编码 Agent 调用模型生成代码（需要真实 LLM）")
    parser.add_argument("--max-rounds", type=int, default=2, help="最大审查轮次，默认 2")
    parser.add_argument(
        "--block-severity",
        choices=["high", "medium", "low"],
        default="high",
        help="达到该级别的问题视为阻塞问题并触发修复，默认 high",
    )
    parser.add_argument("--mock", action="store_true", help="用假模型演示项目 1 Agent 的工具调用流程")
    parser.add_argument("--offline", action="store_true", help="完全不调用 LLM，只跑规则审查与自动修复")
    parser.add_argument("--run-root", default="runs", help="产物目录，默认 runs/")
    parser.add_argument("--quiet", action="store_true", help="只输出最终摘要，不打印每个节点日志")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings.from_env()
    llm, mode_note, real_llm = _build_llm(settings, args)

    if args.requirement and not real_llm:
        print("根据需求生成代码需要真实 LLM，请配置 LLM_API_KEY 后重试，或改用 --file 指定已有代码")
        return 2
    if not args.file and not args.requirement:
        print("请至少提供 --file 或 --requirement")
        return 2

    print(f"模式：{mode_note}")
    try:
        run = run_workflow(
            settings,
            source_path=args.file or "",
            requirement=args.requirement or "",
            max_rounds=args.max_rounds,
            block_severity=args.block_severity,
            llm=llm,
            echo=not args.quiet,
            run_root=args.run_root,
        )
    except WorkflowError as exc:
        print(f"工作流定义错误：{exc}")
        return 2
    except (FileNotFoundError, ValueError) as exc:
        print(f"运行失败：{exc}")
        return 1

    print("")
    print("=" * 72)
    print("工作流拓扑：")
    print(run.workflow.to_text())
    print("=" * 72)
    print(f"运行结果：{run.summary()}")
    print(f"产物目录：{run.run_dir}")
    if run.report_path:
        print(f"审查报告：{run.report_path}")
    if run.state.artifacts.get("failed_node"):
        print(f"注意：节点 {run.state.artifacts['failed_node']} 执行失败，已走错误分支")
        return 1
    return 0 if run.result.status == "completed" else 1


def _build_llm(settings: Settings, args: argparse.Namespace) -> tuple[object | None, str, bool]:
    """返回 (llm, 模式说明, 是否真实模型)。"""
    if args.offline:
        return None, "离线规则模式（--offline，不调用任何模型）", False
    if args.mock:
        return MockLLMClient(), "Mock 模式（假模型，仅演示项目 1 Agent 的工具调用流程）", False
    if not settings.api_key:
        return None, "离线规则模式（未检测到 LLM_API_KEY，规则审查与自动修复仍然完整运行）", False
    client = LLMClient(
        settings.base_url,
        settings.api_key,
        settings.model,
        settings.timeout_s,
        settings.max_retries,
    )
    return client, f"真实模型模式（{settings.model}）", True
