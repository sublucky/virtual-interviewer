"""EmbeddingClient（架构 §3.4）：bge-m3 本地 / 百炼远程 / hash 离线开发。

hash 实现不依赖模型权重与网络，用于骨架自测与 CI；
生产切 bge-m3 或 dashscope，只改 EMBEDDING_PROVIDER。
"""

from __future__ import annotations

import hashlib
import math
from typing import Protocol

from server.config import RAGSettings


class EmbeddingClient(Protocol):
    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbedding:
    """确定性哈希向量：语义能力弱，但保证骨架离线可跑、结果稳定。"""

    def __init__(self, dim: int) -> None:
        self.dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(text) for text in texts]

    def _one(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        # 按 2-gram 散列累加，让近似文本得到近似向量
        tokens = [text[i : i + 2] for i in range(max(len(text) - 1, 1))]
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]


class BGEEmbedding:
    """本地 bge-m3（CPU 可跑），需 pip install sentence-transformers。"""

    def __init__(self, model_name: str, dim: int) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)
        self.dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(texts, normalize_embeddings=True)
        return [list(map(float, v)) for v in vectors]


class DashScopeEmbedding:
    """百炼 text-embedding（OpenAI 兼容）。"""

    def __init__(self, *, api_key: str, base_url: str, model: str, dim: int) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self.dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        resp = await self._client.embeddings.create(model=self._model, input=texts)
        return [item.embedding for item in resp.data]


def build_embedding(rag: RAGSettings, *, llm_api_key: str = "", llm_api_base: str = "") -> EmbeddingClient:
    provider = rag.embedding_provider.lower()
    if provider in {"bge", "bge-m3", "local"}:
        return BGEEmbedding(rag.embedding_model, rag.embedding_dim)
    if provider in {"dashscope", "bailian"}:
        return DashScopeEmbedding(
            api_key=llm_api_key,
            base_url=llm_api_base or "https://dashscope.aliyuncs.com/compatible-mode/v1",
            model=rag.embedding_model,
            dim=rag.embedding_dim,
        )
    return HashEmbedding(rag.embedding_dim)
