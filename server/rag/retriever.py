"""Retriever（架构 §3.4）：检索与降级。

关键约束：检索在关键路径上，超时（默认 2s）或异常一律返回空结果，
由 prompt 里的兜底题库继续面试，绝不让 RAG 拖垮对话。
"""

from __future__ import annotations

import asyncio

from server.config import RAGSettings
from server.debug import DebugEmitter, Stopwatch
from server.models import CorpusHit
from server.providers.embedding import EmbeddingClient
from server.rag.store import VectorStore

MAX_SNIPPET = 400


class Retriever:
    def __init__(
        self,
        *,
        store: VectorStore,
        embedding: EmbeddingClient,
        settings: RAGSettings,
        debug: DebugEmitter,
    ) -> None:
        self._store = store
        self._embedding = embedding
        self._settings = settings
        self._debug = debug

    async def retrieve(
        self,
        *,
        session_id: str,
        query: str,
        role: str,
        kinds: list[str],
        limit: int,
        exclude_ids: list[str] | None = None,
    ) -> list[CorpusHit]:
        watch = Stopwatch()
        try:
            hits = await asyncio.wait_for(
                self._search(query, role, kinds, limit, exclude_ids),
                timeout=self._settings.search_timeout,
            )
        except asyncio.TimeoutError:
            self._debug.log(session_id, "检索超时，降级为无 RAG", query=query[:80])
            return []
        except Exception as exc:  # noqa: BLE001 — 向量库故障不应中断面试
            self._debug.log(session_id, f"检索失败，降级为无 RAG：{exc}", query=query[:80])
            return []

        self._debug.retrieval(
            session_id,
            query=query[:200],
            kinds=kinds,
            hits=[
                {"id": h.entry.id, "score": round(h.score, 4), "kind": h.entry.kind}
                for h in hits
            ],
            took_ms=watch.elapsed_ms(),
        )
        return hits

    async def _search(
        self,
        query: str,
        role: str,
        kinds: list[str],
        limit: int,
        exclude_ids: list[str] | None,
    ) -> list[CorpusHit]:
        vector = (await self._embedding.embed([query]))[0]
        # role 只作软过滤：先按岗位精确检索，不足时放开
        hits = await self._store.search(
            vector, limit=limit, kinds=kinds, role=role, exclude_ids=exclude_ids
        )
        if len(hits) < limit:
            seen = {h.entry.id for h in hits}
            extra = await self._store.search(
                vector, limit=limit, kinds=kinds, exclude_ids=exclude_ids
            )
            hits += [h for h in extra if h.entry.id not in seen]
        return hits[:limit]

    # -- 查询构造（架构 §3.4）--------------------------------------------

    @staticmethod
    def opening_query(role: str, jd: str, resume: str) -> str:
        return " ".join(filter(None, [role, jd[:300], resume[:300]]))

    @staticmethod
    def followup_query(last_answer: str, question: str) -> str:
        return f"{question[:150]} {last_answer[:350]}".strip()


def format_context(hits: list[CorpusHit]) -> str:
    """把命中语料拼成 prompt 片段，标注来源 id 便于报告溯源。"""
    if not hits:
        return ""
    lines: list[str] = []
    for hit in hits:
        entry = hit.entry
        body = entry.content.strip()[:MAX_SNIPPET]
        line = f"- [{entry.kind}|{entry.id}] {body}"
        if entry.rubric:
            line += f"\n  评分要点：{entry.rubric.strip()[:MAX_SNIPPET]}"
        if entry.reference_answer:
            line += f"\n  参考答案要点：{entry.reference_answer.strip()[:MAX_SNIPPET]}"
        lines.append(line)
    return "\n".join(lines)
