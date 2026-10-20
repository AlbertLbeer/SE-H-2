"""生成统一 diff，用于展示修复前后的改动。"""

from __future__ import annotations

import difflib


def unified_diff(before: str, after: str, from_name: str = "before", to_name: str = "after") -> str:
    if before == after:
        return ""
    lines = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=from_name,
        tofile=to_name,
        lineterm="",
        n=3,
    )
    return "\n".join(lines) + "\n"


def changed_line_count(diff: str) -> int:
    return sum(1 for line in diff.splitlines() if line.startswith(("+", "-")) and not line.startswith(("+++", "---")))
