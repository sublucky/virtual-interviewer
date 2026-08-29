"""Debug 子系统（架构 §3.13）。

设计要点：
- 会话级开关，未开启时 emit 为空操作（一次 dict 查询 + bool 判断）
- 事件入缓冲前统一脱敏，密钥/凭证不落缓冲、不出网
- 每会话环形缓冲，前端断线重连可拉 history 补齐
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import deque
from typing import Any

from server.models import DebugEvent, DebugEventType

RING_SIZE = 500

_SECRET_PATTERNS = [
    re.compile(r"(sk-[A-Za-z0-9_\-.]{6})[A-Za-z0-9_\-.]+"),
    re.compile(r"(Bearer\s+)[A-Za-z0-9_\-.]+", re.IGNORECASE),
]
_SECRET_KEYS = {"api_key", "apikey", "authorization", "password", "passwd", "token", "secret", "remote_pass"}


def mask_secrets(value: Any) -> Any:
    """递归脱敏：敏感键整体打码，字符串中的密钥模式部分打码。"""
    if isinstance(value, dict):
        return {
            k: ("***" if k.lower() in _SECRET_KEYS else mask_secrets(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [mask_secrets(v) for v in value]
    if isinstance(value, str):
        masked = value
        for pattern in _SECRET_PATTERNS:
            masked = pattern.sub(r"\1***", masked)
        return masked
    return value


class DebugEmitter:
    """事件总线：采集 → 脱敏 → 环形缓冲 + 订阅队列。"""

    def __init__(self, *, default_enabled: bool = False) -> None:
        self._default_enabled = default_enabled
        self._enabled: dict[str, bool] = {}
        self._ring: dict[str, deque[DebugEvent]] = {}
        self._subscribers: dict[str, list[asyncio.Queue[DebugEvent]]] = {}

    # -- 开关 --------------------------------------------------------------

    def register(self, session_id: str, enabled: bool | None = None) -> None:
        self._enabled[session_id] = self._default_enabled if enabled is None else enabled
        self._ring.setdefault(session_id, deque(maxlen=RING_SIZE))

    def set_enabled(self, session_id: str, enabled: bool) -> None:
        self._enabled[session_id] = enabled
        self._ring.setdefault(session_id, deque(maxlen=RING_SIZE))

    def is_enabled(self, session_id: str) -> bool:
        return self._enabled.get(session_id, self._default_enabled)

    def forget(self, session_id: str) -> None:
        self._enabled.pop(session_id, None)
        self._ring.pop(session_id, None)
        self._subscribers.pop(session_id, None)

    # -- 采集 --------------------------------------------------------------

    def emit(self, session_id: str, type_: DebugEventType, **data: Any) -> None:
        if not self.is_enabled(session_id):
            return
        event = DebugEvent(type=type_, data=mask_secrets(data))
        self._ring.setdefault(session_id, deque(maxlen=RING_SIZE)).append(event)
        for queue in self._subscribers.get(session_id, []):
            # 订阅端消费不及时不应拖慢面试主链路
            if not queue.full():
                queue.put_nowait(event)

    def state_change(self, session_id: str, *, from_: str, to: str, reason: str = "") -> None:
        self.emit(session_id, "state_change", **{"from": from_, "to": to, "reason": reason})

    def retrieval(self, session_id: str, *, query: str, kinds: list[str], hits: list[dict[str, Any]], took_ms: int) -> None:
        self.emit(session_id, "retrieval", query=query, kinds=kinds, hits=hits, took_ms=took_ms)

    def comm(self, session_id: str, *, target: str, action: str, took_ms: int, status: Any = "ok", **extra: Any) -> None:
        self.emit(session_id, "comm", target=target, action=action, took_ms=took_ms, status=status, **extra)

    def latency(self, session_id: str, **segments: Any) -> None:
        self.emit(session_id, "latency", **segments)

    def log(self, session_id: str, message: str, **extra: Any) -> None:
        self.emit(session_id, "debug_log", message=message, **extra)

    # -- 消费 --------------------------------------------------------------

    def history(self, session_id: str) -> list[DebugEvent]:
        return list(self._ring.get(session_id, ()))

    def subscribe(self, session_id: str) -> asyncio.Queue[DebugEvent]:
        queue: asyncio.Queue[DebugEvent] = asyncio.Queue(maxsize=RING_SIZE)
        self._subscribers.setdefault(session_id, []).append(queue)
        return queue

    def unsubscribe(self, session_id: str, queue: asyncio.Queue[DebugEvent]) -> None:
        subs = self._subscribers.get(session_id)
        if subs and queue in subs:
            subs.remove(queue)


class Stopwatch:
    """用于给 comm/latency 事件打点。"""

    def __init__(self) -> None:
        self._start = time.perf_counter()
        self._marks: dict[str, int] = {}

    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self._start) * 1000)

    def mark(self, name: str) -> int:
        value = self.elapsed_ms()
        self._marks[name] = value
        return value

    @property
    def marks(self) -> dict[str, int]:
        return dict(self._marks)
