"""语音编排自检：假 Omni 转写 + 合成，验证 transcript → RAG → /humanaudio 路径。

不连真实 vLLM-Omni。Omni 失败时应能降级到文字 speak。
    ./.venv/bin/python scripts/smoke_voice.py
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

from server.audio import pcm16_to_wav  # noqa: E402
from server.config import RAGSettings  # noqa: E402
from server.corpus.manager import CorpusManager  # noqa: E402
from server.debug import DebugEmitter  # noqa: E402
from server.interview.engine import InterviewEngine  # noqa: E402
from server.interview.evaluator import Evaluator  # noqa: E402
from server.models import InterviewConfig  # noqa: E402
from server.pipeline import Pipeline  # noqa: E402
from server.providers.embedding import HashEmbedding  # noqa: E402
from server.providers.omni_realtime import OmniError  # noqa: E402
from server.rag.retriever import Retriever  # noqa: E402
from server.rag.store import VectorStore  # noqa: E402
from server.session import SessionRepository  # noqa: E402
from server.storage import Storage  # noqa: E402


class FakeLLM:
    async def stream(self, messages: list[dict[str, str]], **_: Any) -> AsyncIterator[tuple[str, str]]:
        yield "thinking", ""
        yield "delta", "请介绍一下你最近的项目。"

    async def complete_json(self, messages: list[dict[str, str]], **_: Any) -> dict[str, Any]:
        return {
            "overall": 70,
            "recommendation": "lean_hire",
            "level_guess": "中级",
            "dimensions": [],
            "strengths": [],
            "risks": [],
            "evidence": [],
            "next_round_focus": [],
            "summary": "ok",
        }


class FakeAvatar:
    def __init__(self) -> None:
        self.spoken: list[str] = []
        self.audio: list[int] = []

    async def speak(self, rtc_session_id: str, text: str, *, interrupt: bool = False) -> None:
        self.spoken.append(text)

    async def speak_audio(self, rtc_session_id: str, wav_bytes: bytes, *, interrupt: bool = False) -> None:
        self.audio.append(len(wav_bytes))


class FakeOmni:
    async def transcribe(self, audio: bytes, *, session_id: str = "") -> str:
        assert audio, "应收到音频"
        return "我负责订单系统重构，QPS 大约三千。"

    async def synthesize(self, text: str, *, session_id: str = "", instructions: str = "") -> bytes:
        return pcm16_to_wav(b"\x00\x00" * 160, 24000)


class BrokenOmni:
    async def transcribe(self, audio: bytes, *, session_id: str = "") -> str:
        raise OmniError("down")

    async def synthesize(self, text: str, *, session_id: str = "", instructions: str = "") -> bytes:
        raise OmniError("down")


async def _pipeline(workdir: Path, omni: Any, voice_mode: str) -> tuple[Pipeline, Any, Any]:
    rag = RAGSettings(qdrant_url="", qdrant_path=workdir / "qdrant", embedding_dim=256)
    debug = DebugEmitter(default_enabled=True)
    storage = Storage(workdir / "smoke.db")
    store = VectorStore(rag)
    embedding = HashEmbedding(rag.embedding_dim)
    corpus = CorpusManager(store=store, embedding=embedding, storage=storage)
    await corpus.bootstrap()
    retriever = Retriever(store=store, embedding=embedding, settings=rag, debug=debug)
    llm = FakeLLM()
    avatar = FakeAvatar()
    sessions = SessionRepository(storage=storage, debug=debug)
    pipeline = Pipeline(
        sessions=sessions,
        engine=InterviewEngine(llm=llm, retriever=retriever, rag=rag, debug=debug),  # type: ignore[arg-type]
        evaluator=Evaluator(llm=llm, retriever=retriever, rag=rag, debug=debug),  # type: ignore[arg-type]
        avatar=avatar,
        debug=debug,
        omni=omni,
        voice_mode=voice_mode,
    )
    return pipeline, sessions, avatar


async def main() -> int:
    wav = pcm16_to_wav(b"\x01\x00" * 320, 16000)

    workdir = Path(tempfile.mkdtemp(prefix="vi-voice-"))
    try:
        pipeline, sessions, avatar = await _pipeline(workdir, FakeOmni(), "omni")
        session = sessions.create(InterviewConfig(role="后端工程师", rounds=4, debug=True))
        session.rtc_session_id = "fake-rtc"
        kinds: list[str] = []
        async for kind, data in pipeline.voice_turn(session, audio=wav):
            kinds.append(kind)
            if kind == "transcript":
                assert "订单" in data["text"]
        assert "transcript" in kinds
        assert avatar.audio, "应走 /humanaudio"
        print(f"[1/2] 语音回合 OK：events={kinds} humanaudio={avatar.audio}")

        down = Path(tempfile.mkdtemp(prefix="vi-voice-down-"))
        pipeline2, sessions2, avatar2 = await _pipeline(down, BrokenOmni(), "omni")
        session2 = sessions2.create(InterviewConfig(role="后端工程师", rounds=4, debug=True))
        session2.rtc_session_id = "fake-rtc"
        errors: list[str] = []
        async for kind, data in pipeline2.voice_turn(session2, audio=wav):
            if kind == "error":
                errors.append(data["message"])
        assert errors, "Omni 挂掉应返回 error 而非抛异常"
        async for _ in pipeline2.turn(session2, kickoff=True):
            pass
        assert avatar2.spoken, "降级文字 speak 应仍可用"
        print(f"[2/2] Omni 挂掉降级 OK：{errors[0][:40]} speak={len(avatar2.spoken)}")
        shutil.rmtree(down, ignore_errors=True)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print("语音链路自检通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
