"""分析编排器。"""

from datetime import datetime
from typing import Any
from uuid import uuid4

import pandas as pd

from outluna.analysis.base import AnalyzerBase
from outluna.analysis.company import CompanyAnalyzer
from outluna.analysis.context import AnalysisContext
from outluna.analysis.fundamentals import FundamentalsAnalyzer
from outluna.analysis.institutional import InstitutionalAnalyzer
from outluna.analysis.llm_analyst import LLMAnalyst
from outluna.analysis.sentiment import SentimentAnalyzer
from outluna.analysis.technical import TechnicalAnalyzer
from outluna.data.gateway import DataGateway
from outluna.data.models import AnalysisReport, AnalyzerResult


class AnalysisOrchestrator:
    """分析编排器。

    按配置顺序执行多个分析器，并可选调用 LLM 进行综合研判。
    """

    def __init__(
        self,
        gateway: DataGateway,
        analyzers: list[AnalyzerBase] | None = None,
        enable_llm: bool = True,
        llm_provider: Any | None = None,
    ):
        self.gateway = gateway
        self.analyzers = analyzers or self._default_analyzers(gateway, enable_llm, llm_provider)

    def _default_analyzers(
        self,
        gateway: DataGateway,
        enable_llm: bool,
        llm_provider: Any | None = None,
    ) -> list[AnalyzerBase]:
        """默认分析器组合。"""
        analyzers: list[AnalyzerBase] = [
            FundamentalsAnalyzer(gateway),
            CompanyAnalyzer(gateway),
            InstitutionalAnalyzer(gateway),
            TechnicalAnalyzer(gateway),
            SentimentAnalyzer(gateway),
        ]
        if enable_llm:
            analyzers.append(LLMAnalyst(gateway, llm_provider=llm_provider))
        return analyzers

    async def analyze(self, symbol: str, strategy_name: str = "") -> AnalysisReport:
        """执行完整分析流程。

        按配置顺序执行各分析器，汇总各维度关键信号。
        若启用 LLM，则由 LLMAnalyst 生成结构化投资建议；
        若未启用 LLM，则使用规则化综合方法生成风险等级与投资建议。
        """
        context = AnalysisContext(symbol=symbol)

        # 预加载 K 线数据供各分析器共享
        try:
            end_date = datetime.now().strftime("%Y-%m-%d")
            start_date = (datetime.now() - pd.Timedelta(days=120)).strftime("%Y-%m-%d")
            context.kline_data = self.gateway.get_ohlcv(
                symbol, start_date=start_date, end_date=end_date, bars=60
            )
        except Exception:
            context.kline_data = None

        for analyzer in self.analyzers:
            result = await analyzer.analyze(symbol, context)
            context.add_result(result)

        # 生成风险等级和投资建议
        final_llm_result = context.results.get("llm")
        risk_rating = ""
        recommendation = ""
        if final_llm_result:
            recommendation = final_llm_result.summary
            for signal in final_llm_result.signals:
                if signal.startswith("风险等级："):
                    risk_rating = signal.replace("风险等级：", "")
                    break
            if not risk_rating:
                risk_rating = final_llm_result.data.get("risk_rating", "")
        else:
            # 未启用 LLM 时，基于规则生成结论
            risk_rating = self._rule_risk_rating(context.results)
            recommendation = self._build_rule_based_recommendation(context.results)

        return AnalysisReport(
            report_id=str(uuid4())[:8],
            symbol=symbol,
            created_at=datetime.now(),
            strategy_name=strategy_name,
            results=context.results,
            llm_summary=recommendation if "llm" in context.results else "",
            risk_rating=risk_rating,
            recommendation=recommendation,
        )

    def _rule_risk_rating(self, results: dict[str, AnalyzerResult]) -> str:
        """基于信号数量给出风险等级。"""
        positive = 0
        negative = 0
        for result in results.values():
            for signal in result.signals:
                if any(word in signal for word in ["失败", "亏损", "偏高", "负面", "偏弱", "净流出"]):
                    negative += 1
                elif any(word in signal for word in ["良好", "为正", "合理", "正面", "偏强", "净流入"]):
                    positive += 1
        if positive > negative + 2:
            return "中低风险"
        if positive > negative:
            return "中等风险"
        if negative > positive + 2:
            return "高风险"
        return "中高风险"

    def _build_rule_based_recommendation(
        self, results: dict[str, AnalyzerResult]
    ) -> str:
        """未启用 LLM 时，基于规则生成投资建议摘要。"""
        positive_signals = 0
        negative_signals = 0
        for result in results.values():
            for signal in result.signals:
                if any(word in signal for word in ["失败", "亏损", "偏高", "负面", "偏弱", "净流出"]):
                    negative_signals += 1
                elif any(word in signal for word in ["良好", "为正", "合理", "正面", "偏强", "净流入"]):
                    positive_signals += 1

        if positive_signals > negative_signals + 2:
            parts = ["多维度存在积极信号，可作为重点关注标的。"]
        elif positive_signals > negative_signals:
            parts = ["存在部分积极信号，但需进一步观察确认，建议谨慎跟踪。"]
        else:
            parts = ["存在较多风险或不确定性信号，建议回避或继续观察。"]

        signal_notes = []
        for dimension, result in results.items():
            if result.signals:
                signal_notes.append(f"{dimension}：{result.signals[0]}")
        if signal_notes:
            parts.append("主要信号：" + "；".join(signal_notes[:3]) + "。")

        return "".join(parts)
