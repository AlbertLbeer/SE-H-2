"""自动修复器。

修复 Agent 只处理“可以安全地按规则改写”的问题（`Finding.auto_fixable`）。
所有修复遵循同一条规则：改完必须能重新通过 `ast.parse`，否则这次修复作废，
由上层记录为“修复失败”，原代码保持不变 —— 这是流程里的错误恢复机制。
"""

from __future__ import annotations

import ast
import re
from typing import Callable

from ..workflow.state import Finding


def apply_fix(source: str, finding: Finding) -> tuple[str | None, str]:
    """返回 (新源码, 说明)；无法修复时新源码为 None。"""
    fixer = FIXERS.get(finding.rule_id)
    if fixer is None:
        return None, "没有对应的自动修复器"
    try:
        new_source = fixer(source, finding)
    except Exception as exc:  # 修复器自身异常不应打断整个流程
        return None, f"修复器异常：{type(exc).__name__}: {exc}"
    if not new_source:
        return None, "该位置无法安全地自动修复"
    if new_source == source:
        return None, "修复后源码没有变化"
    try:
        ast.parse(new_source)
    except SyntaxError as exc:
        return None, f"修复后语法校验失败（第 {exc.lineno} 行：{exc.msg}），已放弃本次修改"
    return new_source, "已修复"


# --- 各规则的修复实现 ---


def _fix_off_by_one(source: str, finding: Finding) -> str | None:
    meta = finding.meta
    replacement = meta.get("replacement")
    span = (meta.get("start"), meta.get("end"))
    if not replacement or not span[0] or not span[1]:
        return None
    return _splice(source, tuple(span[0]), tuple(span[1]), replacement)


def _fix_bare_except(source: str, finding: Finding) -> str | None:
    lineno = finding.meta.get("lineno") or finding.line
    if not lineno:
        return None
    lines = source.splitlines(keepends=True)
    if lineno > len(lines):
        return None
    raw = lines[lineno - 1]
    match = re.search(r"except\s*:", raw)
    if not match:
        return None
    lines[lineno - 1] = raw[: match.start()] + "except Exception:" + raw[match.end():]
    return "".join(lines)


def _fix_none_compare(source: str, finding: Finding) -> str | None:
    meta = finding.meta
    start = meta.get("start")
    end = meta.get("end")
    if not start or not end:
        return None
    segment = _slice(source, tuple(start), tuple(end))
    # 只替换运算符本身，保留原有空格，避免出现 "is  None" 这种双空格
    if meta.get("op") == "ne":
        replaced = segment.replace("!=", "is not", 1)
    else:
        replaced = segment.replace("==", "is", 1)
    if replaced == segment:
        return None
    return _splice(source, tuple(start), tuple(end), replaced)


def _fix_missing_colon(source: str, finding: Finding) -> str | None:
    msg = finding.meta.get("msg", "")
    if "expected ':'" not in msg:
        return None
    lineno = finding.meta.get("lineno") or finding.line
    if not lineno:
        return None
    lines = source.splitlines(keepends=True)
    if lineno > len(lines):
        return None
    raw = lines[lineno - 1]
    core = raw.rstrip("\r\n")
    tail = raw[len(core):]
    stripped = core.rstrip()
    if stripped.endswith(":"):
        return None
    lines[lineno - 1] = stripped + ":" + core[len(stripped):] + tail
    return "".join(lines)


FIXERS: dict[str, Callable[[str, Finding], str | None]] = {
    "PY001": _fix_off_by_one,
    "PY003": _fix_bare_except,
    "PY006": _fix_none_compare,
    "PY009": _fix_missing_colon,
}


# --- 位置换算 ---
# ast 的 col_offset 是 UTF-8 字节偏移，源码里出现中文时必须换算成字符下标，
# 否则切片位置会错位。


def _line_starts(source: str) -> list[int]:
    starts = [0]
    for index, char in enumerate(source):
        if char == "\n":
            starts.append(index + 1)
    return starts


def _index(source: str, lineno: int, byte_col: int) -> int:
    starts = _line_starts(source)
    if lineno < 1 or lineno > len(starts):
        raise ValueError(f"行号越界：{lineno}")
    line_start = starts[lineno - 1]
    line_end = source.find("\n", line_start)
    if line_end == -1:
        line_end = len(source)
    line = source[line_start:line_end]
    char_col = len(line.encode("utf-8")[:byte_col].decode("utf-8", errors="ignore"))
    return line_start + min(char_col, len(line))


def _slice(source: str, start: tuple[int, int], end: tuple[int, int]) -> str:
    return source[_index(source, *start): _index(source, *end)]


def _splice(source: str, start: tuple[int, int], end: tuple[int, int], replacement: str) -> str:
    head = _index(source, *start)
    tail = _index(source, *end)
    return source[:head] + replacement + source[tail:]
