"""语音链路抽象（架构 §3.10）。

MVP：ASR 在浏览器（Web Speech API）、TTS 由 LiveTalking 内部完成，
因此服务端这两个接口默认是占位实现。它们存在的意义是把演进路径钉住：
- 换本地 Whisper：实现 ASREngine
- 换独立 TTS：实现 TTSEngine，pipeline 改为先合成再送 /humanaudio
- 换端到端实时语音：实现 RealtimeVoiceEngine，替换三段式管道
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol


class ASREngine(Protocol):
    async def transcribe(self, audio: AsyncIterator[bytes]) -> AsyncIterator[str]: ...


class TTSEngine(Protocol):
    async def synthesize(self, text: str) -> AsyncIterator[bytes]: ...
    async def interrupt(self) -> None: ...


class RealtimeVoiceEngine(Protocol):
    """端到端实时语音（Qwen-Omni / GPT-Realtime 类），二期接入。"""

    async def converse(self, audio: AsyncIterator[bytes]) -> AsyncIterator[bytes]: ...


class BrowserASR:
    """占位：识别在浏览器完成，服务端只接收最终文本。"""

    async def transcribe(self, audio: AsyncIterator[bytes]) -> AsyncIterator[str]:
        raise NotImplementedError("MVP 由浏览器 Web Speech API 完成识别")


class AvatarInlineTTS:
    """占位：TTS 由 LiveTalking 在 /human 内部完成。"""

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        raise NotImplementedError("MVP 由 LiveTalking 内部完成合成")

    async def interrupt(self) -> None:
        return None
