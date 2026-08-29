"""Evaluator（架构 §3.6）：面试结束后离线出报告。

不在关键路径，可容忍数秒延迟；解析失败返回降级报告而非抛错。
"""

from __future__ import annotations

from server.config import RAGSettings
from server.debug import DebugEmitter, Stopwatch
from server.models import Dimension, Evidence, Report
from server.providers.llm import ChatLLM
from server.rag.retriever import Retriever, format_context
from server.session import InterviewSession

SYSTEM = """你是资深面试评估专家。根据面试全程对话给出结构化评价。
要求：
- 每个结论必须能对应到候选人原话，evidence 里引用原话片段
- 没有证据支持的维度分数给 0 并在 note 说明「本场未覆盖」
- 只输出 JSON，不要任何解释文字"""

TEMPLATE = """岗位：{role}
{jd_block}
评分标准参考（内部资料）：
{rubric}

面试对话记录：
{transcript}

输出 JSON：
{{
  "overall": 0-100,
  "recommendation": "strong_hire|hire|lean_hire|lean_no|no_hire",
  "level_guess": "如 P6 / 高级工程师",
  "dimensions": [
    {{"name": "技术深度", "score": 1-5, "note": "简述"}},
    {{"name": "工程实践", "score": 1-5, "note": "简述"}},
    {{"name": "问题解决", "score": 1-5, "note": "简述"}},
    {{"name": "沟通表达", "score": 1-5, "note": "简述"}},
    {{"name": "岗位匹配", "score": 1-5, "note": "简述"}}
  ],
  "strengths": ["…"],
  "risks": ["…"],
  "evidence": [{{"quote": "候选人原话片段", "why": "支撑了什么结论"}}],
  "next_round_focus": ["下一轮建议重点考察…"],
  "summary": "150 字内总评"
}}"""


class Evaluator:
    def __init__(
        self,
        *,
        llm: ChatLLM,
        retriever: Retriever,
        rag: RAGSettings,
        debug: DebugEmitter,
    ) -> None:
        self._llm = llm
        self._retriever = retriever
        self._rag = rag
        self._debug = debug

    async def evaluate(self, session: InterviewSession) -> Report:
        watch = Stopwatch()
        config = session.config
        hits = await self._retriever.retrieve(
            session_id=session.id,
            query=f"{config.role} 面试评分标准 评估维度",
            role=config.role,
            kinds=["rubric"],
            limit=self._rag.top_k_rubric,
        )
        prompt = TEMPLATE.format(
            role=config.role,
            jd_block=f"岗位要求：\n{config.jd.strip()[:800]}\n" if config.jd else "",
            rubric=format_context(hits) or "（无，使用通用标准）",
            transcript=self._transcript(session),
        )
        try:
            data = await self._llm.complete_json(
                [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
            )
            report = _to_report(data)
        except Exception as exc:  # noqa: BLE001 — 报告失败不应让会话卡在 Evaluating
            self._debug.log(session.id, f"评估失败，返回降级报告：{exc}")
            report = Report(summary=f"评估生成失败：{exc}", recommendation="lean_no")

        self._debug.latency(session.id, evaluate_ms=watch.elapsed_ms())
        return report

    @staticmethod
    def _transcript(session: InterviewSession) -> str:
        lines = []
        for message in session.visible_messages():
            who = "面试官" if message.role == "assistant" else "候选人"
            lines.append(f"{who}：{message.content}")
        return "\n".join(lines) or "（无有效对话）"


def _to_report(data: dict) -> Report:
    return Report(
        overall=int(data.get("overall") or 0),
        recommendation=str(data.get("recommendation") or "lean_no"),
        level_guess=str(data.get("level_guess") or ""),
        dimensions=[
            Dimension(
                name=str(d.get("name") or ""),
                score=int(d.get("score") or 0),
                note=str(d.get("note") or ""),
            )
            for d in (data.get("dimensions") or [])
        ],
        strengths=[str(s) for s in (data.get("strengths") or [])],
        risks=[str(s) for s in (data.get("risks") or [])],
        evidence=[
            Evidence(quote=str(e.get("quote") or ""), why=str(e.get("why") or ""))
            for e in (data.get("evidence") or [])
        ],
        next_round_focus=[str(s) for s in (data.get("next_round_focus") or [])],
        summary=str(data.get("summary") or ""),
    )
