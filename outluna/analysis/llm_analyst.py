"""LLM 综合研判分析器。"""

import os
from typing import Any

from outluna.analysis.base import AnalyzerBase
from outluna.config import settings
from outluna.data.gateway import DataGateway
from outluna.data.models import AnalysisContext, AnalyzerResult


class LLMAnalyst(AnalyzerBase):
    """LLM 综合研判分析器（可选）。

    将多维度分析结果输入 LLM，生成投资建议和风险提示。
    如果未配置 LLM API Key，则返回基于规则的简单综合判断。
    """

    dimension = "llm"

    def __init__(self, gateway: DataGateway):
        super().__init__(gateway)
        self.client = None
        if settings.llm_api_key:
            try:
                from openai import OpenAI
                self.client = OpenAI(
                    api_key=settings.llm_api_key,
                    base_url=settings.llm_base_url or None,
                )
            except ImportError:
                pass

    async def analyze(self, symbol: str, context: AnalysisContext | None = None) -> AnalyzerResult:
        """执行 LLM 综合研判。"""
        if context is None:
            return AnalyzerResult(
                dimension=self.dimension,
                data={},
                signals=["缺少分析上下文"],
                score=0,
                summary="未提供分析上下文，无法进行综合研判。",
            )

        # 基于已有结果生成综合判断
        scores = []
        summaries = []
        for dim, result in context.results.items():
            if result.score is not None:
                scores.append(result.score)
            summaries.append(f"【{dim}】{result.summary}")

        avg_score = sum(scores) / len(scores) if scores else 50

        if self.client:
            recommendation = await self._call_llm(symbol, context)
        else:
            recommendation = self._rule_based_recommendation(avg_score, context.results)

        risk_rating = self._risk_rating(avg_score)

        return AnalyzerResult(
            dimension=self.dimension,
            data={"avg_score": avg_score, "llm_recommendation": recommendation},
            signals=[f"综合评分：{avg_score:.0f}/100", f"风险等级：{risk_rating}"],
            score=avg_score,
            summary=recommendation,
        )

    def _rule_based_recommendation(self, avg_score: float, results: dict) -> str:
        """基于规则生成投资建议。"""
        if avg_score >= 75:
            return "综合评分较高，基本面、资金面或情绪面存在积极信号，可作为重点关注标的，但需结合自身仓位和风险偏好决策。"
        elif avg_score >= 60:
            return "综合评分中等，存在部分积极信号，但需进一步观察确认，建议谨慎跟踪。"
        else:
            return "综合评分偏低，存在较多风险或不确定性信号，建议回避或继续观察。"

    def _risk_rating(self, score: float) -> str:
        """根据评分划分风险等级。"""
        if score >= 75:
            return "中低风险"
        elif score >= 60:
            return "中等风险"
        elif score >= 40:
            return "中高风险"
        return "高风险"

    async def _call_llm(self, symbol: str, context: AnalysisContext) -> str:
        """调用 LLM 生成综合研判。"""
        prompt = self._build_prompt(symbol, context)
        try:
            response = self.client.chat.completions.create(
                model=settings.llm_model,
                messages=[
                    {
                        "role": "system",
                        "content": "你是一位专业的股票投资分析师。请基于以下多维度分析结果，给出简明扼要的投资建议（不超过 200 字），并明确指出主要风险和关注点。仅作投资辅助参考，不构成投资建议。",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
            )
            return response.choices[0].message.content or "LLM 返回为空"
        except Exception as exc:
            return f"LLM 调用失败：{exc}"

    def _build_prompt(self, symbol: str, context: AnalysisContext) -> str:
        """构建 LLM 提示词。"""
        lines = [f"股票代码：{symbol}", ""]
        for dim, result in context.results.items():
            lines.append(f"【{dim}】")
            lines.append(f"评分：{result.score}")
            lines.append(f"摘要：{result.summary}")
            lines.append("")
        return "\n".join(lines)
