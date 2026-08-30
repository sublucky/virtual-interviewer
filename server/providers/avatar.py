"""AvatarRenderer（架构 §3.11）：LiveTalking HTTP 客户端。

只走公开 HTTP 接口，不引用 LiveTalking 源码，便于其独立升级。
后期换全视频生成时，实现同一组方法即可，编排层不动。
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from server.config import AvatarSettings
from server.models import HealthStatus


class AvatarRenderer(Protocol):
    async def open_stream(self, sdp: str, **kw: Any) -> tuple[str, str]: ...
    async def speak(self, rtc_session_id: str, text: str, *, interrupt: bool = False) -> None: ...
    async def speak_audio(
        self, rtc_session_id: str, wav_bytes: bytes, *, interrupt: bool = False
    ) -> None: ...
    async def interrupt(self, rtc_session_id: str) -> None: ...
    async def is_speaking(self, rtc_session_id: str) -> bool: ...
    async def health(self) -> HealthStatus: ...


class LiveTalkingAvatar:
    def __init__(self, settings: AvatarSettings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(base_url=settings.base_url, timeout=settings.timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def open_stream(self, sdp: str, **kw: Any) -> tuple[str, str]:
        """WHEP 信令：返回 (answer_sdp, rtc_session_id)。"""
        params = {"avatar": kw.get("avatar") or self._settings.avatar_id}
        resp = await self._client.post(
            "/whep",
            params=params,
            content=sdp,
            headers={"Content-Type": "application/sdp"},
        )
        resp.raise_for_status()
        return resp.text, resp.headers.get("X-Session-ID", "")

    async def speak(self, rtc_session_id: str, text: str, *, interrupt: bool = False) -> None:
        """type=echo：TTS 由 LiveTalking 内部完成。"""
        resp = await self._client.post(
            "/human",
            json={
                "sessionid": rtc_session_id,
                "text": text,
                "type": "echo",
                "interrupt": interrupt,
            },
        )
        resp.raise_for_status()

    async def speak_audio(
        self, rtc_session_id: str, wav_bytes: bytes, *, interrupt: bool = False
    ) -> None:
        """外置音频驱动口型：POST /humanaudio（sessionid + file）。"""
        if interrupt:
            try:
                await self.interrupt(rtc_session_id)
            except Exception:  # noqa: BLE001 — 打断失败仍尝试推音频
                pass
        resp = await self._client.post(
            "/humanaudio",
            data={"sessionid": rtc_session_id},
            files={"file": ("speech.wav", wav_bytes, "audio/wav")},
        )
        resp.raise_for_status()

    async def interrupt(self, rtc_session_id: str) -> None:
        resp = await self._client.post("/interrupt_talk", json={"sessionid": rtc_session_id})
        resp.raise_for_status()

    async def is_speaking(self, rtc_session_id: str) -> bool:
        resp = await self._client.post("/is_speaking", json={"sessionid": rtc_session_id})
        resp.raise_for_status()
        return bool(resp.json().get("data"))

    async def set_audiotype(self, rtc_session_id: str, audiotype: int) -> None:
        resp = await self._client.post(
            "/set_audiotype", json={"sessionid": rtc_session_id, "audiotype": audiotype}
        )
        resp.raise_for_status()

    async def health(self) -> HealthStatus:
        try:
            resp = await self._client.post("/is_speaking", json={"sessionid": "healthcheck"})
            # 会话不存在也说明服务在线
            return HealthStatus(ok=True, extra={"base": self._settings.base_url, "status": resp.status_code})
        except Exception as exc:  # noqa: BLE001 — 需要把不可用原因暴露到 /api/meta
            return HealthStatus(ok=False, detail=str(exc), extra={"base": self._settings.base_url})


class NullAvatar:
    """无数字人环境下的空实现：文字面试仍可跑通。"""

    async def open_stream(self, sdp: str, **kw: Any) -> tuple[str, str]:
        raise RuntimeError("未配置数字人服务")

    async def speak(self, rtc_session_id: str, text: str, *, interrupt: bool = False) -> None:
        return None

    async def speak_audio(
        self, rtc_session_id: str, wav_bytes: bytes, *, interrupt: bool = False
    ) -> None:
        return None

    async def interrupt(self, rtc_session_id: str) -> None:
        return None

    async def is_speaking(self, rtc_session_id: str) -> bool:
        return False

    async def health(self) -> HealthStatus:
        return HealthStatus(ok=False, detail="未配置数字人服务")
