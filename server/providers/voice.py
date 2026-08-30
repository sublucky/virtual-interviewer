"""语音链路抽象（架构 §3.10）。

- VOICE_MODE=text：浏览器 Web Speech（ASR）+ LiveTalking 内置 TTS
- VOICE_MODE=omni：ASR → LLM → TTS 三段式
  - ASR：Whisper（本地 faster-whisper）或 Omni Realtime
  - LLM：InterviewEngine（RAG + 流式）
  - TTS：ChatTTS（本地）→ Omni 合成兜底 → LiveTalking /humanaudio 对口型
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from server.providers.chat_tts import ChatTTSEngine, ChatTTSError, NullTTS, build_tts
from server.providers.omni_realtime import NullOmni, OmniError, QwenOmniRealtime, build_omni
from server.providers.whisper_asr import NullASR, WhisperASR, WhisperError, build_asr

__all__ = [
    "ASREngine",
    "TTSEngine",
    "RealtimeVoiceEngine",
    "BrowserASR",
    "AvatarInlineTTS",
    "QwenOmniRealtime",
    "WhisperASR",
    "ChatTTSEngine",
    "NullOmni",
    "NullASR",
    "NullTTS",
    "OmniError",
    "WhisperError",
    "ChatTTSError",
    "build_omni",
    "build_asr",
    "build_tts",
]


class ASREngine(Protocol):
    """语音识别：音频 → 文本。"""

    async def transcribe(self, audio: bytes, *, session_id: str = "") -> str: ...


class TTSEngine(Protocol):
    """语音合成：文本 → WAV 音频。"""

    async def synthesize(self, text: str, *, session_id: str = "", instructions: str = "") -> bytes: ...
    async def interrupt(self) -> None: ...


class RealtimeVoiceEngine(Protocol):
    """端到端实时语音（预留）：一次调用完成转写 + 回复 + 音频。"""

    async def transcribe(self, audio: bytes, *, session_id: str = "") -> str: ...
    async def synthesize(self, text: str, *, session_id: str = "", instructions: str = "") -> bytes: ...


class BrowserASR:
    """占位：识别在浏览器完成，服务端只接收最终文本。"""

    async def transcribe(self, audio: bytes, *, session_id: str = "") -> str:
        raise NotImplementedError("文字模式由浏览器 Web Speech API 完成识别")


class AvatarInlineTTS:
    """占位：TTS 由 LiveTalking 在 /human 内部完成。"""

    async def synthesize(self, text: str, *, session_id: str = "", instructions: str = "") -> bytes:
        raise NotImplementedError("文字模式由 LiveTalking 内部完成合成")

    async def interrupt(self) -> None:
        return None
