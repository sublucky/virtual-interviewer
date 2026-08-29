"""会话状态机与会话仓库（架构 §3.3）。

状态流转显式声明，非法流转直接抛错，禁止隐式跳转。
"""

from __future__ import annotations

import asyncio
import uuid

from server.debug import DebugEmitter
from server.models import (
    InterviewConfig,
    Message,
    Report,
    SessionState,
    utc_now,
)
from server.storage import Storage

TRANSITIONS: dict[SessionState, set[SessionState]] = {
    SessionState.CREATED: {SessionState.OPENING},
    SessionState.OPENING: {SessionState.SPEAKING, SessionState.LISTENING, SessionState.CLOSING},
    SessionState.LISTENING: {SessionState.THINKING, SessionState.CLOSING},
    SessionState.THINKING: {SessionState.SPEAKING, SessionState.LISTENING, SessionState.CLOSING},
    SessionState.SPEAKING: {SessionState.LISTENING, SessionState.CLOSING},
    SessionState.CLOSING: {SessionState.EVALUATING},
    SessionState.EVALUATING: {SessionState.DONE},
    SessionState.DONE: set(),
}


class IllegalTransition(RuntimeError):
    pass


class InterviewSession:
    def __init__(self, *, session_id: str, config: InterviewConfig) -> None:
        self.id = session_id
        self.config = config
        self.state = SessionState.CREATED
        self.messages: list[Message] = []
        self.turns = 0
        self.asked_corpus_ids: list[str] = []
        self.rtc_session_id: str | None = None
        self.report: Report | None = None
        self.created_at = utc_now()
        self.ended_at: str | None = None
        self.lock = asyncio.Lock()

    # -- 状态机 ------------------------------------------------------------

    def can_transition(self, to: SessionState) -> bool:
        return to in TRANSITIONS.get(self.state, set())

    def transition(self, to: SessionState, *, reason: str = "") -> tuple[SessionState, SessionState]:
        if not self.can_transition(to):
            raise IllegalTransition(f"{self.state.value} -> {to.value} 不是合法流转")
        previous, self.state = self.state, to
        if to is SessionState.DONE:
            self.ended_at = utc_now()
        return previous, to

    # -- 上下文 ------------------------------------------------------------

    def add_message(self, role: str, content: str) -> Message:
        message = Message(role=role, content=content)  # type: ignore[arg-type]
        self.messages.append(message)
        return message

    def llm_messages(self) -> list[dict[str, str]]:
        """control 消息以 user 身份进入 LLM，但不对候选人展示。"""
        return [
            {"role": "user" if m.role == "control" else m.role, "content": m.content}
            for m in self.messages
        ]

    def visible_messages(self) -> list[Message]:
        return [m for m in self.messages if m.role in {"user", "assistant"}]

    def remember_corpus(self, ids: list[str]) -> None:
        for cid in ids:
            if cid not in self.asked_corpus_ids:
                self.asked_corpus_ids.append(cid)

    @property
    def rounds_exhausted(self) -> bool:
        return self.turns >= self.config.rounds


class SessionRepository:
    """MVP 单并发：进程内字典 + 每会话锁；完整产品换 Redis。"""

    def __init__(self, *, storage: Storage, debug: DebugEmitter) -> None:
        self._sessions: dict[str, InterviewSession] = {}
        self._storage = storage
        self._debug = debug

    def create(self, config: InterviewConfig) -> InterviewSession:
        session = InterviewSession(session_id=uuid.uuid4().hex[:12], config=config)
        self._sessions[session.id] = session
        self._debug.register(session.id, config.debug)
        self._persist(session)
        return session

    def get(self, session_id: str) -> InterviewSession | None:
        return self._sessions.get(session_id)

    def transition(self, session: InterviewSession, to: SessionState, *, reason: str = "") -> None:
        previous, current = session.transition(to, reason=reason)
        self._debug.state_change(session.id, from_=previous.value, to=current.value, reason=reason)
        self._persist(session)

    def add_message(self, session: InterviewSession, role: str, content: str) -> None:
        message = session.add_message(role, content)
        self._storage.append_message(session.id, message)

    def save_report(self, session: InterviewSession, report: Report) -> None:
        session.report = report
        self._storage.save_report(session.id, report)
        self._persist(session)

    def _persist(self, session: InterviewSession) -> None:
        self._storage.upsert_session(
            session_id=session.id,
            config=session.config,
            state=session.state.value,
            turns=session.turns,
            asked_corpus_ids=session.asked_corpus_ids,
            created_at=session.created_at,
            ended_at=session.ended_at,
        )
