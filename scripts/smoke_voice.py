"""语音编排自检：假 Whisper ASR + 假 ChatTTS/Omni TTS，验证 ASR → LLM → TTS → /humanaudio 路径。

不连真实 vLLM-Omni / faster-whisper / ChatTTS。TTS 降级链：ChatTTS → Omni → 文字 speak。
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
from server.providers.chat_tts import ChatTTSError  # noqa: E402
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


class FakeASR:
    async def transcribe(self, audio: bytes, *, session_id: str = "") -> str:
        assert audio, "应收到音频"
        return "我负责订单系统重构，QPS 大约三千。"


class FakeTTS:
    """假 ChatTTS：合成固定 WAV。"""

    async def synthesize(self, text: str, *, session_id: str = "", instructions: str = "") -> bytes:
        return pcm16_to_wav(b"\x00\x00" * 160, 24000)


class BrokenTTS:
    async def synthesize(self, text: str, *, session_id: str = "", instructions: str = "") -> bytes:
        raise ChatTTSError("down")


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


async def _pipeline(
    workdir: Path, omni: Any, voice_mode: str, asr: Any = None, tts: Any = None
) -> tuple[Pipeline, Any, Any]:
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
        asr=asr,
        tts=tts,
        voice_mode=voice_mode,
    )
    return pipeline, sessions, avatar


async def _voice_round(pipeline: Pipeline, sessions: Any, wav: bytes) -> list[str]:
    session = sessions.create(InterviewConfig(role="后端工程师", rounds=4, debug=True))
    session.rtc_session_id = "fake-rtc"
    kinds: list[str] = []
    async for kind, data in pipeline.voice_turn(session, audio=wav):
        kinds.append(kind)
        if kind == "transcript":
            assert "订单" in data["text"]
    return kinds


async def main() -> int:
    wav = pcm16_to_wav(b"\x01\x00" * 320, 16000)

    workdir = Path(tempfile.mkdtemp(prefix="vi-voice-"))
    try:
        # 1) Whisper ASR + ChatTTS TTS 主链路
        pipeline, sessions, avatar = await _pipeline(workdir, FakeOmni(), "omni", asr=FakeASR(), tts=FakeTTS())
        kinds = await _voice_round(pipeline, sessions, wav)
        assert "transcript" in kinds
        assert avatar.audio, "TTS 应走 /humanaudio"
        print(f"[1/5] Whisper ASR → LLM → ChatTTS 回合 OK：events={kinds} humanaudio={avatar.audio}")

        # 2) ChatTTS 挂掉 → 回退 Omni 合成，仍走 /humanaudio
        down = Path(tempfile.mkdtemp(prefix="vi-voice-tts-down-"))
        pipeline2, sessions2, avatar2 = await _pipeline(
            down, FakeOmni(), "omni", asr=FakeASR(), tts=BrokenTTS()
        )
        await _voice_round(pipeline2, sessions2, wav)
        assert avatar2.audio, "ChatTTS 挂掉应回退 Omni 合成"
        print(f"[2/5] ChatTTS 挂掉回退 Omni 合成 OK：humanaudio={avatar2.audio}")
        shutil.rmtree(down, ignore_errors=True)

        # 3) ChatTTS + Omni 都挂 → 降级文字 speak
        both_down = Path(tempfile.mkdtemp(prefix="vi-voice-all-down-"))
        pipeline3, sessions3, avatar3 = await _pipeline(
            both_down, BrokenOmni(), "omni", asr=FakeASR(), tts=BrokenTTS()
        )
        kinds3 = await _voice_round(pipeline3, sessions3, wav)
        assert "error" not in kinds3, f"TTS 全挂应降级文字而非报错：{kinds3}"
        assert avatar3.spoken, "降级文字 speak 应仍可用"
        print(f"[3/5] TTS 全挂降级文字 OK：speak={len(avatar3.spoken)}")
        shutil.rmtree(both_down, ignore_errors=True)

        # 4) 无 ASR 时回退 Omni 转写
        fallback = Path(tempfile.mkdtemp(prefix="vi-voice-fallback-"))
        pipeline4, sessions4, _avatar4 = await _pipeline(
            fallback, FakeOmni(), "omni", asr=None, tts=FakeTTS()
        )
        kinds4 = await _voice_round(pipeline4, sessions4, wav)
        assert "transcript" in kinds4
        print(f"[4/5] 无 ASR 回退 Omni 转写 OK：events={kinds4}")
        shutil.rmtree(fallback, ignore_errors=True)

        # 5) 无 RTC：ChatTTS 合成后走浏览器 SSE 音频
        no_rtc = Path(tempfile.mkdtemp(prefix="vi-voice-nortc-"))
        pipeline5, sessions5, avatar5 = await _pipeline(
            no_rtc, FakeOmni(), "omni", asr=FakeASR(), tts=FakeTTS()
        )
        session5 = sessions5.create(InterviewConfig(role="后端工程师", rounds=4, debug=True))
        # 故意不设 rtc_session_id
        audio_events = 0
        async for kind, data in pipeline5.voice_turn(session5, audio=wav):
            if kind == "assistant_audio":
                assert data.get("audio_b64")
                audio_events += 1
        assert audio_events >= 1, "无数字人时应下发浏览器 TTS"
        assert not avatar5.audio, "无 RTC 不应推 humanaudio"
        print(f"[5/5] 无 RTC 浏览器 TTS OK：audio_events={audio_events}")
        shutil.rmtree(no_rtc, ignore_errors=True)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print("语音链路自检通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
