"""命令行入口。

两种使用方式：

* 单 Agent 模式（项目 1）：`python -m app.main --file review_files/my_code.py`
* Workflow 模式（项目 2）：`python -m app.main workflow --file review_files/my_code.py`

不带任何参数时进入单 Agent 的交互式对话。
"""

from __future__ import annotations

import argparse
import sys

from .agent import CodeReviewAgent, ToolRegistry
from .config import Settings
from .llm_client import LLMClient
from .mock_llm import MockLLMClient
# 模型可以调用的工具
from .tools.file_reader import FileReaderTool
from .tools.python_syntax import PythonSyntaxTool

WORKFLOW_COMMANDS = {"workflow", "flow"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="代码审查 Agent（单 Agent 模式）；Workflow 模式见 `python -m app.main workflow --help`"
    )
    parser.add_argument("--file", help="要审查的代码文件，相对于工作区根目录")
    parser.add_argument("--request", help="审查请求")
    parser.add_argument("--mock", action="store_true", help="使用本地假模型演示 Agent 工具调用流程")
    return parser


def main(argv: list[str] | None = None) -> None:
    args_in = list(sys.argv[1:] if argv is None else argv)
    # 子命令分发：workflow 交给 Workflow 模块，其余参数保持项目 1 的行为
    if args_in and args_in[0] in WORKFLOW_COMMANDS:
        from .workflow.cli import main as workflow_main

        raise SystemExit(workflow_main(args_in[1:]))
    if args_in and args_in[0] == "review":
        args_in = args_in[1:]

    args = build_parser().parse_args(args_in)
    settings = Settings.from_env()
    # 两个工具都由本地程序执行，模型只能选择是否调用，不能直接执行任意命令。
    file_reader = FileReaderTool(settings.workspace_root, settings.max_file_bytes)
    registry = ToolRegistry(
        {
            "read_file": file_reader,
            "check_python_syntax": PythonSyntaxTool(file_reader),
        }
    )
    client = (
        MockLLMClient()
        if args.mock
        else LLMClient(settings.base_url, settings.api_key, settings.model, settings.timeout_s, settings.max_retries)
    )
    agent = CodeReviewAgent(client, registry, settings.max_iterations, settings.max_history)

    if args.file:
        request = args.request or "请审查这个代码文件，指出潜在 Bug、风险和改进建议。"
        user_input = f"{request}\n文件路径：{args.file}"
        print(agent.run(user_input))
        return

    print("代码审查 Agent 已启动，输入 quit 退出。")
    print("提示：要运行 3 个 Agent 协作的 Workflow，请使用 `python -m app.main workflow --file <文件>`")
    while True:
        user_input = input("你：").strip()
        if user_input.lower() in {"quit", "exit", "q"}:
            break
        if not user_input:
            continue
        try:
            print(f"Agent：{agent.run(user_input)}")
        except RuntimeError as exc:
            print(f"Agent：{exc}")


if __name__ == "__main__":
    main()
