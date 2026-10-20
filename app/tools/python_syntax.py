from __future__ import annotations

import ast
import json
from dataclasses import dataclass

from .file_reader import FileReaderTool


@dataclass
class PythonSyntaxTool:
    """使用 Python AST 解析器检查语法，不执行用户代码。"""

    file_reader: FileReaderTool

    name: str = "check_python_syntax"
    description: str = "检查工作区内 Python 文件的语法，只解析代码，不执行代码。"

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
                            "description": "相对于工作区根目录的 Python 文件路径",
                        }
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
        }

    def execute(self, path: str) -> str:
        # 复用文件读取工具的路径安全、大小限制和 UTF-8 校验。
        source = self.file_reader.execute(path)
        try:
            # ast.parse 只构建语法树，不会运行代码或导入模块。
            ast.parse(source, filename=path, mode="exec")
        except SyntaxError as exc:
            result = {
                "ok": False,
                "path": path,
                "line": exc.lineno,
                "column": exc.offset,
                "message": exc.msg,
            }
        else:
            result = {
                "ok": True,
                "path": path,
                "message": "Python 语法检查通过。",
            }
        # 返回 JSON，方便模型稳定识别检查状态和错误位置。
        return json.dumps(result, ensure_ascii=False)

