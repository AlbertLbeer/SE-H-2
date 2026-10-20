"""安全写文件工具。

修复 Agent 需要把新版本代码落到磁盘（这样“审查 → 修复 → 再审查”才是真的端到端）。
写入规则与读取工具一致：路径必须在工作区内、大小有上限，另外采用“临时文件 + 原子替换”，
避免写到一半中断留下半个文件。
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FileWriterTool:
    workspace_root: Path
    max_bytes: int = 200_000

    name: str = "write_file"
    description: str = "把文本内容写入工作区内的文件，路径必须在工作区内。"

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "相对于工作区根目录的文件路径"},
                        "content": {"type": "string", "description": "要写入的 UTF-8 文本"},
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
            },
        }

    def resolve(self, path: str) -> Path:
        requested = (self.workspace_root / path).resolve()
        try:
            requested.relative_to(self.workspace_root)
        except ValueError as exc:
            raise ValueError("文件路径必须位于工作区目录内") from exc
        return requested

    def write(self, path: str, content: str) -> str:
        target = self.resolve(path)
        data = content.encode("utf-8")
        if len(data) > self.max_bytes:
            raise ValueError(f"内容超过大小限制：{self.max_bytes} 字节")
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_path = tempfile.mkstemp(dir=str(target.parent), prefix=".tmp-", suffix=".swp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
                stream.write(content)
            os.replace(temp_path, target)
        except BaseException:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise
        return path

    def relative(self, target: Path) -> str:
        return str(Path(target).resolve().relative_to(self.workspace_root))
