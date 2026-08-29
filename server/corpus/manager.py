"""CorpusManager（架构 §3.5）：语料 CRUD、状态机、种子导入。

写入双写：Qdrant 存向量+正文（供检索），SQLite 存元数据（供后台列表）。
状态机：draft --启用--> active --停用--> disabled --启用--> active
只有 active 参与检索；Agent 产出强制落 draft，必须人工确认才生效。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import yaml

from server.models import CorpusEntry, CorpusStatus, utc_now
from server.providers.embedding import EmbeddingClient
from server.rag.store import VectorStore
from server.storage import Storage

SEED_DIR = Path(__file__).parent / "seed"

ALLOWED_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"active", "disabled"},
    "active": {"disabled"},
    "disabled": {"active"},
}


class CorpusError(RuntimeError):
    pass


class CorpusManager:
    def __init__(
        self,
        *,
        store: VectorStore,
        embedding: EmbeddingClient,
        storage: Storage,
    ) -> None:
        self._store = store
        self._embedding = embedding
        self._storage = storage

    # -- 写入 --------------------------------------------------------------

    async def upsert(self, entries: list[CorpusEntry]) -> int:
        if not entries:
            return 0
        vectors = await self._embedding.embed([self._embed_text(e) for e in entries])
        await self._store.upsert(entries, vectors)
        self._storage.upsert_corpus_meta(entries)
        return len(entries)

    @staticmethod
    def _embed_text(entry: CorpusEntry) -> str:
        """把岗位、标签一起编码，让检索能区分同题不同岗位的语境。"""
        parts = [entry.role, " ".join(entry.tags), entry.content]
        if entry.rubric:
            parts.append(entry.rubric)
        return "\n".join(p for p in parts if p)

    async def set_status(self, ids: list[str], status: CorpusStatus) -> None:
        current = {row["id"]: row["status"] for row in self._storage.list_corpus_meta(limit=10_000)}
        for cid in ids:
            now = current.get(cid)
            if now is None:
                raise CorpusError(f"语料不存在：{cid}")
            if now == status:
                continue
            if status not in ALLOWED_STATUS_TRANSITIONS.get(now, set()):
                raise CorpusError(f"状态流转不合法：{cid} {now} -> {status}")
        await self._store.set_status(ids, status)
        self._storage.set_corpus_status(ids, status)

    async def delete(self, ids: list[str]) -> None:
        await self._store.delete(ids)
        self._storage.set_corpus_status(ids, "disabled")

    # -- 查询 --------------------------------------------------------------

    def list(
        self,
        *,
        role: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        return self._storage.list_corpus_meta(role=role, kind=kind, status=status, limit=limit)

    async def stats(self) -> dict[str, Any]:
        return {"by_status": self._storage.corpus_stats(), "vectors": await self._store.count()}

    # -- 种子导入 ----------------------------------------------------------

    async def bootstrap(self, *, force: bool = False) -> int:
        """冷启动导入种子语料；已有数据则跳过（除非 force）。"""
        await self._store.ensure_collection()
        if not force and await self._store.count() > 0:
            return 0
        entries = load_seed_entries()
        return await self.upsert(entries)


def load_seed_entries(seed_dir: Path = SEED_DIR) -> list[CorpusEntry]:
    entries: list[CorpusEntry] = []
    for path in sorted(seed_dir.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        defaults = raw.get("defaults") or {}
        for item in raw.get("entries") or []:
            merged = {**defaults, **item}
            merged.setdefault("id", f"seed-{path.stem}-{uuid.uuid4().hex[:8]}")
            merged.setdefault("source", "import")
            merged.setdefault("status", "active")
            merged["updated_at"] = utc_now()
            entries.append(CorpusEntry(**merged))
    return entries
