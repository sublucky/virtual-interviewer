from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# --------------------------------------------------------------------------
# 会话
# --------------------------------------------------------------------------

InterviewStyle = Literal["gentle", "probe", "system"]


class SessionState(str, Enum):
    """会话状态机节点，流转规则见 SessionStateMachine.TRANSITIONS。"""

    CREATED = "Created"
    OPENING = "Opening"
    LISTENING = "Listening"
    THINKING = "Thinking"
    SPEAKING = "Speaking"
    CLOSING = "Closing"
    EVALUATING = "Evaluating"
    DONE = "Done"


class InterviewConfig(BaseModel):
    role: str = Field(min_length=1, max_length=80)
    company: str = ""
    jd: str = ""
    resume: str = ""
    style: InterviewStyle = "probe"
    rounds: int = Field(default=8, ge=4, le=16)
    debug: bool | None = None


class Message(BaseModel):
    # control 消息只进 LLM 上下文，不对候选人展示
    role: Literal["system", "user", "assistant", "control"]
    content: str
    created_at: str = Field(default_factory=utc_now)


# --------------------------------------------------------------------------
# 语料
# --------------------------------------------------------------------------

CorpusKind = Literal["question", "rubric", "knowledge", "case"]
CorpusSource = Literal["manual", "agent", "import"]
CorpusStatus = Literal["draft", "active", "disabled"]


class CorpusEntry(BaseModel):
    id: str
    kind: CorpusKind
    role: str
    tags: list[str] = Field(default_factory=list)
    content: str
    reference_answer: str | None = None
    rubric: str | None = None
    source: CorpusSource = "manual"
    status: CorpusStatus = "active"
    version: int = 1
    updated_at: str = Field(default_factory=utc_now)

    def as_payload(self) -> dict[str, Any]:
        return self.model_dump()


class CorpusHit(BaseModel):
    entry: CorpusEntry
    score: float


# --------------------------------------------------------------------------
# 评估报告
# --------------------------------------------------------------------------

Recommendation = Literal["strong_hire", "hire", "lean_hire", "lean_no", "no_hire"]


class Dimension(BaseModel):
    name: str
    score: int = 0
    note: str = ""


class Evidence(BaseModel):
    quote: str = ""
    why: str = ""


class Report(BaseModel):
    overall: int = 0
    recommendation: Recommendation | str = "lean_no"
    level_guess: str = ""
    dimensions: list[Dimension] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    next_round_focus: list[str] = Field(default_factory=list)
    summary: str = ""


# --------------------------------------------------------------------------
# Debug 事件（架构 §3.13）
# --------------------------------------------------------------------------

DebugEventType = Literal["state_change", "retrieval", "comm", "latency", "debug_log"]


class DebugEvent(BaseModel):
    type: DebugEventType
    at: str = Field(default_factory=utc_now)
    data: dict[str, Any] = Field(default_factory=dict)

    def as_sse(self) -> dict[str, Any]:
        return {"type": self.type, "at": self.at, **self.data}


class HealthStatus(BaseModel):
    ok: bool
    detail: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)
