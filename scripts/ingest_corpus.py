"""RAG 语料入库：强制导入种子 + 用语料 Agent 生成 draft。

用法：
    # 直接走进程内组件（推荐，不依赖 HTTP）
    ./.venv/bin/python scripts/ingest_corpus.py

    # 或对已启动的服务发 HTTP（需服务在跑）
    ./.venv/bin/python scripts/ingest_corpus.py --http http://127.0.0.1:8090
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 主题清单：Agent 产出一律 draft，需人工审核后启用
AGENT_JOBS: list[tuple[str, str, int]] = [
    ("后端工程师", "分布式事务与一致性", 3),
    ("后端工程师", "可观测性与排障", 2),
    ("前端工程师", "渲染性能与体验指标", 3),
    ("算法工程师", "推荐系统评估与实验", 3),
    ("产品经理", "B 端需求优先级", 2),
    ("客户端工程师", "启动性能与包体积", 2),
]


async def ingest_local(*, activate_agent: bool) -> None:
    from server.config import settings
    from server.corpus.agent import CorpusAgent
    from server.corpus.manager import CorpusManager
    from server.providers.embedding import build_embedding
    from server.providers.llm import build_llm
    from server.rag.store import VectorStore
    from server.storage import Storage

    storage = Storage(settings.sqlite_path)
    store = VectorStore(settings.rag)
    embedding = build_embedding(
        settings.rag, llm_api_key=settings.llm.api_key, llm_api_base=settings.llm.api_base
    )
    corpus = CorpusManager(store=store, embedding=embedding, storage=storage)
    agent = CorpusAgent(build_llm(settings.llm))

    imported = await corpus.bootstrap(force=True)
    print(f"[seed] upsert {imported} 条（force）")

    drafted = 0
    draft_ids: list[str] = []
    for role, topic, count in AGENT_JOBS:
        existing = [row["id"] for row in corpus.list(role=role, kind="question", limit=30)]
        entries = await agent.generate(
            role=role, topic=topic, count=count, kind="question", existing=existing
        )
        if entries:
            await corpus.upsert(entries)
            drafted += len(entries)
            draft_ids.extend(e.id for e in entries)
            print(f"[agent] {role} / {topic} → {len(entries)} draft")

    if activate_agent and draft_ids:
        await corpus.set_status(draft_ids, "active")
        print(f"[agent] 已启用 {len(draft_ids)} 条（--activate）")

    stats = await corpus.stats()
    print(f"[stats] {stats}")
    by_role: dict[str, int] = {}
    for row in corpus.list(limit=10_000):
        by_role[row["role"]] = by_role.get(row["role"], 0) + 1
    print("[roles]", dict(sorted(by_role.items())))

    store.close()
    storage.close()


async def ingest_http(base: str, *, activate_agent: bool) -> None:
    import httpx

    async with httpx.AsyncClient(base_url=base.rstrip("/"), timeout=60.0) as client:
        r = await client.post("/api/corpus/bootstrap", params={"force": True})
        r.raise_for_status()
        print(f"[seed] {r.json()}")

        drafted = 0
        draft_ids: list[str] = []
        for role, topic, count in AGENT_JOBS:
            r = await client.post(
                "/api/corpus/agent",
                json={
                    "role": role,
                    "topic": topic,
                    "count": count,
                    "kind": "question",
                    "save_as_draft": True,
                },
            )
            r.raise_for_status()
            data = r.json()
            entries = data.get("entries") or []
            drafted += len(entries)
            draft_ids.extend(e["id"] for e in entries)
            print(f"[agent] {role} / {topic} → {len(entries)} draft")

        if activate_agent and draft_ids:
            r = await client.post(
                "/api/corpus/status", json={"ids": draft_ids, "status": "active"}
            )
            r.raise_for_status()
            print(f"[agent] 已启用 {len(draft_ids)} 条（--activate）")

        stats = (await client.get("/api/corpus/stats")).json()
        print(f"[stats] {stats}")


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG 语料入库")
    parser.add_argument("--http", help="已启动服务的 base URL，如 http://127.0.0.1:8090")
    parser.add_argument(
        "--activate",
        action="store_true",
        help="把本次 Agent 草稿直接启用为 active（默认保留 draft 待审）",
    )
    args = parser.parse_args()
    if args.http:
        asyncio.run(ingest_http(args.http, activate_agent=args.activate))
    else:
        asyncio.run(ingest_local(activate_agent=args.activate))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
