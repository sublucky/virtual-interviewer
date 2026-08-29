"""LLM 网关（架构 §3.9）：vLLM 主用 + 备份降级 + 熔断。

统一 OpenAI 兼容协议，vLLM / 百炼 / llama-server 同一客户端。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator
from typing import Any, Protocol

from openai import APIError, AsyncOpenAI

from server.config import LLMSettings
from server.models import HealthStatus

_THINK_OPEN, _THINK_CLOSE = "<think>", "</think>"


class LLMError(RuntimeError):
    pass


class _Breaker:
    """连续失败达阈值切换降级，冷却后半开探测。"""

    def __init__(self, *, threshold: int, cooldown: float) -> None:
        self._threshold = threshold
        self._cooldown = cooldown
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def open(self) -> bool:
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at >= self._cooldown:
            self._opened_at = None
            self._failures = 0
            return False
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._threshold:
            self._opened_at = time.monotonic()


class LLMClient:
    def __init__(self, settings: LLMSettings) -> None:
        self._settings = settings
        self._primary = AsyncOpenAI(
            api_key=settings.api_key or "not-needed",
            base_url=settings.api_base,
            timeout=settings.total_timeout,
        )
        self._fallback = (
            AsyncOpenAI(
                api_key=settings.fallback_api_key or "not-needed",
                base_url=settings.fallback_api_base,
                timeout=settings.total_timeout,
            )
            if settings.has_fallback
            else None
        )
        self._breaker = _Breaker(
            threshold=settings.breaker_threshold, cooldown=settings.breaker_cooldown
        )

    # -- 路由 --------------------------------------------------------------

    def _client(self) -> tuple[AsyncOpenAI, str]:
        if self._breaker.open and self._fallback is not None:
            return self._fallback, "fallback"
        return self._primary, "primary"

    def _extra_body(self) -> dict[str, Any] | None:
        if not self._settings.disable_thinking:
            return None
        return {"enable_thinking": False}

    async def _create(self, client: AsyncOpenAI, **kwargs: Any) -> Any:
        extra = self._extra_body()
        if extra:
            try:
                return await client.chat.completions.create(**kwargs, extra_body=extra)
            except APIError:
                # 部分本地服务不认识 enable_thinking，去掉重试一次
                pass
        return await client.chat.completions.create(**kwargs)

    # -- 流式对话 ----------------------------------------------------------

    async def stream(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> AsyncIterator[tuple[str, str]]:
        """产出 ('delta', text) 或 ('thinking', '')。"""
        client, route = self._client()
        kwargs: dict[str, Any] = {
            "model": self._settings.model,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        try:
            stream = await self._create(client, **kwargs)
        except Exception as exc:  # noqa: BLE001 — 统一转为 LLMError 交上层降级
            self._breaker.record_failure()
            if route == "primary" and self._fallback is not None:
                stream = await self._create(self._fallback, **kwargs)
            else:
                raise LLMError(f"LLM 请求失败：{exc}") from exc

        in_think = False
        got_any = False
        try:
            async for chunk in stream:
                text, thinking_only = _split_delta(chunk)
                if thinking_only:
                    yield "thinking", ""
                    continue
                if not text:
                    continue
                visible, in_think = _strip_think(text, in_think)
                if visible:
                    got_any = True
                    yield "delta", visible
                elif in_think:
                    yield "thinking", ""
        except Exception as exc:  # noqa: BLE001
            self._breaker.record_failure()
            raise LLMError(f"LLM 流中断：{exc}") from exc

        if got_any:
            self._breaker.record_success()

    # -- JSON 输出 ---------------------------------------------------------

    async def complete_json(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 1800,
        temperature: float = 0.3,
    ) -> dict[str, Any]:
        client, route = self._client()
        kwargs: dict[str, Any] = {
            "model": self._settings.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        try:
            resp = await self._create(client, **kwargs)
        except Exception as exc:  # noqa: BLE001
            self._breaker.record_failure()
            if route == "primary" and self._fallback is not None:
                resp = await self._create(self._fallback, **kwargs)
            else:
                raise LLMError(f"LLM 请求失败：{exc}") from exc

        raw = (resp.choices[0].message.content or "").strip()
        self._breaker.record_success()
        visible, _ = _strip_think(raw, False)
        return _parse_json(visible.strip())

    # -- 健康检查 ----------------------------------------------------------

    async def health(self) -> HealthStatus:
        try:
            models = await self._primary.models.list()
            ids = [m.id for m in models.data[:12]]
            return HealthStatus(
                ok=True,
                extra={
                    "base": self._settings.api_base,
                    "model": self._settings.model,
                    "models": ids,
                    "breaker_open": self._breaker.open,
                },
            )
        except Exception as exc:  # noqa: BLE001 — 健康检查失败需要展示原因
            return HealthStatus(
                ok=False,
                detail=str(exc),
                extra={"base": self._settings.api_base, "model": self._settings.model},
            )


# --------------------------------------------------------------------------
# 解析辅助
# --------------------------------------------------------------------------


def _split_delta(chunk: Any) -> tuple[str, bool]:
    if not getattr(chunk, "choices", None):
        return "", False
    delta = chunk.choices[0].delta
    content = getattr(delta, "content", None) or ""
    reasoning = getattr(delta, "reasoning_content", None) or ""
    if not reasoning:
        extra = getattr(delta, "model_extra", None) or {}
        if isinstance(extra, dict):
            reasoning = extra.get("reasoning_content") or ""
    return content, bool(reasoning) and not content


def _strip_think(chunk: str, in_think: bool) -> tuple[str, bool]:
    """剥离 <think>...</think>，跨 chunk 保持状态。"""
    out: list[str] = []
    i = 0
    while i < len(chunk):
        if not in_think:
            start = chunk.find(_THINK_OPEN, i)
            if start == -1:
                out.append(chunk[i:])
                break
            out.append(chunk[i:start])
            i = start + len(_THINK_OPEN)
            in_think = True
        else:
            end = chunk.find(_THINK_CLOSE, i)
            if end == -1:
                return "".join(out), True
            i = end + len(_THINK_CLOSE)
            in_think = False
    return "".join(out), in_think


def _parse_json(raw: str) -> dict[str, Any]:
    text = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise LLMError(f"模型未返回 JSON：{text[:240]}")
    return json.loads(text[start : end + 1])


class ChatLLM(Protocol):
    """面试引擎 / 评估 / 语料 Agent 共用的最小接口，便于 mock 替换。"""

    async def stream(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> AsyncIterator[tuple[str, str]]: ...

    async def complete_json(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 1800,
        temperature: float = 0.3,
    ) -> dict[str, Any]: ...

    async def health(self) -> HealthStatus: ...


_MOCK_REPLIES = [
    "你好，我是今天的面试官。我们大概聊四十分钟，先从项目开始。请先简单介绍一下你最近负责的系统。",
    "你提到这个系统，当时的流量和数据量具体是多少？",
    "明白了。那你为什么选择现在这套方案，而不是更简单的替代？",
    "好的。请再说一个你在这个项目里踩过的坑，以及后来怎么防复发。",
    "感谢你的分享。后续我们会内部讨论，一周内给你结果。你有什么想问我的吗？",
]


class MockLLM:
    """本地前端联调：按脚本逐字流式吐字，不访问真实模型。"""

    def __init__(self) -> None:
        self._i = 0

    async def stream(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> AsyncIterator[tuple[str, str]]:
        _ = messages, max_tokens, temperature
        reply = _MOCK_REPLIES[min(self._i, len(_MOCK_REPLIES) - 1)]
        self._i += 1
        yield "thinking", ""
        for char in reply:
            await asyncio.sleep(0.012)
            yield "delta", char

    async def complete_json(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 1800,
        temperature: float = 0.3,
    ) -> dict[str, Any]:
        _ = messages, max_tokens, temperature
        return {
            "overall": 72,
            "recommendation": "lean_hire",
            "level_guess": "高级工程师",
            "dimensions": [
                {"name": "技术深度", "score": 4, "note": "能讲清取舍，但量化证据偏少"},
                {"name": "工程实践", "score": 3, "note": "有规范意识，落地细节可再追"},
                {"name": "问题解决", "score": 4, "note": "排查路径清楚"},
                {"name": "沟通表达", "score": 4, "note": "结构完整"},
                {"name": "岗位匹配", "score": 3, "note": "本场覆盖面一般"},
            ],
            "strengths": ["能量化问题规模", "有复盘意识"],
            "risks": ["分布式场景本场未覆盖"],
            "evidence": [{"quote": "流量大概三千", "why": "说明有真实容量认知"}],
            "next_round_focus": ["分布式事务与一致性"],
            "summary": "基础扎实，深度尚可，建议进入下一轮。",
        }

    async def health(self) -> HealthStatus:
        return HealthStatus(ok=True, extra={"provider": "mock", "model": "mock-interviewer"})


def build_llm(settings: LLMSettings) -> ChatLLM:
    if settings.provider == "mock":
        return MockLLM()
    return LLMClient(settings)
