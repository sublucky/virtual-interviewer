"""Qwen3-Omni Realtime 客户端（vLLM-Omni /v1/realtime + chat 音频备用）。"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import websockets

from server.audio import load_user_pcm16, pcm16_to_wav
from server.config import OmniSettings
from server.debug import DebugEmitter, Stopwatch
from server.models import HealthStatus

_INPUT_RATE = 16000
_OUTPUT_RATE = 24000
_CHUNK_BYTES = 16000 * 2 // 5  # 200ms


class OmniError(RuntimeError):
    pass


class QwenOmniRealtime:
    """RealtimeVoiceEngine：上行 PCM16 16kHz，下行转写 + 24kHz PCM。"""

    def __init__(self, settings: OmniSettings, debug: DebugEmitter | None = None) -> None:
        self._settings = settings
        self._debug = debug
        self._http = httpx.AsyncClient(base_url=settings.http_base, timeout=settings.timeout)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def health(self) -> HealthStatus:
        extra = {
            "base": self._settings.http_base,
            "model": self._settings.model,
            "voice_mode": self._settings.voice_mode,
            "realtime": self._settings.realtime_url,
        }
        try:
            resp = await self._http.get("/v1/models", timeout=3.0)
            resp.raise_for_status()
            return HealthStatus(ok=True, extra=extra)
        except Exception as exc:  # noqa: BLE001 — 健康检查必须吞掉连接错误
            return HealthStatus(ok=False, detail=str(exc), extra=extra)

    async def transcribe(self, audio: bytes, *, session_id: str = "") -> str:
        """整段录音 → 用户转写。优先 Realtime，失败则 chat+audio 输入。"""
        pcm = load_user_pcm16(audio)
        if not pcm:
            return ""
        watch = Stopwatch()
        try:
            text = await self._realtime_transcribe(pcm)
            self._comm(session_id, "transcribe", watch.elapsed_ms(), chars=len(text))
            return text
        except Exception as first:  # noqa: BLE001
            self._comm(session_id, "transcribe", watch.elapsed_ms(), status="retry", error=str(first))
            text = await self._chat_transcribe(pcm)
            self._comm(session_id, "transcribe_chat", watch.elapsed_ms(), chars=len(text))
            return text

    async def synthesize(self, text: str, *, session_id: str = "", instructions: str = "") -> bytes:
        """口播文本 → WAV（chat/completions modalities text+audio）。"""
        watch = Stopwatch()
        payload: dict[str, Any] = {
            "model": self._settings.model,
            "messages": [
                {
                    "role": "system",
                    "content": instructions
                    or "你是面试官。请用自然口语完整读出用户给出的文本，不要增删内容，不要解释。",
                },
                {"role": "user", "content": text},
            ],
            "modalities": ["text", "audio"],
            "audio": {"voice": self._settings.speaker, "format": "wav"},
        }
        try:
            resp = await self._http.post("/v1/chat/completions", json=payload)
            resp.raise_for_status()
            wav = _extract_audio_wav(resp.json())
            self._comm(session_id, "synthesize", watch.elapsed_ms(), bytes=len(wav), chars=len(text))
            return wav
        except Exception as exc:  # noqa: BLE001
            self._comm(session_id, "synthesize", watch.elapsed_ms(), status="error", error=str(exc))
            raise OmniError(f"Omni 合成失败：{exc}") from exc

    async def converse(self, audio: bytes, *, instructions: str = "", session_id: str = "") -> tuple[str, str, bytes]:
        """一次 Realtime：用户转写 + 助手文本 + 24k PCM。"""
        pcm = load_user_pcm16(audio)
        events = await self._realtime_round(pcm, instructions=instructions)
        wav = pcm16_to_wav(events["audio_pcm"], events.get("sample_rate") or _OUTPUT_RATE)
        self._comm(session_id, "converse", 0, user=len(events["user_transcript"]))
        return events["user_transcript"], events["assistant_text"], wav

    # -- Realtime ----------------------------------------------------------

    async def _realtime_transcribe(self, pcm: bytes) -> str:
        events = await self._realtime_round(
            pcm,
            instructions="你是转写引擎。只输出候选人语音的逐字转写，不要回答、不要提问。",
        )
        text = events["user_transcript"] or events["assistant_text"]
        if not text.strip():
            raise OmniError("Realtime 未返回转写")
        return text.strip()

    async def _realtime_round(self, pcm: bytes, *, instructions: str = "") -> dict[str, Any]:
        user_parts: list[str] = []
        user_final = ""
        assistant_parts: list[str] = []
        audio_parts: list[bytes] = []
        sample_rate = _OUTPUT_RATE

        async with websockets.connect(self._settings.realtime_url, max_size=64 * 1024 * 1024) as ws:
            await ws.send(
                json.dumps(
                    {
                        "type": "session.update",
                        "model": self._settings.model,
                        "voice": self._settings.speaker,
                        "instructions": instructions,
                    }
                )
            )
            await ws.send(json.dumps({"type": "input_audio_buffer.commit", "final": False}))
            for i in range(0, len(pcm), _CHUNK_BYTES):
                chunk = pcm[i : i + _CHUNK_BYTES]
                await ws.send(
                    json.dumps(
                        {
                            "type": "input_audio_buffer.append",
                            "audio": base64.b64encode(chunk).decode("ascii"),
                        }
                    )
                )
            await ws.send(json.dumps({"type": "input_audio_buffer.commit", "final": True}))

            while True:
                raw = await ws.recv()
                if isinstance(raw, bytes):
                    continue
                event = json.loads(raw)
                kind = event.get("type") or ""

                if kind == "error":
                    raise OmniError(str(event))

                if kind in (
                    "transcription.delta",
                    "conversation.item.input_audio_transcription.delta",
                ):
                    delta = event.get("delta") or event.get("text") or ""
                    if delta:
                        user_parts.append(str(delta))
                    continue

                if kind in (
                    "transcription.done",
                    "conversation.item.input_audio_transcription.completed",
                ):
                    user_final = str(event.get("text") or event.get("transcript") or "")
                    continue

                if kind in ("response.audio_transcript.delta", "response.text.delta"):
                    delta = event.get("delta") or ""
                    if delta:
                        assistant_parts.append(str(delta))
                    continue

                if kind == "response.audio_transcript.done":
                    if event.get("text"):
                        assistant_parts = [str(event["text"])]
                    continue

                if kind == "response.audio.delta":
                    sr = event.get("sample_rate_hz") or event.get("sample_rate")
                    if isinstance(sr, int) and sr > 0:
                        sample_rate = sr
                    b64 = event.get("audio") or event.get("delta") or ""
                    if b64:
                        audio_parts.append(base64.b64decode(b64))
                    continue

                if kind in (
                    "response.audio.done",
                    "response.done",
                    "conversation.item.completed",
                ):
                    break

        return {
            "user_transcript": (user_final or "".join(user_parts)).strip(),
            "assistant_text": "".join(assistant_parts).strip(),
            "audio_pcm": b"".join(audio_parts),
            "sample_rate": sample_rate,
        }

    async def _chat_transcribe(self, pcm: bytes) -> str:
        wav_b64 = base64.b64encode(pcm16_to_wav(pcm, _INPUT_RATE)).decode("ascii")
        resp = await self._http.post(
            "/v1/chat/completions",
            json={
                "model": self._settings.model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_audio",
                                "input_audio": {"data": wav_b64, "format": "wav"},
                            },
                            {"type": "text", "text": "请只输出这段语音的逐字转写，不要回答。"},
                        ],
                    }
                ],
                "modalities": ["text"],
            },
        )
        resp.raise_for_status()
        text = _extract_text(resp.json()).strip()
        if not text:
            raise OmniError("chat 转写为空")
        return text

    def _comm(self, session_id: str, action: str, took_ms: int, **extra: Any) -> None:
        if self._debug and session_id:
            self._debug.comm(session_id, target="omni", action=action, took_ms=took_ms, **extra)


class NullOmni:
    async def aclose(self) -> None:
        return None

    async def health(self) -> HealthStatus:
        return HealthStatus(ok=False, detail="未配置 Omni")

    async def transcribe(self, audio: bytes, *, session_id: str = "") -> str:
        raise OmniError("Omni 未启用")

    async def synthesize(self, text: str, *, session_id: str = "", instructions: str = "") -> bytes:
        raise OmniError("Omni 未启用")


def build_omni(settings: OmniSettings, debug: DebugEmitter | None = None) -> QwenOmniRealtime | NullOmni:
    if not settings.api_base:
        return NullOmni()
    return QwenOmniRealtime(settings, debug=debug)


def _extract_text(body: dict[str, Any]) -> str:
    choice = (body.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") in ("text", "output_text"):
                parts.append(str(item.get("text") or ""))
        return "".join(parts)
    return str(choice.get("text") or "")


def _extract_audio_wav(body: dict[str, Any]) -> bytes:
    choice = (body.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    audio = message.get("audio") or choice.get("audio") or body.get("audio")
    raw_b64 = ""
    rate = _OUTPUT_RATE
    if isinstance(audio, dict):
        raw_b64 = str(audio.get("data") or audio.get("b64") or "")
        rate = int(audio.get("sample_rate") or audio.get("sample_rate_hz") or rate)
    elif isinstance(audio, str):
        raw_b64 = audio
    if not raw_b64 and isinstance(message.get("content"), list):
        for item in message["content"]:
            if isinstance(item, dict) and item.get("type") in ("audio", "output_audio"):
                raw_b64 = str(item.get("data") or (item.get("audio") or {}).get("data") or "")
                break
    if not raw_b64:
        raise OmniError("响应中没有音频")
    blob = base64.b64decode(raw_b64)
    if len(blob) >= 12 and blob[:4] == b"RIFF":
        return blob
    return pcm16_to_wav(blob, rate)
