"""执行日志与状态追踪。

同时提供两种输出：
* 控制台：即时看到每个节点做了什么（执行日志）；
* `trace.jsonl`：一行一个事件，便于事后回放和自动化断言（状态追踪）。
"""

from __future__ import annotations

import io
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO


class TraceRecorder:
    def __init__(
        self,
        run_dir: Path | None = None,
        echo: bool = True,
        stream: TextIO | None = None,
        file_name: str = "trace.jsonl",
    ):
        self.run_dir = Path(run_dir) if run_dir else None
        self.echo = echo
        self.stream = stream if stream is not None else sys.stdout
        self.events: list[dict[str, Any]] = []
        self._started = time.perf_counter()
        self._handle: TextIO | None = None
        if self.run_dir is not None:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            self._handle = (self.run_dir / file_name).open("w", encoding="utf-8")

    def elapsed(self) -> float:
        return time.perf_counter() - self._started

    def event(self, kind: str, message: str = "", node: str | None = None, **data: Any) -> dict[str, Any]:
        record = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "elapsed_s": round(self.elapsed(), 3),
            "kind": kind,
            "node": node,
            "message": message,
            "data": data,
        }
        self.events.append(record)
        if self._handle is not None:
            self._handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._handle.flush()
        if self.echo:
            self._echo(record)
        return record

    def _echo(self, record: dict[str, Any]) -> None:
        text = f"[{record['elapsed_s']:6.2f}s] {record['kind']:<12} {record['message']}".rstrip()
        try:
            print(text, file=self.stream)
        except UnicodeEncodeError:  # 极端终端编码下的兜底，不打断流程
            encoding = getattr(self.stream, "encoding", None) or "utf-8"
            safe = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
            print(safe, file=self.stream)

    def to_dict(self) -> dict[str, Any]:
        return {"events": self.events, "count": len(self.events)}

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> "TraceRecorder":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


class NullTrace(TraceRecorder):
    """测试用：不输出、不落盘。"""

    def __init__(self) -> None:
        super().__init__(run_dir=None, echo=False, stream=io.StringIO())
