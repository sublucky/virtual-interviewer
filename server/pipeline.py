"""Pipeline（架构 §3.2/§4）：把状态机、引擎、数字人、Debug 串成一轮交互。

首句优先：LLM 流式输出按句切分，第一句就推给数字人开口，
不等整段生成完，这是端到端延迟能压进预算的关键。
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from typing import Any

from server.debug import DebugEmitter, Stopwatch
from server.interview.engine import InterviewEngine
from server.interview.evaluator import Evaluator
from server.models import DebugEvent, SessionState
from server.providers.llm import LLMError
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
    ) -> None:
        self._sessions = sessions
        self._engine = engine
        self._evaluator = evaluator
        self._avatar = avatar
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
                        await self._push(session, sentence, interrupt=not spoke_once)
                        spoke_once = True
                    continue

                if kind == "done":
                    if buffer.strip():
                        await self._push(session, buffer.strip(), interrupt=not spoke_once)
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

    async def _push(self, session: InterviewSession, text: str, *, interrupt: bool) -> None:
        """把一句话推给数字人。推送失败降级为纯文本面试，不中断会话。"""
        if not session.rtc_session_id:
            return
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
