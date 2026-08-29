"""LLM 网关（架构 §3.9）：vLLM 主用 + 备份降级 + 熔断。

统一 OpenAI 兼容协议，vLLM / 百炼 / llama-server 同一客户端。
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator
from typing import Any

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
