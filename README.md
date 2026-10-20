# Code Review Workflow Agent

一个 3 个 Agent 协作的代码审查 Workflow（作业 2）。流程是 **编码 → 审查 → 修复 → 复审 → 报告**：
多个 Agent 通过共享状态和消息总线协作，支持条件分支、循环修复、并行审查、执行日志和错误降级。

> 项目 1 的单 Agent 审查器（`CodeReviewAgent`）被完整复用为 Workflow 里“审查 Agent”的模型分支，
> 没有重写，也没有绕过。

## 1. 它能做什么

给定一个 Python 文件，Workflow 会自动完成：

1. **编码 Agent** 载入代码（或在给出需求时调用模型生成代码），把代码落盘成一个版本文件；
2. **审查 Agent** 并行做两件事：规则化 AST 扫描（结构化问题）+ 复用项目 1 的 Agent 做模型审查（自然语言意见）；
3. **门控节点** 判断还有没有达到阻塞阈值的问题、还有没有剩余轮次，决定“继续修”还是“直接出报告”；
4. **修复 Agent** 按规则逐条自动修复，每修一条都重新做语法校验，修完写新版本并生成 diff；
5. 修复后回到第 2 步 **复审**，直到问题清零或用完轮次；
6. **报告 Agent** 输出 Markdown 报告：流程拓扑、每轮问题、修复 diff、遗留问题、最终代码、执行记录。

## 2. 快速开始

无需安装第三方依赖（只用 Python 标准库，Python 3.9+）：

```powershell
# 1) 进入仓库目录（目录名取决于你 clone 时的名字）
cd code-review-agent

# 2) 不需要 API Key，规则审查 + 自动修复完整可跑
python -m app.main workflow --file examples/off_by_one.py --offline
```

输出示例（节选）：

```text
模式：离线规则模式（--offline，不调用任何模型）
[  0.00s] workflow.start 开始执行工作流「code-review-workflow」，入口节点 coder
[  0.01s] node.done    编码 Agent → review｜读取已有代码，共 6 行 → runs/20261020-085118/work/v1_off_by_one.py
[  0.02s] node.done    审查 Agent → gate｜第 1 轮审查：1 个问题（高 1）
[  0.02s] route.explicit gate 显式路由到 fixer
[  0.02s] node.done    门控（条件分支） → fixer｜仍有 1 个「高」级问题，进入第 2 轮修复
[  0.03s] node.done    修复 Agent → review｜第 2 轮修复：1 条已修复，0 条需人工
[  0.03s] node.done    审查 Agent → gate｜第 2 轮审查：0 个问题（高 0）
[  0.03s] node.done    门控（条件分支） → report｜没有达到「高」级的问题，审查通过
[  0.03s] node.done    报告 Agent → [结束]｜报告已生成：runs/20261020-085118/report.md
[  0.03s] workflow.end 工作流结束：completed（在节点 report 结束（无后续边）），共 7 步
```

每次运行都会在 `runs/<时间戳>/` 下留下完整产物（`runs/` 已被 `.gitignore` 忽略，不会进仓库）：

```text
runs/20261019-085118/
├── report.md             # 审查报告（含 mermaid 拓扑图、diff、遗留问题）
├── trace.jsonl           # 逐事件执行日志
├── state.json            # 最终共享状态快照
├── run.json              # 工作流定义 + 状态 + 消息 + 运行结果的汇总
├── messages.json         # Agent 之间的消息记录
├── workflow.mmd          # mermaid 流程图源码
└── work/
    ├── v1_off_by_one.py  # 编码 Agent 产出的原始版本
    └── v2_off_by_one.py  # 修复 Agent 产出的修复版本
```

一份完整报告样例见 [docs/sample-report.md](docs/sample-report.md)（`examples/multi_issue.py`，离线模式，含“修不动的遗留问题”分支）。

## 3. 三种运行模式

| 命令 | 是否需要 API Key | 说明 |
|------|------------------|------|
| `python -m app.main workflow --file <文件> --offline` | 否 | 只跑规则审查与自动修复，最稳定，适合演示和测试 |
| `python -m app.main workflow --file <文件> --mock` | 否 | 假模型驱动项目 1 的 Agent 循环，演示“模型审查”节点如何调用工具 |
| `python -m app.main workflow --file <文件>` | 是 | 真实模型：审查 Agent 会额外生成自然语言审查意见 |
| `python -m app.main workflow --requirement "<需求>"` | 是 | 真实模型：编码 Agent 先按需求生成代码，再走审查与修复 |

配置真实模型（DeepSeek 示例）：

```powershell
$env:LLM_BASE_URL = "https://api.deepseek.com"
$env:LLM_MODEL = "deepseek-flash"
$env:LLM_API_KEY = "替换为你的 API Key"
```

> 程序只从环境变量读取配置，不会自动加载 `.env` 文件；`.env.example` 是变量清单，照着它设置即可。
> 真实 Key 不要写进任何源码或文档，`.env` 已在 `.gitignore` 中忽略。

常用参数：

```powershell
python -m app.main workflow `
  --file examples/multi_issue.py `
  --max-rounds 3 `              # 最多 3 轮审查/修复（默认 2）
  --block-severity high `       # high/medium/low：达到该级别视为阻塞问题
  --quiet                       # 只打印最终摘要
```

自己写代码待审查时，把文件放进 `review_files/`（该目录内容不进 Git），
再把上面的 `--file` 换成 `review_files/你的文件.py` 即可。

## 4. 演示素材

仓库自带三个示例文件，覆盖三种典型路径：

```powershell
# ① 可自动修复：range(len(x) + 1) 越界 → 修复 → 复审清零 → 通过
python -m app.main workflow --file examples/off_by_one.py --offline

# ② 语法错误：缺少冒号 → 修复 → 复审通过
python -m app.main workflow --file examples/syntax_error.py --offline

# ③ 混合问题：3 个可自动修复 + 4 个需人工确认（eval / os.system / shell=True / 硬编码密钥）
python -m app.main workflow --file examples/multi_issue.py --max-rounds 2
```

第 ③ 个用例会演示“轮次用尽但仍有阻塞问题”的分支：报告里会列出遗留问题并提示人工处理。

> 说明：`examples/syntax_error.py` 与 `examples/multi_issue.py` 是**故意写坏的测试输入**，
> 用于演示审查与修复能力，不要把它们当成项目源码；`examples/` 不会被导入或执行。

## 5. 项目结构

```text
code-review-agent/
├── app/
│   ├── workflow/                 # Workflow 框架（与具体业务无关）
│   │   ├── state.py              # 共享状态 + 结构化问题 Finding
│   │   ├── bus.py                # Agent 间消息总线（广播 / 定向 / 主题查询）
│   │   ├── node.py               # 节点抽象、NodeResult、并行节点
│   │   ├── graph.py              # 节点 + 条件边 + 校验 + mermaid 导出
│   │   ├── engine.py             # 执行引擎（顺序 / 分支 / 循环守卫 / 重试 / 错误分支）
│   │   ├── trace.py              # 控制台日志 + trace.jsonl
│   │   ├── pipeline.py           # 接线、运行、产物落盘（唯一描述“谁接谁”的地方）
│   │   └── cli.py                # workflow 子命令
│   ├── agents/                   # 业务 Agent
│   │   ├── coder.py              # 编码 Agent
│   │   ├── reviewer.py           # 审查 Agent（规则 + 模型并行）
│   │   ├── fixer.py              # 修复 Agent
│   │   ├── gate.py               # 门控节点（条件分支）
│   │   └── reporter.py           # 报告 Agent
│   ├── tools/
│   │   ├── static_rules.py       # 10 条 AST 规则
│   │   ├── autofix.py            # 4 类自动修复器（带语法复核）
│   │   ├── file_reader.py        # 项目 1：安全读文件
│   │   ├── file_writer.py        # 安全写文件（原子替换）
│   │   ├── python_syntax.py      # 项目 1：AST 语法检查
│   │   └── diffing.py            # 统一 diff
│   ├── agent.py                  # 项目 1：单 Agent 循环（被复用为审查节点）
│   ├── llm_client.py             # LLM HTTP 客户端（含重试）
│   ├── mock_llm.py               # 假模型
│   ├── prompts.py                # 三种 Agent 的 Prompt
│   ├── config.py                 # 环境变量配置
│   └── main.py                   # 统一入口（单 Agent / Workflow）
├── examples/                     # 演示用待审查代码
├── review_files/                 # 自己放待审查代码（被 Git 忽略）
├── tests/                        # 61 个单元测试与端到端测试
├── docs/
│   ├── USAGE.md                  # 详细使用说明
│   └── sample-report.md          # 一次运行的报告样例
├── Design.md                     # 架构与设计说明
└── requirements.txt
```

## 6. 作业要求对照

| 要求 | 实现位置 |
|------|----------|
| 至少 2 个 Agent 协作完成完整流程 | 编码 / 审查 / 修复 / 报告 4 个 Agent 节点（`app/agents/`） |
| Agent 间消息传递或状态共享 | 两者都有：`WorkflowState` 共享状态 + `MessageBus` 消息（`app/workflow/state.py`、`bus.py`） |
| 基本流程控制（顺序执行） | `WorkflowEngine` 按边顺序推进（`app/workflow/engine.py`） |
| MVP 可运行 | 无 API Key 也能端到端跑通，见第 2 节 |
| 条件分支 | 门控节点返回 `route`，图上有“需要修复 / 通过”两条边 |
| 循环 | `fixer → review` 回边，`max_rounds` 与引擎的 `max_visits` 双重退出条件 |
| 并行执行 | 审查节点内部 `ParallelNode` 并行跑规则审查与模型审查 |
| 执行日志与状态追踪 | `trace.jsonl` + `state.json` + `messages.json` |
| 错误处理与恢复 | 节点异常重试、`when="error"` 错误分支、模型不可用自动降级、修复后语法复核失败则回滚该条修改 |
| 复用项目 1 的 Agent | `app/agents/reviewer.py` 直接 `from ..agent import CodeReviewAgent` |
| 可扩展性 | 新增 Agent 只需写一个节点 + 在 `pipeline.py` 加两条边，引擎与报告无需改动 |

## 7. 测试

```powershell
python -m unittest discover -s tests -v
```

覆盖：Workflow 引擎（顺序 / 分支 / 循环守卫 / 重试 / 错误分支 / 图校验）、消息总线、
并行节点合并、状态隔离、10 条规则、4 个自动修复器、安全读写文件、以及 8 个端到端场景
（含“修复后复审通过走”和“轮次用尽仍需人工”两条主路径）。

架构细节见 [Design.md](Design.md)，用法细节见 [docs/USAGE.md](docs/USAGE.md)。

## 8. 单 Agent 模式

```powershell
# 一次性审查
python -m app.main --file examples/off_by_one.py --request "请审查这个 Python 文件"

# 无 API Key 演示
python -m app.main --mock --file examples/off_by_one.py

# 交互式多轮对话
python -m app.main
```

## 9. 当前限制

- 规则审查只覆盖 10 类常见问题，且只支持 Python；不执行待审查代码
- 自动修复只处理 4 类可安全改写的问题，其余问题只给建议
- 模型审查意见是自然语言，结构化结论始终以规则扫描为准（保证门控判断可复现）
- 历史消息、运行产物都保存在 `runs/`，不进入版本库
- 审查结论不能替代人工 Review
