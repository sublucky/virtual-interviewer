"""语音链路抽象（架构 §3.10）。

- VOICE_MODE=text：浏览器 Web Speech + LiveTalking 内置 TTS
- VOICE_MODE=omni：QwenOmniRealtime（转写 + 口播音频）→ /humanaudio
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from server.providers.omni_realtime import NullOmni, OmniError, QwenOmniRealtime, build_omni

__all__ = [
    "ASREngine",
    "TTSEngine",
    "RealtimeVoiceEngine",
    "BrowserASR",
    "AvatarInlineTTS",
    "QwenOmniRealtime",
    "NullOmni",
    "OmniError",
    "build_omni",
]


class ASREngine(Protocol):
    async def transcribe(self, audio: AsyncIterator[bytes]) -> AsyncIterator[str]: ...


class TTSEngine(Protocol):
    async def synthesize(self, text: str) -> AsyncIterator[bytes]: ...
    async def interrupt(self) -> None: ...


class RealtimeVoiceEngine(Protocol):
    async def transcribe(self, audio: bytes, *, session_id: str = "") -> str: ...
    async def synthesize(self, text: str, *, session_id: str = "", instructions: str = "") -> bytes: ...


class BrowserASR:
    """占位：识别在浏览器完成，服务端只接收最终文本。"""

    async def transcribe(self, audio: AsyncIterator[bytes]) -> AsyncIterator[str]:
        raise NotImplementedError("文字模式由浏览器 Web Speech API 完成识别")


class AvatarInlineTTS:
    """占位：TTS 由 LiveTalking 在 /human 内部完成。"""

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        raise NotImplementedError("文字模式由 LiveTalking 内部完成合成")

    async def interrupt(self) -> None:
        return None
