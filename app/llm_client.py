from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[dict[str, Any]]
    raw: dict[str, Any]


class LLMClient:
    def __init__(self, base_url: str, api_key: str, model: str, timeout_s: int, max_retries: int):
        # 兼容两种常见配置：完整 endpoint，或只填写服务商的 base URL。
        self.base_url = self._normalize_endpoint(base_url)
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s
        self.max_retries = max_retries

    @staticmethod
    def _normalize_endpoint(base_url: str) -> str:
        endpoint = base_url.rstrip("/")
        if endpoint.endswith("/chat/completions"):
            return endpoint
        return f"{endpoint}/chat/completions"

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        # 在真正请求前尽早检查配置，让错误提示比远程接口返回的错误更清楚。
        if not self.api_key:
            raise RuntimeError("未设置 LLM_API_KEY。请配置 API Key，或使用 --mock 运行流程演示。")
        if not self.model:
            raise RuntimeError("未设置 LLM_MODEL。请配置要使用的模型名称。")

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
        }
        if tools:
            # DeepSeek 的 Chat Completions 接口兼容 OpenAI 风格的 tools 参数。
            payload["tools"] = tools

        request = urllib.request.Request(
            self.base_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                # 非流式请求便于先完成 Agent 工具调用；后续可再增加流式输出。
                with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                    raw = json.loads(response.read().decode("utf-8"))
                message = raw["choices"][0]["message"]
                return LLMResponse(
                    content=message.get("content"),
                    tool_calls=message.get("tool_calls") or [],
                    raw=raw,
                )
            except urllib.error.HTTPError as exc:
                # 尽量保留服务商返回的错误正文，便于定位模型名或工具 schema 配置问题。
                try:
                    detail = exc.read().decode("utf-8", errors="replace")
                except Exception:
                    detail = str(exc)
                last_error = RuntimeError(f"HTTP {exc.code}: {detail[:500]}")
                if attempt < self.max_retries:
                    time.sleep(2**attempt)
            except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
                # 指数退避，避免接口短暂波动时立即连续请求。
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(2**attempt)
        raise RuntimeError(f"LLM 调用失败，已重试 {self.max_retries} 次：{last_error}") from last_error
