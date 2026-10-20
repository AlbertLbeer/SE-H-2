# 使用说明

本文档说明如何运行、配置和扩展代码审查 Workflow。架构与设计动机见 [../Design.md](../Design.md)。

## 1. 环境

- Python 3.9 及以上（开发与验证使用 3.12）
- 无第三方依赖，全部使用标准库（`requirements.txt` 里没有可安装项）
- 不需要 API Key 也能跑通完整流程（规则审查 + 自动修复）

在项目根目录执行命令（工作区根目录默认为当前目录）：

```powershell
# 目录名取决于你 clone 时的名字
cd code-review-agent
```

## 2. 三种模式

### 2.1 离线模式（推荐用于演示 / 批改）

不调用任何模型，规则审查 + 自动修复完整运行：

```powershell
python -m app.main workflow --file examples/off_by_one.py --offline
```

### 2.2 Mock 模式

用假模型驱动项目 1 的 Agent 循环，可以看到审查 Agent 如何调用 `read_file` / `check_python_syntax`：

```powershell
python -m app.main workflow --file examples/multi_issue.py --mock
```

报告里的“模型审查意见”会明确标注是 Mock 演示结果，不代表真实审查质量。

### 2.3 真实模型模式

配置环境变量后，审查 Agent 会额外生成自然语言审查意见；给出 `--requirement` 时编码 Agent 会先生成代码：

```powershell
$env:LLM_BASE_URL = "https://api.deepseek.com"
$env:LLM_MODEL = "deepseek-flash"
$env:LLM_API_KEY = "替换为你的 API Key"

# 审查已有文件
python -m app.main workflow --file examples/multi_issue.py

# 从需求生成代码，再审查 + 修复
python -m app.main workflow --requirement "实现一个函数，返回列表中去重后的元素，保持原顺序" --max-rounds 2
```

> 没有配置 `LLM_API_KEY` 时，程序会自动降级为离线规则模式，并在第一行打印当前模式。

## 3. 参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `--file` | 无 | 已有代码文件，路径相对工作区根目录 |
| `--requirement` | 无 | 需求描述；与 `--file` 二选一（同时给时以 `--file` 为准） |
| `--max-rounds` | `2` | 最大审查轮次，即最多修几轮 |
| `--block-severity` | `high` | 达到该级别的问题视为阻塞问题：`high` / `medium` / `low` |
| `--mock` | 关 | 使用假模型 |
| `--offline` | 关 | 完全不调用模型 |
| `--run-root` | `runs` | 产物根目录 |
| `--quiet` | 关 | 只输出最终摘要 |

退出码：`0` 正常完成；`1` 流程走了错误分支或未完成；`2` 参数错误 / 工作流定义错误。

## 4. 产物

每次运行生成 `runs/<YYYYMMDD-HHMMSS>/`：

```text
report.md        报告正文（拓扑图、每轮问题、修复 diff、遗留问题、最终代码）+ 执行记录
trace.jsonl      逐事件日志，一行一个 JSON
state.json       最终状态快照
messages.json    Agent 间消息
run.json         图定义 + 状态 + 消息 + 运行结果
workflow.mmd     mermaid 流程图源码
work/v1_*.py     编码 Agent 产出的版本
work/v2_*.py     修复 Agent 产出的版本（可能有多版）
```

原始输入文件不会被修改，修复结果都写在 `work/` 下。

查看报告：

```powershell
Get-Content runs\<时间戳>\report.md -Encoding utf8
```

把 `workflow.mmd` 或报告里的 mermaid 代码块贴到支持 mermaid 的编辑器（Typora、VS Code、GitHub）即可看到流程图。

## 5. 把流程当成库来用

```python
from pathlib import Path
from app.config import Settings
from app.workflow.pipeline import run_workflow

settings = Settings.from_env()
run = run_workflow(settings, source_path="examples/off_by_one.py", max_rounds=2, echo=False)

print(run.result.status)              # completed
print(run.state.findings)             # 剩余问题（Finding 列表）
print(run.report_path)                # 报告路径
print(run.bus.latest("review.done"))  # 最近一次审查结论消息
```

## 6. 扩展

### 6.1 新增一条审查规则

```python
# app/tools/static_rules.py
RULE_CATALOG["PY011"] = {
    "title": "while True 缺少退出条件",
    "severity": "medium",
    "auto_fixable": False,
    "suggestion": "增加明确的 break 条件，或改用有界循环。",
}

def _check_while_true(tree):            # 新规则
    ...

def scan(source, path=""):
    ...
    findings.extend(_check_while_true(tree))   # 注册到扫描流程
```

### 6.2 新增一个自动修复器

```python
# app/tools/autofix.py
def _fix_while_true(source, finding):
    ...

FIXERS["PY011"] = _fix_while_true
```

修复器必须返回新源码或 `None`；返回源码后会自动做一次 `ast.parse` 复核。

### 6.3 新增一个 Agent 节点

```python
from app.workflow.node import AgentNode, NodeResult

class SecurityAuditNode(AgentNode):
    def __init__(self):
        super().__init__("security", "安全审计 Agent")
    def handle(self, state, bus):
        bus.publish("security", "audit.start")
        return NodeResult(summary="审计完成")
```

然后在 `app/workflow/pipeline.py` 的 `build_workflow()` 里 `add_node` + `add_edge`，
引擎、日志、报告会自动适配。

### 6.4 修改流程

只改 `build_workflow()` 的边。例如把最大轮次固定为 1 轮，可以直接删掉 `fixer → review` 这条边。

## 7. 常见问题

**Q：提示“根据需求生成代码需要真实 LLM”？**
`--requirement` 依赖模型写代码；请配置 `LLM_API_KEY`，或改用 `--file` 指定已有代码。

**Q：报告里没有任何问题？**
说明代码没有命中 10 条规则，不代表代码绝对正确；开启真实模型模式可获得更多语义层面的建议。

**Q：修复 Agent 说“需人工处理”？**
这些问题（如 `eval`、`os.system`、硬编码密钥）无法在不改变语义的前提下自动改写，
报告第 4 节会列出它们和建议。

**Q：想复现某次运行？**
`runs/<时间戳>/run.json` 里有同样的输入、图定义、每一步状态与耗时，按记录重跑即可复现。

**Q：`runs/` 会进版本库吗？**
不会，已在 `.gitignore` 中忽略。
