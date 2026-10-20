"""Agent 间的消息总线。

作业要求“实现 Agent 间的消息传递”。这里提供一个极简的发布/订阅 + 点对点投递：

* `publish(sender, topic, to=None, **payload)`：广播（`to=None`）或定向发送；
* `inbox("fixer")`：某个 Agent 读取自己的收件箱；
* `of_topic("review.done")`：按主题回看历史消息，用于报告和调试。

总线只保存消息，不负责调度；调度由 `engine` 完成。这样 Agent 之间是松耦合的：
新增一个 Agent 只需要订阅已有主题，不需要修改发送方。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Message:
    sender: str
    topic: str
    payload: dict[str, Any] = field(default_factory=dict)
    recipient: str | None = None
    ts: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "sender": self.sender,
            "recipient": self.recipient,
            "topic": self.topic,
            "payload": self.payload,
        }


class MessageBus:
    def __init__(self) -> None:
        self._messages: list[Message] = []
        self._lock = threading.Lock()

    def publish(
        self,
        sender: str,
        topic: str,
        to: str | None = None,
        **payload: Any,
    ) -> Message:
        message = Message(
            sender=sender,
            topic=topic,
            payload=payload,
            recipient=to,
            ts=datetime.now().isoformat(timespec="milliseconds"),
        )
        with self._lock:
            self._messages.append(message)
        return message

    def messages(self) -> list[Message]:
        with self._lock:
            return list(self._messages)

    def inbox(self, agent: str) -> list[Message]:
        """定向发给该 Agent 的消息 + 广播消息。"""
        return [m for m in self.messages() if m.recipient in (None, agent) and m.sender != agent]

    def of_topic(self, topic: str) -> list[Message]:
        return [m for m in self.messages() if m.topic == topic]

    def latest(self, topic: str) -> Message | None:
        found = self.of_topic(topic)
        return found[-1] if found else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": len(self._messages),
            "by_topic": _count_topics(self._messages),
            "messages": [m.to_dict() for m in self.messages()],
        }


def _count_topics(messages: list[Message]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for message in messages:
        counts[message.topic] = counts.get(message.topic, 0) + 1
    return counts
