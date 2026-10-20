"""规则化静态审查。

这是 Workflow 里“审查 Agent”的可复现内核：只用 Python 标准库 `ast` 做解析，
不执行待审查代码，因此没有 API Key 也能跑出真实的审查结论（离线演示、自动化测试都靠它）。

每条规则都登记在 `RULE_CATALOG` 里，报告节点用 `rule_title()` 取中文标题，
修复 Agent 用 `rule_of()` 判断该问题是否有可用的自动修复器。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from ..workflow.state import Finding

# 规则编号 → 元信息
RULE_CATALOG: dict[str, dict] = {
    "PY001": {
        "title": "range 越界风险",
        "severity": "high",
        "auto_fixable": True,
        "suggestion": "循环上界多加了偏移量，索引会越界；去掉多余的加减项。",
    },
    "PY002": {
        "title": "可变默认参数",
        "severity": "medium",
        "auto_fixable": False,
        "suggestion": "把默认值改成 None，并在函数体内判断后重新赋值。",
    },
    "PY003": {
        "title": "裸 except",
        "severity": "medium",
        "auto_fixable": True,
        "suggestion": "明确捕获 Exception 或具体异常类型，避免吞掉 KeyboardInterrupt/SystemExit。",
    },
    "PY004": {
        "title": "使用 eval/exec",
        "severity": "high",
        "auto_fixable": False,
        "suggestion": "改用显式解析（ast.literal_eval、json.loads 或专门的解析函数）。",
    },
    "PY005": {
        "title": "subprocess shell=True",
        "severity": "high",
        "auto_fixable": False,
        "suggestion": "以列表形式传参并去掉 shell=True，避免命令注入。",
    },
    "PY006": {
        "title": "与 None 比较使用 ==",
        "severity": "low",
        "auto_fixable": True,
        "suggestion": "判断 None 应使用 is / is not。",
    },
    "PY007": {
        "title": "使用 os.system/os.popen",
        "severity": "high",
        "auto_fixable": False,
        "suggestion": "改用 subprocess.run 并以列表传参。",
    },
    "PY008": {
        "title": "疑似硬编码密钥",
        "severity": "high",
        "auto_fixable": False,
        "suggestion": "从环境变量或配置中读取，不要把密钥写进源码。",
    },
    "PY009": {
        "title": "Python 语法错误",
        "severity": "high",
        "auto_fixable": True,
        "suggestion": "按报错位置修正语法，修复后重新做语法校验。",
    },
    "PY010": {
        "title": "open 未使用 with",
        "severity": "low",
        "auto_fixable": False,
        "suggestion": "使用 with open(...) as f，确保异常时也能关闭文件。",
    },
}

_SECRET_HINTS = ("password", "passwd", "secret", "token", "api_key", "apikey", "access_key", "private_key")
_LOOP_FUNCS = {"range"}
_SHELL_FUNCS = {"run", "call", "Popen", "check_output", "check_call"}


@dataclass(frozen=True)
class Rule:
    rule_id: str
    title: str
    severity: str
    auto_fixable: bool
    suggestion: str


def rule_of(rule_id: str) -> Rule | None:
    meta = RULE_CATALOG.get(rule_id)
    if meta is None:
        return None
    return Rule(rule_id, meta["title"], meta["severity"], meta["auto_fixable"], meta["suggestion"])


def rule_title(rule_id: str) -> str:
    meta = RULE_CATALOG.get(rule_id)
    return meta["title"] if meta else rule_id


def scan(source: str, path: str = "") -> list[Finding]:
    """返回结构化问题列表，按行号排序。"""
    filename = path or "<review>"
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        return [_syntax_finding(exc)]

    findings: list[Finding] = []
    findings.extend(_check_off_by_one(tree))
    findings.extend(_check_mutable_defaults(tree))
    findings.extend(_check_bare_except(tree))
    findings.extend(_check_dangerous_calls(tree))
    findings.extend(_check_shell_true(tree))
    findings.extend(_check_none_compare(tree))
    findings.extend(_check_hardcoded_secret(tree))
    findings.extend(_check_open_without_with(tree))
    findings.sort(key=lambda f: (f.line or 0, f.rule_id))
    return findings


# --- 单条规则 ---


def _syntax_finding(exc: SyntaxError) -> Finding:
    msg = exc.msg or "语法错误"
    expected_colon = "expected ':'" in msg or "expected ':'" in str(exc)
    return _make(
        "PY009",
        message=f"语法错误：{msg}",
        line=exc.lineno,
        column=exc.offset,
        auto_fixable=expected_colon,
        meta={
            "msg": msg,
            "lineno": exc.lineno,
            "offset": exc.offset,
            "text": exc.text,
        },
    )


def _check_off_by_one(tree: ast.AST) -> list[Finding]:
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_name(node.func, _LOOP_FUNCS)):
            continue
        if len(node.args) != 1:
            continue
        argument = node.args[0]
        parsed = _split_len_plus_offset(argument)
        if parsed is None:
            continue
        len_call, offset = parsed
        sequence = _name_of(len_call.args[0]) if len_call.args else None
        if not sequence:
            continue
        scope = _innermost_scope(tree, node.lineno)
        if not _is_subscripted(scope, sequence):
            continue
        findings.append(
            _make(
                "PY001",
                message=(
                    f"range(len({sequence}) + {offset}) 比可索引范围多 {offset}，"
                    f"循环到最后一轮时 {sequence}[i] 会抛 IndexError"
                ),
                line=node.lineno,
                column=node.col_offset + 1,
                auto_fixable=True,
                meta={
                    "start": [argument.lineno, argument.col_offset],
                    "end": [argument.end_lineno, argument.end_col_offset],
                    "replacement": f"len({sequence})",
                    "sequence": sequence,
                },
            )
        )
    return findings


def _check_mutable_defaults(tree: ast.AST) -> list[Finding]:
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        defaults = list(node.args.defaults) + [d for d in node.args.kw_defaults if d is not None]
        for default in defaults:
            if _is_mutable_literal(default):
                findings.append(
                    _make(
                        "PY002",
                        message=f"函数 {node.name} 的参数默认值是可变对象，多次调用会共享同一份数据",
                        line=default.lineno,
                        column=default.col_offset + 1,
                        auto_fixable=False,
                        meta={"function": node.name},
                    )
                )
    return findings


def _check_bare_except(tree: ast.AST) -> list[Finding]:
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            findings.append(
                _make(
                    "PY003",
                    message="使用了裸 except，会一并吞掉 KeyboardInterrupt 和 SystemExit，排查问题时难以定位",
                    line=node.lineno,
                    column=node.col_offset + 1,
                    auto_fixable=True,
                    meta={"lineno": node.lineno, "col": node.col_offset},
                )
            )
    return findings


def _check_dangerous_calls(tree: ast.AST) -> list[Finding]:
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_name(node.func, {"eval", "exec"}):
            name = _name_of(node.func)
            findings.append(
                _make(
                    "PY004",
                    message=f"调用 {name}() 执行字符串代码，输入不可信时等同于远程代码执行",
                    line=node.lineno,
                    column=node.col_offset + 1,
                )
            )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if _is_name(node.func.value, {"os"}) and node.func.attr in {"system", "popen"}:
                findings.append(
                    _make(
                        "PY007",
                        message=f"使用 os.{node.func.attr} 执行 shell 命令，命令拼接存在注入风险",
                        line=node.lineno,
                        column=node.col_offset + 1,
                    )
                )
    return findings


def _check_shell_true(tree: ast.AST) -> list[Finding]:
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr not in _SHELL_FUNCS:
            continue
        for keyword in node.keywords:
            if keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                findings.append(
                    _make(
                        "PY005",
                        message=f"subprocess.{node.func.attr}(shell=True) 会把参数交给 shell 解释，存在命令注入风险",
                        line=node.lineno,
                        column=node.col_offset + 1,
                    )
                )
    return findings


def _check_none_compare(tree: ast.AST) -> list[Finding]:
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        op = node.ops[0]
        if not isinstance(op, (ast.Eq, ast.NotEq)):
            continue
        operands = [node.left] + list(node.comparators)
        if not any(isinstance(item, ast.Constant) and item.value is None for item in operands):
            continue
        symbol = "==" if isinstance(op, ast.Eq) else "!="
        findings.append(
            _make(
                "PY006",
                message=f"使用 {symbol} None 判断空值，应改用 {'is None' if symbol == '==' else 'is not None'}",
                line=node.lineno,
                column=node.col_offset + 1,
                auto_fixable=True,
                meta={
                    "start": [node.lineno, node.col_offset],
                    "end": [node.end_lineno, node.end_col_offset],
                    "op": "eq" if isinstance(op, ast.Eq) else "ne",
                },
            )
        )
    return findings


def _check_hardcoded_secret(tree: ast.AST) -> list[Finding]:
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
            continue
        if len(node.value.value) < 6:
            continue
        for target in node.targets:
            name = _name_of(target)
            if not name:
                continue
            lowered = name.lower()
            if any(hint in lowered for hint in _SECRET_HINTS):
                findings.append(
                    _make(
                        "PY008",
                        message=f"变量 {name} 被直接赋值为字符串常量，疑似硬编码密钥",
                        line=node.lineno,
                        column=node.col_offset + 1,
                    )
                )
    return findings


def _check_open_without_with(tree: ast.AST) -> list[Finding]:
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_name(node.func, {"open"})):
            continue
        if isinstance(parents.get(node), ast.withitem):
            continue
        findings.append(
            _make(
                "PY010",
                message="open() 没有放在 with 语句里，异常时可能无法及时关闭文件",
                line=node.lineno,
                column=node.col_offset + 1,
            )
        )
    return findings


# --- 工具函数 ---


def _make(
    rule_id: str,
    message: str,
    line: int | None = None,
    column: int | None = None,
    auto_fixable: bool = False,
    meta: dict | None = None,
) -> Finding:
    rule = rule_of(rule_id)
    return Finding(
        rule_id=rule_id,
        severity=rule.severity if rule else "medium",
        message=message,
        line=line,
        column=column,
        suggestion=rule.suggestion if rule else "",
        auto_fixable=auto_fixable,
        source="static",
        meta=meta or {},
    )


def _is_name(node: ast.AST, names: set) -> bool:
    found = _name_of(node)
    return bool(found) and found.rsplit(".", 1)[-1] in names


def _name_of(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _name_of(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _split_len_plus_offset(node: ast.AST) -> tuple[ast.Call, int] | None:
    """识别 `len(x) + n` 或 `n + len(x)`，返回 (len 调用, 偏移量)。"""
    if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)):
        return None
    for candidate, offset in ((node.left, node.right), (node.right, node.left)):
        if not (isinstance(candidate, ast.Call) and _is_name(candidate.func, {"len"})):
            continue
        if len(candidate.args) != 1:
            continue
        if isinstance(offset, ast.Constant) and isinstance(offset.value, int) and offset.value > 0:
            return candidate, offset.value
    return None


def _is_mutable_literal(node: ast.AST) -> bool:
    if isinstance(node, (ast.List, ast.Dict, ast.Set)):
        return True
    return isinstance(node, ast.Call) and _is_name(node.func, {"list", "dict", "set"})


def _scope_ranges(tree: ast.AST) -> list[tuple[ast.AST, int, int]]:
    ranges: list[tuple[ast.AST, int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            start = node.lineno
            end = getattr(node, "end_lineno", start) or start
            ranges.append((node, start, end))
    return ranges


def _innermost_scope(tree: ast.AST, lineno: int) -> ast.AST:
    candidates = [item for item in _scope_ranges(tree) if item[1] <= lineno <= item[2]]
    if not candidates:
        return tree
    # 行号范围最小的就是最内层作用域
    return min(candidates, key=lambda item: item[2] - item[1])[0]


def _is_subscripted(scope: ast.AST, sequence: str) -> bool:
    for node in ast.walk(scope):
        if isinstance(node, ast.Subscript) and _name_of(node.value) == sequence:
            return True
    return False
