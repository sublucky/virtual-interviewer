"""Whisper ASR：本地 faster-whisper 语音识别。

浏览器上传 WAV/PCM16 → 16kHz mono PCM16 → faster-whisper 转写。
"""

from __future__ import annotations

import asyncio
from typing import Any

from server.audio import load_user_pcm16
from server.config import WhisperSettings
from server.debug import DebugEmitter, Stopwatch
from server.models import HealthStatus


class WhisperError(RuntimeError):
    pass


class WhisperASR:
    """ASREngine：本地 faster-whisper 转写。"""

    def __init__(self, settings: WhisperSettings, debug: DebugEmitter | None = None) -> None:
        self._settings = settings
        self._debug = debug
        self._model: Any = None
        self._lock = asyncio.Lock()

    async def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        async with self._lock:
            if self._model is not None:
                return self._model
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise WhisperError("缺少 faster-whisper，请 pip install faster-whisper") from exc
            self._model = WhisperModel(
                self._settings.model,
                device=self._settings.device,
                compute_type=self._settings.compute_type,
            )
            return self._model

    async def health(self) -> HealthStatus:
        # 轻量检查：只验证包可用，不在 GET /api/meta 里触发模型下载/加载
        extra = {
            "provider": "whisper",
            "model": self._settings.model,
            "device": self._settings.device,
            "compute_type": self._settings.compute_type,
            "loaded": self._model is not None,
        }
        try:
            import faster_whisper  # noqa: F401

            return HealthStatus(ok=True, extra=extra)
        except ImportError as exc:
            return HealthStatus(ok=False, detail=str(exc), extra=extra)

    async def transcribe(self, audio: bytes, *, session_id: str = "") -> str:
        pcm = load_user_pcm16(audio)
        if not pcm:
            return ""
        watch = Stopwatch()
        try:
            model = await self._ensure_model()
            # faster-whisper 接受 numpy array；这里用 bytes → int16 → float32
            import numpy as np

            samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            segments, _info = await asyncio.to_thread(
                model.transcribe,
                samples,
                language=self._settings.language,
                beam_size=self._settings.beam_size,
                vad_filter=True,
            )
            text = "".join(seg.text for seg in segments).strip()
            self._comm(session_id, "transcribe", watch.elapsed_ms(), chars=len(text))
            return text
        except Exception as exc:  # noqa: BLE001
            self._comm(session_id, "transcribe", watch.elapsed_ms(), status="error", error=str(exc))
            raise WhisperError(f"Whisper 转写失败：{exc}") from exc

    def _comm(self, session_id: str, action: str, took_ms: int, **extra: Any) -> None:
        if self._debug and session_id:
            self._debug.comm(session_id, target="whisper", action=action, took_ms=took_ms, **extra)


class NullASR:
    async def health(self) -> HealthStatus:
        return HealthStatus(ok=False, detail="未配置 ASR")

    async def transcribe(self, audio: bytes, *, session_id: str = "") -> str:
        raise WhisperError("ASR 未启用")


def build_asr(settings: WhisperSettings, debug: DebugEmitter | None = None) -> WhisperASR | NullASR:
    if not settings.enabled:
        return NullASR()
    return WhisperASR(settings, debug=debug)
