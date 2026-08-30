"""骨架自检：用假 LLM 跑通「建会话 → 开场 → 答题 → 收尾 → 报告」全链路。

不依赖 GPU、LiveTalking 或真实模型，用于验证编排、状态机、RAG 与 Debug 是否自洽。
    ./.venv/bin/python scripts/smoke.py
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.config import RAGSettings  # noqa: E402
from server.corpus.manager import CorpusManager  # noqa: E402
from server.debug import DebugEmitter  # noqa: E402
from server.interview.engine import InterviewEngine  # noqa: E402
from server.interview.evaluator import Evaluator  # noqa: E402
from server.models import InterviewConfig  # noqa: E402
from server.pipeline import Pipeline  # noqa: E402
from server.providers.embedding import HashEmbedding  # noqa: E402
from server.rag.retriever import Retriever  # noqa: E402
from server.rag.store import VectorStore  # noqa: E402
from server.session import SessionRepository  # noqa: E402
from server.storage import Storage  # noqa: E402

REPLIES = [
    "你好，我是今天的面试官。我们大概聊四十分钟，先从项目开始。请先简单介绍一下你最近负责的系统。",
    "你提到订单表变慢，那当时的 QPS 和数据量具体是多少？",
    "明白了。那你为什么选择联合索引而不是分表？",
    "好的。最后请说说你在这个项目里最大的失误。",
]


class FakeLLM:
    """按脚本逐字吐字，模拟流式；complete_json 返回固定报告。"""

    def __init__(self) -> None:
        self._i = 0

    async def stream(self, messages: list[dict[str, str]], **_: Any) -> AsyncIterator[tuple[str, str]]:
        reply = REPLIES[min(self._i, len(REPLIES) - 1)]
        self._i += 1
        yield "thinking", ""
        for char in reply:
            await asyncio.sleep(0)
            yield "delta", char

    async def complete_json(self, messages: list[dict[str, str]], **_: Any) -> dict[str, Any]:
        return {
            "overall": 72,
            "recommendation": "lean_hire",
            "level_guess": "高级工程师",
            "dimensions": [{"name": "技术深度", "score": 4, "note": "索引原理讲得清楚"}],
            "strengths": ["能量化问题规模"],
            "risks": ["未覆盖分布式场景"],
            "evidence": [{"quote": "QPS 大概三千", "why": "说明有真实容量认知"}],
            "next_round_focus": ["分布式事务"],
            "summary": "基础扎实，深度尚可，建议进入下一轮。",
        }


class FakeAvatar:
    def __init__(self) -> None:
        self.spoken: list[str] = []

    async def speak(self, rtc_session_id: str, text: str, *, interrupt: bool = False) -> None:
        self.spoken.append(text)

    async def speak_audio(self, rtc_session_id: str, wav_bytes: bytes, *, interrupt: bool = False) -> None:
        self.spoken.append(f"<audio:{len(wav_bytes)}>")


async def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="vi-smoke-"))
    rag = RAGSettings(qdrant_url="", qdrant_path=workdir / "qdrant", embedding_dim=256)
    debug = DebugEmitter(default_enabled=True)
    storage = Storage(workdir / "smoke.db")
    store = VectorStore(rag)
    embedding = HashEmbedding(rag.embedding_dim)
    corpus = CorpusManager(store=store, embedding=embedding, storage=storage)

    imported = await corpus.bootstrap()
    print(f"[1/5] 种子语料导入：{imported} 条，向量数 {await store.count()}")

    retriever = Retriever(store=store, embedding=embedding, settings=rag, debug=debug)
    hits = await retriever.retrieve(
        session_id="probe", query="MySQL 索引优化 慢查询", role="后端工程师",
        kinds=["question"], limit=3,
    )
    print(f"[2/5] 检索命中 {len(hits)} 条：" + ", ".join(h.entry.id for h in hits))

    llm = FakeLLM()
    avatar = FakeAvatar()
    sessions = SessionRepository(storage=storage, debug=debug)
    pipeline = Pipeline(
        sessions=sessions,
        engine=InterviewEngine(llm=llm, retriever=retriever, rag=rag, debug=debug),  # type: ignore[arg-type]
        evaluator=Evaluator(llm=llm, retriever=retriever, rag=rag, debug=debug),  # type: ignore[arg-type]
        avatar=avatar,
        debug=debug,
    )

    session = sessions.create(
        InterviewConfig(role="后端工程师", company="示例科技", rounds=4, debug=True)
    )
    session.rtc_session_id = "fake-rtc"
    pipeline.attach_debug(session.id)

    states: list[str] = []
    async def run(**kwargs: Any) -> None:
        async for kind, data in pipeline.turn(session, **kwargs):
            if kind == "debug" and data.get("type") == "state_change":
                states.append(f"{data['from']}->{data['to']}")
            elif kind == "done":
                print(f"       面试官：{data['text'][:40]}…")

    await run(kickoff=True)
    print("[3/5] 开场完成")
    for answer in ["我负责订单系统的重构。", "QPS 大概三千，数据量两千万。", "分表改造成本太高。"]:
        await run(text=answer)
    print(f"[4/5] 已进行 {session.turns} 轮；数字人收到 {len(avatar.spoken)} 句")

    await run(end=True)
    report = session.report
    assert report is not None, "报告未生成"
    print(f"[5/5] 报告：{report.overall} 分 / {report.recommendation} / {report.summary[:20]}…")

    print("\n状态流转：" + " ".join(states))
    events = debug.history(session.id)
    kinds = {}
    for event in events:
        kinds[event.type] = kinds.get(event.type, 0) + 1
    print(f"Debug 事件：{kinds}（共 {len(events)} 条）")
    print(f"最终状态：{session.state.value}")

    store.close()
    storage.close()
    shutil.rmtree(workdir, ignore_errors=True)
    print("\n全链路自检通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
