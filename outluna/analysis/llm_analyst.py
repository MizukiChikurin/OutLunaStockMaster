"""LLM 综合研判分析器。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from outluna.analysis.base import AnalyzerBase
from outluna.analysis.context import AnalysisContext
from outluna.config import settings
from outluna.data.gateway import DataGateway
from outluna.data.models import AnalyzerResult

if TYPE_CHECKING:
    from openai import AsyncOpenAI


class LLMRecommendation(BaseModel):
    """LLM 结构化输出模型。"""

    summary: str = Field(description="不超过 200 字的投资建议摘要")
    risk_rating: str = Field(description="风险等级：低风险、中低风险、中等风险、中高风险、高风险")
    key_points: list[str] = Field(description="主要关注点或风险点，3-5 条")
    score: int = Field(ge=0, le=100, description="综合评分 0-100")


class LLMAnalyst(AnalyzerBase):
    """LLM 综合研判分析器（可选）。

    将多维度分析结果输入 LLM，生成结构化投资建议和风险提示。
    如果未配置 LLM API Key，则返回基于规则的简单综合判断。
    """

    dimension = "llm"

    def __init__(self, gateway: DataGateway):
        super().__init__(gateway)
        self.client: AsyncOpenAI | None = None
        if settings.llm_api_key:
            try:
                from openai import AsyncOpenAI

                self.client = AsyncOpenAI(
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

        scores = []
        for result in context.results.values():
            if result.score is not None:
                scores.append(result.score)

        avg_score = sum(scores) / len(scores) if scores else 50.0

        if self.client:
            llm_result = await self._call_llm(symbol, context)
            recommendation = llm_result.summary
            risk_rating = llm_result.risk_rating
            score = float(llm_result.score)
            signals = [f"综合评分：{score:.0f}/100", f"风险等级：{risk_rating}"]
            signals.extend(llm_result.key_points)
        else:
            recommendation = self._rule_based_recommendation(avg_score, context.results)
            risk_rating = self._risk_rating(avg_score)
            score = avg_score
            signals = [f"综合评分：{score:.0f}/100", f"风险等级：{risk_rating}"]

        return AnalyzerResult(
            dimension=self.dimension,
            data={"avg_score": score, "llm_recommendation": recommendation, "risk_rating": risk_rating},
            signals=signals,
            score=score,
            summary=recommendation,
        )

    def _rule_based_recommendation(self, avg_score: float, results: dict) -> str:
        """基于规则生成投资建议。"""
        if avg_score >= 75:
            return "综合评分较高，基本面、资金面或情绪面存在积极信号，可作为重点关注标的，但需结合自身仓位和风险偏好决策。"
        if avg_score >= 60:
            return "综合评分中等，存在部分积极信号，但需进一步观察确认，建议谨慎跟踪。"
        return "综合评分偏低，存在较多风险或不确定性信号，建议回避或继续观察。"

    def _risk_rating(self, score: float) -> str:
        """根据评分划分风险等级。"""
        if score >= 75:
            return "中低风险"
        if score >= 60:
            return "中等风险"
        if score >= 40:
            return "中高风险"
        return "高风险"

    async def _call_llm(self, symbol: str, context: AnalysisContext) -> LLMRecommendation:
        """调用 LLM 生成结构化综合研判。"""
        assert self.client is not None, "LLM 客户端未初始化"
        prompt = self._build_prompt(symbol, context)
        try:
            response = await self.client.beta.chat.completions.parse(
                model=settings.llm_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是一位专业的股票投资分析师。请基于多维度分析结果，"
                            "给出结构化投资建议。仅作投资辅助参考，不构成投资建议。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                response_format=LLMRecommendation,
            )
            parsed = response.choices[0].message.parsed
            if parsed is None:
                raise RuntimeError("LLM 未返回结构化内容")
            return parsed
        except Exception as exc:
            return LLMRecommendation(
                summary=f"LLM 调用失败：{exc}",
                risk_rating="高风险",
                key_points=["LLM 服务异常，请检查配置与网络"],
                score=0,
            )

    def _build_prompt(self, symbol: str, context: AnalysisContext) -> str:
        """构建 LLM 提示词。"""
        lines = [f"股票代码：{symbol}", ""]
        for dim, result in context.results.items():
            lines.append(f"【{dim}】")
            lines.append(f"评分：{result.score}")
            lines.append(f"摘要：{result.summary}")
            if result.signals:
                lines.append(f"信号：{'；'.join(result.signals)}")
            lines.append("")
        return "\n".join(lines)
