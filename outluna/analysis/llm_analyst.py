"""LLM 综合研判分析器。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

import pandas as pd
from pydantic import BaseModel, Field

from outluna.analysis.base import AnalyzerBase
from outluna.analysis.context import AnalysisContext
from outluna.config import settings
from outluna.data.gateway import DataGateway
from outluna.data.models import AnalyzerResult

if TYPE_CHECKING:
    from openai import AsyncOpenAI


class LLMRecommendation(BaseModel):
    """LLM 结构化输出模型，匹配标准分析结论模板。"""

    summary: str = Field(description="不超过 300 字的投资建议摘要")
    risk_rating: str = Field(description="风险等级：低风险、中低风险、中等风险、中高风险、高风险")
    key_points: list[str] = Field(description="主要关注点或风险点，3-5 条")
    company_overview: str = Field(description="公司概况：名称、实际控制人、控股股东、主营业务、行业地位")
    price_performance: str = Field(description="股价表现：当前价格、涨跌幅、近期走势、短期技术信号")
    financial_highlights: str = Field(
        description="财务亮点：盈利能力、成长性、资本结构、现金流、运营效率等核心结论"
    )
    key_financial_data: str = Field(
        description="关键财务数据：总资产、总负债、归母净资产、营业收入、归母净利润、经营现金流、EPS、BPS 等"
    )
    valuation: str = Field(description="估值参考：静态市盈率、市净率等")
    advantages: list[str] = Field(description="优势列表，3-5 条")
    risks: list[str] = Field(description="风险列表，3-5 条")
    overall_evaluation: str = Field(description="总体评价：综合判断与投资建议")


class LLMAnalyst(AnalyzerBase):
    """LLM 综合研判分析器。

    将 Kimi Datasource 获取的原始数据（公司信息、实时行情、技术指标、
    历史价格、财务指标、财务报表）以及各维度规则分析结果输入 LLM，
    生成结构化投资建议。

    支持两种 LLM 调用方式：
    1. 外部传入的 LLMProvider（如 AstrBotLLMProvider）；
    2. 默认的 OpenAI 兼容客户端（通过 settings 配置）。
    """

    dimension = "llm"

    def __init__(
        self,
        gateway: DataGateway,
        llm_provider: Any | None = None,
    ):
        super().__init__(gateway)
        self.llm_provider = llm_provider
        self.client: AsyncOpenAI | None = None
        if self.llm_provider is None and settings.llm_api_key:
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
                summary="未提供分析上下文，无法进行综合研判。",
            )

        has_llm = self.llm_provider is not None or self.client is not None
        if has_llm:
            llm_result = await self._call_llm(symbol, context)
            recommendation = self._format_recommendation(llm_result)
            risk_rating = llm_result.risk_rating
            signals = [f"风险等级：{risk_rating}"]
            signals.extend(llm_result.key_points)
        else:
            recommendation = self._rule_based_recommendation(context.results)
            risk_rating = "中高风险"
            signals = ["未配置 LLM，使用规则化汇总", f"风险等级：{risk_rating}"]

        return AnalyzerResult(
            dimension=self.dimension,
            data={"llm_recommendation": recommendation, "risk_rating": risk_rating},
            signals=signals,
            summary=recommendation,
        )

    def _format_recommendation(self, result: LLMRecommendation) -> str:
        """将 LLM 结构化输出拼接为标准 Markdown 报告模板。"""
        lines = [
            "## 1. 公司概况",
            "",
            result.company_overview,
            "",
            "## 2. 股价表现",
            "",
            result.price_performance,
            "",
            "## 3. 财务亮点",
            "",
            result.financial_highlights,
            "",
            "## 4. 关键财务数据",
            "",
            result.key_financial_data,
            "",
            "## 5. 估值参考",
            "",
            result.valuation,
            "",
            "## 6. 优势与风险",
            "",
            "**优势**：",
        ]
        for idx, item in enumerate(result.advantages, 1):
            lines.append(f"{idx}. {item}")
        lines.extend(["", "**风险**："])
        for idx, item in enumerate(result.risks, 1):
            lines.append(f"{idx}. {item}")
        lines.extend(
            [
                "",
                "## 7. 总体评价",
                "",
                result.overall_evaluation,
                "",
                "## 8. 摘要",
                "",
                result.summary,
            ]
        )
        return "\n".join(lines)

    def _rule_based_recommendation(self, results: dict) -> str:
        """基于规则生成投资建议。"""
        positive_signals = 0
        negative_signals = 0
        for result in results.values():
            for signal in result.signals:
                if any(word in signal for word in ["失败", "亏损", "偏高", "负面", "偏弱", "净流出"]):
                    negative_signals += 1
                elif any(word in signal for word in ["良好", "为正", "合理", "正面", "偏强", "净流入"]):
                    positive_signals += 1
        if positive_signals > negative_signals + 2:
            return "整体信号偏积极，可作为重点关注标的，但需结合自身仓位和风险偏好决策。"
        if positive_signals > negative_signals:
            return "整体信号中性偏多，存在部分积极信号，但需进一步观察确认，建议谨慎跟踪。"
        return "整体信号中性或偏空，存在较多风险或不确定性信号，建议回避或继续观察。"

    async def _call_llm(self, symbol: str, context: AnalysisContext) -> LLMRecommendation:
        """调用 LLM 生成结构化综合研判。"""
        prompt = self._build_prompt(symbol, context)
        system_prompt = (
            "你是一位专业的股票投资分析师。请基于提供的原始数据和多维度分析结果，"
            "按照固定模板输出结构化分析结论。模板必须包含以下 8 个部分：\n"
            "1. 公司概况\n"
            "2. 股价表现\n"
            "3. 财务亮点\n"
            "4. 关键财务数据\n"
            "5. 估值参考\n"
            "6. 优势与风险（各 3-5 条）\n"
            "7. 总体评价\n"
            "8. 摘要（不超过 300 字）\n\n"
            "仅作投资辅助参考，不构成投资建议。"
        )

        fallback = LLMRecommendation(
            summary="LLM 调用失败，无法生成综合研判。",
            risk_rating="高风险",
            key_points=["LLM 服务异常，请检查配置与网络"],
            company_overview="",
            price_performance="",
            financial_highlights="",
            key_financial_data="",
            valuation="",
            advantages=[],
            risks=[],
            overall_evaluation="",
        )

        if self.llm_provider is not None:
            try:
                return await self._call_with_provider(prompt, system_prompt)
            except Exception as exc:
                fallback.summary = f"LLM 调用失败：{exc}"
                return fallback

        if self.client is not None:
            try:
                return await self._call_with_openai(prompt, system_prompt)
            except Exception as exc:
                fallback.summary = f"LLM 调用失败：{exc}"
                return fallback

        return fallback

    async def _call_with_provider(self, prompt: str, system_prompt: str) -> LLMRecommendation:
        """使用外部 LLMProvider 生成结构化输出。"""
        assert self.llm_provider is not None
        return cast(
            LLMRecommendation,
            await self.llm_provider.generate_structured(
                system_prompt=system_prompt,
                user_prompt=prompt,
                response_format=LLMRecommendation,
                temperature=0.3,
            ),
        )

    async def _call_with_openai(self, prompt: str, system_prompt: str) -> LLMRecommendation:
        """使用 OpenAI 兼容客户端生成结构化输出。"""
        assert self.client is not None
        response = await self.client.beta.chat.completions.parse(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            response_format=LLMRecommendation,
        )
        parsed = response.choices[0].message.parsed
        if parsed is None:
            raise RuntimeError("LLM 未返回结构化内容")
        return parsed

    def _build_prompt(self, symbol: str, context: AnalysisContext) -> str:
        """构建包含原始数据和多维度分析结果的 LLM 提示词。"""
        lines = [
            f"请对股票 {symbol} 进行综合分析，基于以下原始数据和分析结果。",
            "",
            "## 一、原始数据",
            "",
        ]

        for dim, result in context.results.items():
            if dim == "llm":
                continue
            lines.append(f"### {dim}")
            data = result.data or {}
            for key, value in data.items():
                text = self._serialize_value(value)
                if text:
                    lines.append(f"**{key}**：")
                    lines.append(text)
                    lines.append("")
            if result.signals:
                lines.append(f"**关键信号**：{'；'.join(result.signals[:5])}")
            lines.append("")

        lines.extend([
            "## 二、各维度关键信号汇总",
            "",
        ])
        for dim, result in context.results.items():
            if dim == "llm":
                continue
            lines.append(f"- {dim}")
            if result.summary:
                lines.append(f"  - {result.summary}")
            for signal in result.signals[:5]:
                lines.append(f"  - {signal}")
        lines.append("")

        lines.append(
            "请结合以上数据，严格按照系统提示中的 8 部分模板输出结构化分析结论。"
        )
        return "\n".join(lines)

    def _serialize_value(self, value: Any) -> str:
        """将任意值序列化为文本，便于 LLM 理解。"""
        if value is None:
            return ""
        if isinstance(value, pd.DataFrame):
            if value.empty:
                return ""
            preview = value.head(10)
            return preview.to_string(index=False)
        if isinstance(value, dict):
            if not value:
                return ""
            return json.dumps(value, ensure_ascii=False, indent=2, default=str)
        if isinstance(value, list):
            if not value:
                return ""
            return json.dumps(value, ensure_ascii=False, indent=2, default=str)
        return str(value)
