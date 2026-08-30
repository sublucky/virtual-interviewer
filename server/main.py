"""FastAPI 接入层（架构 §3.2）：只做协议转换与依赖装配，业务逻辑在下层。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from server.config import settings
from server.corpus.agent import CorpusAgent
from server.corpus.manager import CorpusError, CorpusManager
from server.debug import DebugEmitter
from server.interview.engine import InterviewEngine
from server.interview.evaluator import Evaluator
from server.models import CorpusEntry, CorpusStatus, InterviewConfig
from server.pipeline import Pipeline
from server.providers.avatar import LiveTalkingAvatar
from server.providers.embedding import build_embedding
from server.providers.llm import build_llm
from server.providers.omni_realtime import build_omni
from server.rag.retriever import Retriever
from server.rag.store import VectorStore
from server.session import InterviewSession, SessionRepository
from server.storage import Storage


class Container:
    """依赖装配：所有 provider 在此实例化，替换实现只改这里。"""

    def __init__(self) -> None:
        self.debug = DebugEmitter(default_enabled=settings.debug_default)
        self.storage = Storage(settings.sqlite_path)
        self.llm = build_llm(settings.llm)
        self.embedding = build_embedding(
            settings.rag, llm_api_key=settings.llm.api_key, llm_api_base=settings.llm.api_base
        )
        self.store = VectorStore(settings.rag)
        self.avatar = LiveTalkingAvatar(settings.avatar)
        self.omni = build_omni(settings.omni, debug=self.debug)
        self.retriever = Retriever(
            store=self.store, embedding=self.embedding, settings=settings.rag, debug=self.debug
        )
        self.corpus = CorpusManager(
            store=self.store, embedding=self.embedding, storage=self.storage
        )
        self.corpus_agent = CorpusAgent(self.llm)
        self.sessions = SessionRepository(storage=self.storage, debug=self.debug)
        self.pipeline = Pipeline(
            sessions=self.sessions,
            engine=InterviewEngine(
                llm=self.llm, retriever=self.retriever, rag=settings.rag, debug=self.debug
            ),
            evaluator=Evaluator(
                llm=self.llm, retriever=self.retriever, rag=settings.rag, debug=self.debug
            ),
            avatar=self.avatar,
            debug=self.debug,
            omni=self.omni,
            voice_mode=settings.omni.voice_mode,
        )

    async def startup(self) -> None:
        imported = await self.corpus.bootstrap()
        if imported:
            print(f"[corpus] 已导入种子语料 {imported} 条")

    async def shutdown(self) -> None:
        await self.avatar.aclose()
        await self.omni.aclose()
        self.store.close()
        self.storage.close()

    def session(self, session_id: str) -> InterviewSession:
        session = self.sessions.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="会话不存在或已过期")
        return session


_container: Container | None = None


def get_container() -> Container:
    if _container is None:
        raise HTTPException(status_code=503, detail="服务尚未就绪")
    return _container


Ctx = Annotated[Container, Depends(get_container)]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _container
    _container = Container()
    await _container.startup()
    try:
        yield
    finally:
        await _container.shutdown()
        _container = None


app = FastAPI(title="Virtual Interviewer", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------
# 请求体
# --------------------------------------------------------------------------


class MessageRequest(BaseModel):
    text: str = ""
    kickoff: bool = False
    end: bool = False


class DebugRequest(BaseModel):
    enabled: bool


class CorpusUpsertRequest(BaseModel):
    entries: list[CorpusEntry]


class CorpusStatusRequest(BaseModel):
    ids: list[str]
    status: CorpusStatus


class CorpusAgentRequest(BaseModel):
    role: str
    topic: str
    count: int = Field(default=5, ge=1, le=20)
    kind: str = "question"
    save_as_draft: bool = True


# --------------------------------------------------------------------------
# 元信息
# --------------------------------------------------------------------------


@app.get("/api/meta")
async def meta(ctx: Ctx) -> dict[str, Any]:
    llm, avatar, vector, omni = (
        await ctx.llm.health(),
        await ctx.avatar.health(),
        await ctx.store.health(),
        await ctx.omni.health(),
    )
    return {
        "llm": llm.model_dump(),
        "avatar": avatar.model_dump(),
        "vector": vector.model_dump(),
        "omni": omni.model_dump(),
        "voice_mode": settings.omni.voice_mode,
        "embedding": {
            "provider": settings.rag.embedding_provider,
            "dim": settings.rag.embedding_dim,
        },
        "llm_provider": settings.llm.provider,
        "debug_default": settings.debug_default,
        "presets": [
            {"role": "后端工程师", "style": "probe", "rounds": 8},
            {"role": "前端工程师", "style": "probe", "rounds": 8},
            {"role": "算法工程师", "style": "probe", "rounds": 8},
            {"role": "产品经理", "style": "gentle", "rounds": 8},
            {"role": "客户端工程师", "style": "probe", "rounds": 8},
        ],
    }


# --------------------------------------------------------------------------
# 会话
# --------------------------------------------------------------------------


@app.post("/api/sessions")
async def create_session(config: InterviewConfig, ctx: Ctx) -> dict[str, Any]:
    session = ctx.sessions.create(config)
    return {
        "session_id": session.id,
        "state": session.state.value,
        "debug": ctx.debug.is_enabled(session.id),
        "rounds": config.rounds,
    }


@app.post("/api/sessions/{session_id}/message")
async def send_message(session_id: str, payload: MessageRequest, ctx: Ctx) -> StreamingResponse:
    session = ctx.session(session_id)
    ctx.pipeline.attach_debug(session_id)

    async def event_stream() -> AsyncIterator[str]:
        try:
            async for kind, data in ctx.pipeline.turn(
                session, text=payload.text.strip() or None, kickoff=payload.kickoff, end=payload.end
            ):
                yield _sse({"event": kind, **data})
        except Exception as exc:  # noqa: BLE001 — SSE 内异常必须以事件形式回传前端
            yield _sse({"event": "error", "message": str(exc)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/sessions/{session_id}/voice/turn")
async def voice_turn(
    session_id: str,
    ctx: Ctx,
    file: UploadFile | None = File(None),
    end: bool = Form(False),
) -> StreamingResponse:
    """MVP：整段录音上传（按住说完再传）。预留后续 WS /voice/stream。"""
    session = ctx.session(session_id)
    ctx.pipeline.attach_debug(session_id)
    audio = await file.read() if file is not None else b""

    async def event_stream() -> AsyncIterator[str]:
        try:
            async for kind, data in ctx.pipeline.voice_turn(session, audio=audio, end=end):
                yield _sse({"event": kind, **data})
        except Exception as exc:  # noqa: BLE001
            yield _sse({"event": "error", "message": str(exc)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/sessions/{session_id}/voice/interrupt")
async def voice_interrupt(session_id: str, ctx: Ctx) -> dict[str, Any]:
    session = ctx.session(session_id)
    if session.rtc_session_id:
        try:
            await ctx.avatar.interrupt(session.rtc_session_id)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "detail": str(exc)}
    return {"ok": True}


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str, ctx: Ctx) -> dict[str, Any]:
    session = ctx.session(session_id)
    return {
        "session_id": session.id,
        "state": session.state.value,
        "turns": session.turns,
        "rounds": session.config.rounds,
        "messages": [m.model_dump() for m in session.visible_messages()],
        "debug": ctx.debug.is_enabled(session.id),
    }


@app.get("/api/sessions/{session_id}/report")
async def get_report(session_id: str, ctx: Ctx) -> dict[str, Any]:
    session = ctx.session(session_id)
    if session.report is None:
        return {"ready": False, "state": session.state.value}
    return {"ready": True, "report": session.report.model_dump()}


# --------------------------------------------------------------------------
# Debug（需求 §4.3）
# --------------------------------------------------------------------------


@app.post("/api/sessions/{session_id}/debug")
async def toggle_debug(session_id: str, payload: DebugRequest, ctx: Ctx) -> dict[str, Any]:
    ctx.session(session_id)
    ctx.debug.set_enabled(session_id, payload.enabled)
    if payload.enabled:
        ctx.pipeline.attach_debug(session_id)
    else:
        ctx.pipeline.detach_debug(session_id)
    return {"enabled": payload.enabled}


@app.get("/api/sessions/{session_id}/debug/history")
async def debug_history(session_id: str, ctx: Ctx) -> dict[str, Any]:
    ctx.session(session_id)
    return {
        "enabled": ctx.debug.is_enabled(session_id),
        "events": [e.as_sse() for e in ctx.debug.history(session_id)],
    }


# --------------------------------------------------------------------------
# WebRTC 信令（转发给 LiveTalking）
# --------------------------------------------------------------------------


@app.post("/api/sessions/{session_id}/rtc/offer")
async def rtc_offer(session_id: str, request: Request, ctx: Ctx) -> Response:
    session = ctx.session(session_id)
    offer = (await request.body()).decode("utf-8")
    try:
        answer, rtc_session_id = await ctx.avatar.open_stream(offer)
    except Exception as exc:  # noqa: BLE001 — 数字人不可用时前端走纯文本模式
        raise HTTPException(status_code=502, detail=f"数字人服务不可用：{exc}") from exc
    session.rtc_session_id = rtc_session_id
    ctx.debug.comm(
        session_id, target="livetalking", action="whep", took_ms=0, rtc_session_id=rtc_session_id
    )
    return Response(content=answer, media_type="application/sdp")


# --------------------------------------------------------------------------
# 语料管理（需求 §4.2 管理后台）
# --------------------------------------------------------------------------


@app.get("/api/corpus")
async def list_corpus(
    ctx: Ctx,
    role: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    limit: int = 200,
    with_content: bool = False,
) -> dict[str, Any]:
    items = ctx.corpus.list(role=role, kind=kind, status=status, limit=limit)
    if with_content and items:
        full = {e.id: e for e in await ctx.corpus.get_many([row["id"] for row in items])}
        enriched = []
        for row in items:
            entry = full.get(row["id"])
            if entry is None:
                enriched.append(row)
            else:
                data = entry.model_dump()
                data.update({k: row[k] for k in ("status", "version", "updated_at") if k in row})
                enriched.append(data)
        return {"items": enriched}
    return {"items": items}


@app.get("/api/corpus/stats")
async def corpus_stats(ctx: Ctx) -> dict[str, Any]:
    return await ctx.corpus.stats()


@app.get("/api/corpus/{entry_id}")
async def get_corpus(entry_id: str, ctx: Ctx) -> dict[str, Any]:
    entry = await ctx.corpus.get(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="语料不存在或正文未入库")
    return {"entry": entry.model_dump()}


@app.post("/api/corpus")
async def upsert_corpus(payload: CorpusUpsertRequest, ctx: Ctx) -> dict[str, Any]:
    return {"upserted": await ctx.corpus.upsert(payload.entries)}


@app.post("/api/corpus/status")
async def set_corpus_status(payload: CorpusStatusRequest, ctx: Ctx) -> dict[str, Any]:
    try:
        await ctx.corpus.set_status(payload.ids, payload.status)
    except CorpusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"updated": len(payload.ids)}


@app.post("/api/corpus/delete")
async def delete_corpus(ctx: Ctx, ids: list[str] = Body(..., embed=True)) -> dict[str, Any]:
    await ctx.corpus.delete(ids)
    return {"deleted": len(ids)}


@app.post("/api/corpus/bootstrap")
async def bootstrap_corpus(ctx: Ctx, force: bool = False) -> dict[str, Any]:
    return {"imported": await ctx.corpus.bootstrap(force=force)}


@app.post("/api/corpus/agent")
async def corpus_agent(payload: CorpusAgentRequest, ctx: Ctx) -> dict[str, Any]:
    existing = [row["id"] for row in ctx.corpus.list(role=payload.role, kind="question", limit=20)]
    entries = await ctx.corpus_agent.generate(
        role=payload.role,
        topic=payload.topic,
        count=payload.count,
        kind=payload.kind,  # type: ignore[arg-type]
        existing=existing,
    )
    if payload.save_as_draft and entries:
        await ctx.corpus.upsert(entries)
    return {"entries": [e.model_dump() for e in entries], "saved": payload.save_as_draft}


# --------------------------------------------------------------------------
# 静态资源
# --------------------------------------------------------------------------

if settings.web_dist.is_dir():
    app.mount("/", StaticFiles(directory=settings.web_dist, html=True), name="web")
else:

    @app.get("/")
    async def index() -> JSONResponse:
        return JSONResponse(
            {
                "service": "virtual-interviewer",
                "hint": "前端未构建，执行 cd web && npm install && npm run dev",
                "docs": "/docs",
            }
        )


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def main() -> None:
    import uvicorn

    uvicorn.run("server.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
