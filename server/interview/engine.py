"""InterviewEngine（架构 §3.4）：一轮问答的编排。

职责边界：只负责「检索 → 拼 prompt → 流式产出口播文本」，
状态机流转与数字人推送由 pipeline 负责。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from server.config import RAGSettings
from server.debug import DebugEmitter, Stopwatch
from server.interview import prompts
from server.providers.llm import LLMClient
from server.rag.retriever import Retriever, format_context
from server.session import InterviewSession


class InterviewEngine:
    def __init__(
        self,
        *,
        llm: LLMClient,
        retriever: Retriever,
        rag: RAGSettings,
        debug: DebugEmitter,
    ) -> None:
        self._llm = llm
        self._retriever = retriever
        self._rag = rag
        self._debug = debug

    # -- 开场 --------------------------------------------------------------

    async def opening(self, session: InterviewSession) -> AsyncIterator[tuple[str, str]]:
        config = session.config
        hits = await self._retriever.retrieve(
            session_id=session.id,
            query=Retriever.opening_query(config.role, config.jd, config.resume),
            role=config.role,
            kinds=["question", "knowledge", "rubric"],
            limit=self._rag.top_k_opening,
        )
        session.remember_corpus([h.entry.id for h in hits])
        session.add_message("system", prompts.system_prompt(config, context=format_context(hits)))
        session.add_message("control", prompts.KICKOFF)
        async for item in self._stream(session):
            yield item

    # -- 后续轮次 ----------------------------------------------------------

    async def next_turn(self, session: InterviewSession) -> AsyncIterator[tuple[str, str]]:
        last_answer = next(
            (m.content for m in reversed(session.messages) if m.role == "user"), ""
        )
        last_question = next(
            (m.content for m in reversed(session.messages) if m.role == "assistant"), ""
        )
        hits = await self._retriever.retrieve(
            session_id=session.id,
            query=Retriever.followup_query(last_answer, last_question),
            role=session.config.role,
            kinds=["question", "rubric"],
            limit=self._rag.top_k_followup,
            exclude_ids=session.asked_corpus_ids,
        )
        session.remember_corpus([h.entry.id for h in hits])
        instruction = prompts.CONTINUE
        if hits:
            instruction += "\n\n" + prompts.REFRESH_CONTEXT.format(context=format_context(hits))
        session.add_message("control", instruction)
        async for item in self._stream(session):
            yield item

    # -- 收尾 --------------------------------------------------------------

    async def wrap_up(self, session: InterviewSession) -> AsyncIterator[tuple[str, str]]:
        session.add_message("control", prompts.WRAP_UP)
        async for item in self._stream(session):
            yield item

    # -- 公共流式逻辑 ------------------------------------------------------

    async def _stream(self, session: InterviewSession) -> AsyncIterator[tuple[str, str]]:
        watch = Stopwatch()
        first_token_ms: int | None = None
        chunks: list[str] = []

        async for kind, text in self._llm.stream(session.llm_messages()):
            if kind == "thinking":
                yield "thinking", ""
                continue
            if first_token_ms is None:
                first_token_ms = watch.mark("llm_first_token")
                self._debug.comm(
                    session.id, target="llm", action="first_token", took_ms=first_token_ms
                )
            chunks.append(text)
            yield "delta", text

        reply = "".join(chunks).strip()
        if reply:
            session.add_message("assistant", reply)
        self._debug.latency(
            session.id,
            llm_first_token_ms=first_token_ms or -1,
            llm_total_ms=watch.elapsed_ms(),
            chars=len(reply),
        )
        yield "done", reply
