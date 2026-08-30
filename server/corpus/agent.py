"""语料 Agent（架构 §3.5 / ADR-09）。

产出一律落 draft 状态，人工审核后才进入检索，避免模型幻觉污染题库。
"""

from __future__ import annotations

import uuid

from server.models import CorpusEntry, CorpusKind, utc_now
from server.providers.llm import ChatLLM

SYSTEM = """你是资深技术面试官兼题库编辑。你要为指定岗位产出高质量面试语料。
要求：
- 题目必须可在 3-5 分钟口头回答，避免需要写长代码的题
- 每题给出评分要点（rubric），区分及格/优秀两档
- 参考答案只写要点，不写长篇大论
- 不要重复给定的已有题目
只输出 JSON，不要任何解释文字。"""

TEMPLATE = """岗位：{role}
主题：{topic}
需要生成条数：{count}
语料类型：{kind}
已有题目（避免重复）：
{existing}

输出 JSON：
{{
  "entries": [
    {{
      "content": "题面或知识点正文",
      "tags": ["标签1", "标签2"],
      "rubric": "评分要点：及格线… / 优秀…",
      "reference_answer": "参考要点，分条"
    }}
  ]
}}"""


class CorpusAgent:
    def __init__(self, llm: ChatLLM) -> None:
        self._llm = llm

    async def generate(
        self,
        *,
        role: str,
        topic: str,
        count: int = 5,
        kind: CorpusKind = "question",
        existing: list[str] | None = None,
    ) -> list[CorpusEntry]:
        prompt = TEMPLATE.format(
            role=role,
            topic=topic,
            count=count,
            kind=kind,
            existing="\n".join(f"- {t}" for t in (existing or [])[:20]) or "（无）",
        )
        data = await self._llm.complete_json(
            [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
        )
        entries: list[CorpusEntry] = []
        for item in (data.get("entries") or [])[:count]:
            content = (item.get("content") or "").strip()
            if not content:
                continue
            entries.append(
                CorpusEntry(
                    id=f"agent-{uuid.uuid4().hex[:10]}",
                    kind=kind,
                    role=role,
                    tags=[str(t) for t in (item.get("tags") or [])][:6] or [topic],
                    content=content,
                    rubric=(item.get("rubric") or None),
                    reference_answer=(item.get("reference_answer") or None),
                    source="agent",
                    status="draft",
                    updated_at=utc_now(),
                )
            )
        return entries
