from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class FileReaderTool:
    workspace_root: Path
    max_bytes: int = 200_000

    name: str = "read_file"
    description: str = "读取工作区内的文本代码文件，不执行文件内容。"

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "相对于工作区根目录的文件路径",
                        }
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
        }

    def execute(self, path: str) -> str:
        # resolve 后再检查 relative_to，防止 ../ 路径逃出工作区。
        requested = (self.workspace_root / path).resolve()
        try:
            requested.relative_to(self.workspace_root)
        except ValueError as exc:
            raise ValueError("文件路径必须位于工作区目录内") from exc

        if not requested.exists():
            raise FileNotFoundError(f"文件不存在：{path}")
        if not requested.is_file():
            raise ValueError(f"目标不是普通文件：{path}")
        if requested.stat().st_size > self.max_bytes:
            raise ValueError(f"文件超过大小限制：{self.max_bytes} 字节")

        try:
            # 第一版只处理 UTF-8 文本，避免二进制文件被误当成代码。
            return requested.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("文件不是 UTF-8 文本，暂不支持读取") from exc
