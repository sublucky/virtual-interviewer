"""Pipeline（架构 §3.2/§4）：把状态机、引擎、数字人、Debug 串成一轮交互。

首句优先：LLM 流式输出按句切分，第一句就推给数字人开口，
不等整段生成完，这是端到端延迟能压进预算的关键。
"""

from __future__ import annotations

import asyncio
import base64
import re
from collections.abc import AsyncIterator
from typing import Any

from server.debug import DebugEmitter, Stopwatch
from server.interview.engine import InterviewEngine
from server.interview.evaluator import Evaluator
from server.models import DebugEvent, SessionState
from server.providers.llm import LLMError
from server.providers.omni_realtime import OmniError
from server.providers.whisper_asr import WhisperError
from server.session import InterviewSession, SessionRepository

_SENTENCE_END = re.compile(r"[。！？!?；;\n]")
MIN_FLUSH_CHARS = 12
MAX_BUFFER_CHARS = 60

Event = tuple[str, dict[str, Any]]


class Pipeline:
    def __init__(
        self,
        *,
        sessions: SessionRepository,
        engine: InterviewEngine,
        evaluator: Evaluator,
        avatar: Any,
        debug: DebugEmitter,
        omni: Any = None,
        asr: Any = None,
        tts: Any = None,
        voice_mode: str = "text",
    ) -> None:
        self._sessions = sessions
        self._engine = engine
        self._evaluator = evaluator
        self._avatar = avatar
        self._omni = omni
        self._asr = asr
        self._tts = tts
        self._voice_mode = voice_mode
        self._debug = debug
        self._queues: dict[str, asyncio.Queue[DebugEvent]] = {}

    # -- 对外入口 ----------------------------------------------------------

    async def turn(
        self,
        session: InterviewSession,
        *,
        text: str | None = None,
        kickoff: bool = False,
        end: bool = False,
    ) -> AsyncIterator[Event]:
        async with session.lock:
            if end:
                async for event in self._closing(session):
                    yield event
                return

            if kickoff:
                self._sessions.transition(session, SessionState.OPENING, reason="kickoff")
                async for event in self._speak(session, self._engine.opening(session)):
                    yield event
                async for event in self._finish_listening(session, reason="opening_done"):
                    yield event
                return

            if text:
                self._sessions.add_message(session, "user", text)
                session.turns += 1

            if session.state is SessionState.LISTENING:
                self._sessions.transition(session, SessionState.THINKING, reason="answer_received")

            if session.rounds_exhausted:
                async for event in self._closing(session, auto=True):
                    yield event
                return

            async for event in self._speak(session, self._engine.next_turn(session)):
                yield event
            async for event in self._finish_listening(session, reason="turn_done"):
                yield event

    async def voice_turn(
        self,
        session: InterviewSession,
        *,
        audio: bytes,
        end: bool = False,
    ) -> AsyncIterator[Event]:
        """语音回合：ASR → LLM → TTS。

        ASR 用 Whisper（本地）或 Omni 转写，LLM 走 InterviewEngine（RAG + 流式），
        TTS 用 ChatTTS（本地）或 Omni 合成后送 LiveTalking /humanaudio 对口型。
        """
        if end:
            async for event in self.turn(session, end=True):
                yield event
            return
        asr = self._asr if self._asr is not None else self._omni
        if asr is None:
            yield "error", {"message": "语音引擎未配置"}
            return
        try:
            transcript = (await asr.transcribe(audio, session_id=session.id)).strip()
        except (OmniError, WhisperError) as exc:
            yield "error", {"message": f"语音转写失败：{exc}"}
            return
        except Exception as exc:  # noqa: BLE001
            yield "error", {"message": f"语音转写失败：{exc}"}
            return
        yield "transcript", {"text": transcript}
        if not transcript:
            yield "error", {"message": "没有识别到有效语音"}
            return
        async for event in self.turn(session, text=transcript):
            yield event

    def _back_to_listening(self, session: InterviewSession, *, reason: str) -> None:
        if session.can_transition(SessionState.LISTENING):
            self._sessions.transition(session, SessionState.LISTENING, reason=reason)

    async def _finish_listening(
        self, session: InterviewSession, *, reason: str
    ) -> AsyncIterator[Event]:
        """回到 Listening，并把本次流转的 Debug 事件挂回同一条 SSE。"""
        self._back_to_listening(session, reason=reason)
        for event in self._drain(session):
            yield event

    # -- 说话（流式 + 分句推送数字人）-------------------------------------

    async def _speak(
        self, session: InterviewSession, stream: AsyncIterator[tuple[str, str]]
    ) -> AsyncIterator[Event]:
        watch = Stopwatch()
        buffer = ""
        first_sentence_ms: int | None = None
        spoke_once = False

        try:
            async for kind, payload in stream:
                for event in self._drain(session):
                    yield event

                if kind == "thinking":
                    yield "thinking", {}
                    continue

                if kind == "delta":
                    if session.state in (SessionState.THINKING, SessionState.OPENING):
                        self._sessions.transition(
                            session, SessionState.SPEAKING, reason="first_token"
                        )
                    yield "delta", {"text": payload}
                    buffer += payload
                    sentence, buffer = _take_sentence(buffer)
                    if sentence:
                        if first_sentence_ms is None:
                            first_sentence_ms = watch.mark("first_sentence")
                        # 本轮第一次推送用 interrupt 打断上一轮残留音频
                        async for audio_event in self._push(
                            session, sentence, interrupt=not spoke_once
                        ):
                            yield audio_event
                        spoke_once = True
                    continue

                if kind == "done":
                    if buffer.strip():
                        async for audio_event in self._push(
                            session, buffer.strip(), interrupt=not spoke_once
                        ):
                            yield audio_event
                    self._debug.latency(
                        session.id,
                        first_sentence_ms=first_sentence_ms or -1,
                        speak_total_ms=watch.elapsed_ms(),
                    )
                    for event in self._drain(session):
                        yield event
                    yield "done", {"text": payload, "state": session.state.value}
        except LLMError as exc:
            self._debug.log(session.id, f"LLM 失败：{exc}")
            for event in self._drain(session):
                yield event
            yield "error", {"message": str(exc)}

    async def _push(
        self, session: InterviewSession, text: str, *, interrupt: bool
    ) -> AsyncIterator[Event]:
        """TTS → 数字人；无 RTC 时把 WAV 经 SSE 交给浏览器播放。"""
        wav: bytes | None = None
        if self._voice_mode == "omni":
            wav = await self._synthesize(session, text)

        if session.rtc_session_id:
            if wav is not None:
                if await self._deliver_avatar_audio(session, text, wav, interrupt=interrupt):
                    return
                # LiveTalking 推送失败：降级浏览器播放
                yield "assistant_audio", _audio_payload(wav, interrupt=interrupt)
                return
            await self._push_tts(session, text, interrupt=interrupt)
            return

        if wav is not None:
            self._debug.comm(
                session.id,
                target="browser",
                action="assistant_audio",
                took_ms=0,
                chars=len(text),
                bytes=len(wav),
            )
            yield "assistant_audio", _audio_payload(wav, interrupt=interrupt)

    async def _synthesize(self, session: InterviewSession, text: str) -> bytes | None:
        """按优先级合成：ChatTTS → Omni。"""
        for target, engine in (("chattts", self._tts), ("omni", self._omni)):
            if engine is None:
                continue
            watch = Stopwatch()
            try:
                wav = await engine.synthesize(text, session_id=session.id)
                self._debug.comm(
                    session.id,
                    target=target,
                    action="synthesize",
                    took_ms=watch.elapsed_ms(),
                    chars=len(text),
                    bytes=len(wav),
                )
                return wav
            except Exception as exc:  # noqa: BLE001
                self._debug.comm(
                    session.id,
                    target=target,
                    action="synthesize",
                    took_ms=watch.elapsed_ms(),
                    status="error",
                    error=str(exc),
                )
        return None

    async def _deliver_avatar_audio(
        self, session: InterviewSession, text: str, wav: bytes, *, interrupt: bool
    ) -> bool:
        watch = Stopwatch()
        try:
            await self._avatar.speak_audio(session.rtc_session_id, wav, interrupt=interrupt)
            self._debug.comm(
                session.id,
                target="livetalking",
                action="humanaudio",
                took_ms=watch.elapsed_ms(),
                chars=len(text),
                bytes=len(wav),
            )
            return True
        except Exception as exc:  # noqa: BLE001
            self._debug.comm(
                session.id,
                target="livetalking",
                action="humanaudio",
                took_ms=watch.elapsed_ms(),
                status="error",
                error=str(exc),
            )
            return False

    async def _push_tts(self, session: InterviewSession, text: str, *, interrupt: bool) -> None:
        watch = Stopwatch()
        try:
            await self._avatar.speak(session.rtc_session_id, text, interrupt=interrupt)
            self._debug.comm(
                session.id,
                target="livetalking",
                action="speak",
                took_ms=watch.elapsed_ms(),
                chars=len(text),
            )
        except Exception as exc:  # noqa: BLE001 — 数字人不可用时退化为文字面试
            self._debug.comm(
                session.id,
                target="livetalking",
                action="speak",
                took_ms=watch.elapsed_ms(),
                status="error",
                error=str(exc),
            )

    # -- 收尾与评估 --------------------------------------------------------

    async def _closing(
        self, session: InterviewSession, *, auto: bool = False
    ) -> AsyncIterator[Event]:
        reason = "rounds_exhausted" if auto else "user_end"
        if session.can_transition(SessionState.CLOSING):
            self._sessions.transition(session, SessionState.CLOSING, reason=reason)

        async for event in self._speak(session, self._engine.wrap_up(session)):
            yield event

        self._sessions.transition(session, SessionState.EVALUATING, reason="start_evaluate")
        for event in self._drain(session):
            yield event
        yield "evaluating", {}

        report = await self._evaluator.evaluate(session)
        self._sessions.save_report(session, report)
        self._sessions.transition(session, SessionState.DONE, reason="report_ready")
        for event in self._drain(session):
            yield event
        yield "report", report.model_dump()

    # -- Debug 事件搭车（ADR-12）-----------------------------------------

    def attach_debug(self, session_id: str) -> None:
        """开启 Debug 的会话，把事件挂到同一条 SSE 上，避免第二条长连接。"""
        if self._debug.is_enabled(session_id) and session_id not in self._queues:
            self._queues[session_id] = self._debug.subscribe(session_id)

    def detach_debug(self, session_id: str) -> None:
        queue = self._queues.pop(session_id, None)
        if queue is not None:
            self._debug.unsubscribe(session_id, queue)

    def _drain(self, session: InterviewSession) -> list[Event]:
        queue = self._queues.get(session.id)
        if queue is None:
            return []
        events: list[Event] = []
        while not queue.empty():
            events.append(("debug", queue.get_nowait().as_sse()))
        return events


def _take_sentence(buffer: str) -> tuple[str, str]:
    """从缓冲取出一个可播的完整句子，取不出返回 ('', buffer)。"""
    match = None
    for match in _SENTENCE_END.finditer(buffer):
        pass
    if match and match.end() >= MIN_FLUSH_CHARS:
        return buffer[: match.end()].strip(), buffer[match.end() :]
    if len(buffer) >= MAX_BUFFER_CHARS:
        cut = buffer.rfind("，", 0, MAX_BUFFER_CHARS)
        if cut > MIN_FLUSH_CHARS:
            return buffer[: cut + 1].strip(), buffer[cut + 1 :]
    return "", buffer


def _audio_payload(wav: bytes, *, interrupt: bool) -> dict[str, Any]:
    return {
        "format": "wav",
        "audio_b64": base64.b64encode(wav).decode("ascii"),
        "interrupt": interrupt,
        "bytes": len(wav),
    }
