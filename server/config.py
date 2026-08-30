from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _flag(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _resolve(path: str) -> Path:
    p = Path(path).expanduser()
    return p if p.is_absolute() else ROOT / p


@dataclass(frozen=True)
class LLMSettings:
    # mock：本地前端联调，不请求真实模型
    provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "openai").lower())
    api_base: str = field(default_factory=lambda: _env("LLM_API_BASE", "http://127.0.0.1:8000/v1").rstrip("/"))
    api_key: str = field(default_factory=lambda: _env("LLM_API_KEY") or _env("DASHSCOPE_API_KEY") or "not-needed")
    model: str = field(default_factory=lambda: _env("LLM_MODEL", "deepseek-v4-flash-0731"))
    disable_thinking: bool = field(default_factory=lambda: _flag("LLM_DISABLE_THINKING", True))
    fallback_api_base: str = field(default_factory=lambda: _env("LLM_FALLBACK_API_BASE").rstrip("/"))
    fallback_api_key: str = field(default_factory=lambda: _env("LLM_FALLBACK_API_KEY"))

    first_token_timeout: float = 5.0
    total_timeout: float = 60.0
    breaker_threshold: int = 3
    breaker_cooldown: float = 30.0

    @property
    def has_fallback(self) -> bool:
        return bool(self.fallback_api_base)


@dataclass(frozen=True)
class AvatarSettings:
    base_url: str = field(default_factory=lambda: _env("LIVETALKING_BASE", "http://127.0.0.1:8010").rstrip("/"))
    avatar_id: str = field(default_factory=lambda: _env("AVATAR_ID", "wav2lip256_avatar1"))
    timeout: float = 10.0


@dataclass(frozen=True)
class OmniSettings:
    """Qwen3-Omni：Realtime WS + chat/completions 音频备用。"""

    voice_mode: str = field(default_factory=lambda: _env("VOICE_MODE", "text").lower())
    api_base: str = field(default_factory=lambda: _env("OMNI_API_BASE", "http://127.0.0.1:8091").rstrip("/"))
    model: str = field(default_factory=lambda: _env("OMNI_MODEL", "Qwen/Qwen3-Omni-30B-A3B-Instruct"))
    speaker: str = field(default_factory=lambda: _env("OMNI_SPEAKER", "chelsie"))
    timeout: float = 90.0

    @property
    def enabled(self) -> bool:
        return self.voice_mode == "omni"

    @property
    def http_base(self) -> str:
        raw = self.api_base
        if raw.startswith("ws://"):
            return "http://" + raw[5:]
        if raw.startswith("wss://"):
            return "https://" + raw[6:]
        return raw

    @property
    def realtime_url(self) -> str:
        raw = self.api_base
        if raw.startswith("http://"):
            return "ws://" + raw[7:] + "/v1/realtime"
        if raw.startswith("https://"):
            return "wss://" + raw[8:] + "/v1/realtime"
        if raw.endswith("/v1/realtime"):
            return raw
        return raw.rstrip("/") + "/v1/realtime"


@dataclass(frozen=True)
class RAGSettings:
    qdrant_url: str = field(default_factory=lambda: _env("QDRANT_URL"))
    qdrant_path: Path = field(default_factory=lambda: _resolve(_env("QDRANT_PATH", "./data/qdrant")))
    collection: str = "corpus"
    embedding_provider: str = field(default_factory=lambda: _env("EMBEDDING_PROVIDER", "hash"))
    embedding_model: str = field(default_factory=lambda: _env("EMBEDDING_MODEL", "BAAI/bge-m3"))
    embedding_dim: int = field(default_factory=lambda: int(_env("EMBEDDING_DIM", "1024")))

    top_k_opening: int = 5
    top_k_followup: int = 3
    top_k_rubric: int = 2
    search_timeout: float = 2.0


@dataclass(frozen=True)
class Settings:
    host: str = field(default_factory=lambda: _env("HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("PORT", "8090")))
    sqlite_path: Path = field(default_factory=lambda: _resolve(_env("SQLITE_PATH", "./data/interview.db")))
    debug_default: bool = field(default_factory=lambda: _flag("DEBUG_DEFAULT", False))

    llm: LLMSettings = field(default_factory=LLMSettings)
    avatar: AvatarSettings = field(default_factory=AvatarSettings)
    omni: OmniSettings = field(default_factory=OmniSettings)
    rag: RAGSettings = field(default_factory=RAGSettings)

    web_dist: Path = field(default_factory=lambda: ROOT / "web" / "dist")


settings = Settings()
