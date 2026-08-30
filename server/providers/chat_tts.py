"""ChatTTS：本地中文 TTS（2Noise/ChatTTS）。

文本 → 24kHz WAV，供 LiveTalking /humanaudio 对口型。
模型懒加载（torch，CPU 可跑但慢，GPU/MPS 更佳），首次合成时才下载权重。
音色用 spk_emb 固定：首次随机采样后落盘 assets/audio/，后续复用同一音色。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from server.audio import pcm16_to_wav
from server.config import ROOT, ChatTTSSettings
from server.debug import DebugEmitter, Stopwatch
from server.models import HealthStatus

_SAMPLE_RATE = 24000


class ChatTTSError(RuntimeError):
    pass


class ChatTTSEngine:
    """TTSEngine：本地 ChatTTS 合成。"""

    def __init__(self, settings: ChatTTSSettings, debug: DebugEmitter | None = None) -> None:
        self._settings = settings
        self._debug = debug
        self._chat: Any = None
        self._spk_emb: str | None = None
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        self._chat = None

    async def health(self) -> HealthStatus:
        # 轻量检查：只验证包可用，不在 GET /api/meta 里触发数 GB 的模型下载
        extra = {
            "provider": "chattts",
            "source": self._settings.source,
            "speaker_emb": self._settings.speaker_emb,
            "loaded": self._chat is not None,
        }
        try:
            import ChatTTS  # noqa: F401

            return HealthStatus(ok=True, extra=extra)
        except ImportError as exc:
            return HealthStatus(ok=False, detail=f"缺少 ChatTTS：{exc}", extra=extra)

    async def synthesize(self, text: str, *, session_id: str = "", instructions: str = "") -> bytes:
        text = text.strip()
        if not text:
            return b""
        watch = Stopwatch()
        try:
            chat = await self._ensure_model()
            wav = await asyncio.to_thread(self._infer, chat, text)
            self._comm(session_id, "synthesize", watch.elapsed_ms(), bytes=len(wav), chars=len(text))
            return wav
        except ChatTTSError as exc:
            self._comm(session_id, "synthesize", watch.elapsed_ms(), status="error", error=str(exc))
            raise
        except Exception as exc:  # noqa: BLE001
            self._comm(session_id, "synthesize", watch.elapsed_ms(), status="error", error=str(exc))
            raise ChatTTSError(f"ChatTTS 合成失败：{exc}") from exc

    async def interrupt(self) -> None:
        # 本地推理无法中途取消；LiveTalking 侧的打断由 avatar.interrupt 负责
        return None

    # -- 模型与音色 --------------------------------------------------------

    async def _ensure_model(self) -> Any:
        if self._chat is not None:
            return self._chat
        async with self._lock:
            if self._chat is not None:
                return self._chat
            self._chat, self._spk_emb = await asyncio.to_thread(self._load_model)
            return self._chat

    def _load_model(self) -> tuple[Any, str]:
        try:
            import ChatTTS
        except ImportError as exc:
            raise ChatTTSError("缺少 ChatTTS，请 pip install ChatTTS") from exc
        chat = ChatTTS.Chat()
        kwargs: dict[str, Any] = {"compile": self._settings.compile}
        if self._settings.source == "local":
            kwargs.update(source="local", local_path=self._settings.local_path)
        else:
            kwargs.update(source="huggingface")
        load = getattr(chat, "load", None) or getattr(chat, "load_models")
        try:
            if self._settings.device:
                load(device=self._settings.device, **kwargs)
            else:
                load(**kwargs)
        except TypeError:
            # 旧版没有 device/source 参数，退回最简调用
            load()
        return chat, self._load_speaker(chat)

    def _load_speaker(self, chat: Any) -> str:
        emb_path = Path(self._settings.speaker_emb).expanduser()
        if not emb_path.is_absolute():
            emb_path = ROOT / emb_path
        if emb_path.is_file():
            return emb_path.read_text(encoding="utf-8").strip()
        spk_emb = chat.sample_random_speaker()
        emb_path.parent.mkdir(parents=True, exist_ok=True)
        emb_path.write_text(spk_emb, encoding="utf-8")
        return spk_emb

    # -- 推理 --------------------------------------------------------------

    def _infer(self, chat: Any, text: str) -> bytes:
        import numpy as np

        params: Any
        try:
            import ChatTTS as chattts_mod

            params = chattts_mod.Chat.InferCodeParams(
                spk_emb=self._spk_emb,
                temperature=self._settings.temperature,
            )
        except (AttributeError, TypeError):
            params = {"spk_emb": self._spk_emb, "temperature": self._settings.temperature}
        try:
            wavs = chat.infer(
                [text],
                params_infer_code=params,
                skip_refine_text=self._settings.skip_refine,
            )
        except TypeError:
            wavs = chat.infer([text], params_infer_code=params)
        if not wavs:
            raise ChatTTSError("ChatTTS 未返回音频")
        samples = np.clip(np.asarray(wavs[0], dtype=np.float32).reshape(-1), -1.0, 1.0)
        pcm = (samples * 32767.0).astype(np.int16).tobytes()
        return pcm16_to_wav(pcm, _SAMPLE_RATE)

    def _comm(self, session_id: str, action: str, took_ms: int, **extra: Any) -> None:
        if self._debug and session_id:
            self._debug.comm(session_id, target="chattts", action=action, took_ms=took_ms, **extra)


class NullTTS:
    async def aclose(self) -> None:
        return None

    async def health(self) -> HealthStatus:
        return HealthStatus(ok=False, detail="未配置 TTS")

    async def synthesize(self, text: str, *, session_id: str = "", instructions: str = "") -> bytes:
        raise ChatTTSError("ChatTTS 未启用")

    async def interrupt(self) -> None:
        return None


def build_tts(settings: ChatTTSSettings, debug: DebugEmitter | None = None) -> ChatTTSEngine | NullTTS:
    if not settings.enabled:
        return NullTTS()
    return ChatTTSEngine(settings, debug=debug)
