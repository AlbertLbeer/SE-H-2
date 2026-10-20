from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"环境变量 {name} 必须是整数") from exc
    if value <= 0:
        raise ValueError(f"环境变量 {name} 必须大于 0")
    return value


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: str
    model: str
    timeout_s: int
    max_retries: int
    max_iterations: int
    max_history: int
    workspace_root: Path
    max_file_bytes: int

    @classmethod
    def from_env(cls) -> "Settings":
        # 所有外部配置都从环境变量读取，避免把 API Key 写进源码或提交到仓库。
        return cls(
            # 这里使用完整的 Chat Completions 地址，因为项目用 urllib 直接发 HTTP 请求。
            base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/chat/completions"),
            api_key=os.getenv("LLM_API_KEY", ""),
            # DeepSeek 官方将 V4.1-Flash 的 API 模型名定义为 deepseek-flash。
            model=os.getenv("LLM_MODEL", "deepseek-flash"),
            timeout_s=_int_env("LLM_TIMEOUT_S", 60),
            max_retries=_int_env("LLM_MAX_RETRIES", 2),
            max_iterations=_int_env("AGENT_MAX_ITERATIONS", 6),
            max_history=_int_env("AGENT_MAX_HISTORY", 12),
            workspace_root=Path(os.getenv("FILE_WORKSPACE_ROOT", ".")).resolve(),
            max_file_bytes=_int_env("FILE_MAX_BYTES", 200_000),
        )
