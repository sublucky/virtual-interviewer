"""VideoGenerator（架构 §3.12）：背景/情绪镜头，不进关键路径。

生成失败一律降级为静态兜底，绝不阻塞面试。
"""

from __future__ import annotations

from typing import Protocol

from server.models import HealthStatus


class VideoGenerator(Protocol):
    async def generate(self, prompt: str, duration: float) -> str | None: ...
    async def health(self) -> HealthStatus: ...


class NullVideoGenerator:
    """未接入视频生成时的实现：返回 None，前端用静态背景。"""

    async def generate(self, prompt: str, duration: float) -> str | None:
        return None

    async def health(self) -> HealthStatus:
        return HealthStatus(ok=True, detail="未启用视频生成，使用静态背景")
