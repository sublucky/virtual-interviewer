"""VectorStore：Qdrant 封装（架构 §3.4 / ADR-08）。

QDRANT_URL 为空时使用内嵌本地模式（单进程、文件持久化），
本机开发无需起 Docker；生产填 URL 指向独立 Qdrant 服务。
Qdrant 客户端为同步实现，统一用 to_thread 包装避免阻塞事件循环。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from qdrant_client import QdrantClient, models as qm

from server.config import RAGSettings
from server.models import CorpusEntry, CorpusHit, HealthStatus

_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


def point_id(entry_id: str) -> str:
    """业务 id → 确定性 UUID，保证 upsert 幂等。"""
    return str(uuid.uuid5(_NAMESPACE, entry_id))


class VectorStore:
    def __init__(self, settings: RAGSettings) -> None:
        self._settings = settings
        self._collection = settings.collection
        if settings.qdrant_url:
            self._client = QdrantClient(url=settings.qdrant_url)
            self._mode = f"remote:{settings.qdrant_url}"
        else:
            settings.qdrant_path.mkdir(parents=True, exist_ok=True)
            self._client = QdrantClient(path=str(settings.qdrant_path))
            self._mode = f"embedded:{settings.qdrant_path}"

    @property
    def mode(self) -> str:
        return self._mode

    async def ensure_collection(self) -> None:
        await asyncio.to_thread(self._ensure_collection_sync)

    def _ensure_collection_sync(self) -> None:
        if self._client.collection_exists(self._collection):
            return
        self._client.create_collection(
            collection_name=self._collection,
            vectors_config=qm.VectorParams(
                size=self._settings.embedding_dim, distance=qm.Distance.COSINE
            ),
        )
        if not self._settings.qdrant_url:
            # 内嵌模式不支持 payload 索引，过滤走全量扫描（数据量小可接受）
            return
        for field in ("role", "kind", "status"):
            self._client.create_payload_index(
                collection_name=self._collection,
                field_name=field,
                field_schema=qm.PayloadSchemaType.KEYWORD,
            )

    async def upsert(self, entries: list[CorpusEntry], vectors: list[list[float]]) -> None:
        points = [
            qm.PointStruct(id=point_id(entry.id), vector=vector, payload=entry.as_payload())
            for entry, vector in zip(entries, vectors, strict=True)
        ]
        await asyncio.to_thread(
            self._client.upsert, collection_name=self._collection, points=points
        )

    async def search(
        self,
        vector: list[float],
        *,
        limit: int,
        kinds: list[str] | None = None,
        role: str | None = None,
        exclude_ids: list[str] | None = None,
        status: str = "active",
    ) -> list[CorpusHit]:
        must: list[Any] = [qm.FieldCondition(key="status", match=qm.MatchValue(value=status))]
        if kinds:
            must.append(qm.FieldCondition(key="kind", match=qm.MatchAny(any=list(kinds))))
        if role:
            must.append(qm.FieldCondition(key="role", match=qm.MatchValue(value=role)))
        must_not = (
            [qm.HasIdCondition(has_id=[point_id(i) for i in exclude_ids])] if exclude_ids else None
        )
        result = await asyncio.to_thread(
            self._client.query_points,
            collection_name=self._collection,
            query=vector,
            limit=limit,
            query_filter=qm.Filter(must=must, must_not=must_not),
            with_payload=True,
        )
        hits: list[CorpusHit] = []
        for point in result.points:
            if not point.payload:
                continue
            hits.append(CorpusHit(entry=CorpusEntry(**point.payload), score=float(point.score)))
        return hits

    async def set_status(self, entry_ids: list[str], status: str) -> None:
        await asyncio.to_thread(
            self._client.set_payload,
            collection_name=self._collection,
            payload={"status": status},
            points=[point_id(i) for i in entry_ids],
        )

    async def delete(self, entry_ids: list[str]) -> None:
        await asyncio.to_thread(
            self._client.delete,
            collection_name=self._collection,
            points_selector=qm.PointIdsList(points=[point_id(i) for i in entry_ids]),
        )

    async def count(self) -> int:
        result = await asyncio.to_thread(
            self._client.count, collection_name=self._collection, exact=True
        )
        return int(result.count)

    async def health(self) -> HealthStatus:
        try:
            total = await self.count()
            return HealthStatus(ok=True, extra={"mode": self._mode, "points": total})
        except Exception as exc:  # noqa: BLE001 — 向量库不可用时需降级而非崩溃
            return HealthStatus(ok=False, detail=str(exc), extra={"mode": self._mode})

    def close(self) -> None:
        self._client.close()
