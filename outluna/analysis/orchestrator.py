"""分析编排器。"""

from datetime import datetime
from uuid import uuid4

from outluna.analysis.base import AnalyzerBase
from outluna.analysis.company import CompanyAnalyzer
from outluna.analysis.context import AnalysisContext
from outluna.analysis.fundamentals import FundamentalsAnalyzer
from outluna.analysis.institutional import InstitutionalAnalyzer
from outluna.analysis.llm_analyst import LLMAnalyst
from outluna.analysis.sentiment import SentimentAnalyzer
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
    ):
        self.gateway = gateway
        self.analyzers = analyzers or self._default_analyzers(gateway, enable_llm)

    def _default_analyzers(
        self, gateway: DataGateway, enable_llm: bool
    ) -> list[AnalyzerBase]:
        """默认分析器组合。"""
        analyzers: list[AnalyzerBase] = [
            FundamentalsAnalyzer(gateway),
            CompanyAnalyzer(gateway),
            InstitutionalAnalyzer(gateway),
            SentimentAnalyzer(gateway),
        ]
        if enable_llm:
            analyzers.append(LLMAnalyst(gateway))
        return analyzers

    async def analyze(self, symbol: str, strategy_name: str = "") -> AnalysisReport:
        """执行完整分析流程。"""
        context = AnalysisContext(symbol=symbol)

        # 预加载 K 线数据供各分析器共享
        try:
            context.kline_data = self.gateway.get_ohlcv(symbol, bars=60)
        except Exception:
            context.kline_data = None

        for analyzer in self.analyzers:
            result = await analyzer.analyze(symbol, context)
            context.add_result(result)

        # 如果最后一个不是 LLM，则额外做一次规则综合
        if not isinstance(self.analyzers[-1], LLMAnalyst):
            llm_result = await LLMAnalyst(self.gateway).analyze(symbol, context)
            context.add_result(llm_result)

        # 生成风险等级和投资建议
        llm_result = context.results.get("llm")
        risk_rating = ""
        recommendation = ""
        if llm_result:
            for signal in llm_result.signals:
                if "风险等级" in signal:
                    risk_rating = signal.replace("风险等级：", "")
            recommendation = llm_result.summary

        return AnalysisReport(
            report_id=str(uuid4())[:8],
            symbol=symbol,
            created_at=datetime.now(),
            strategy_name=strategy_name,
            results=context.results,
            llm_summary=recommendation,
            risk_rating=risk_rating,
            recommendation=recommendation,
        )
