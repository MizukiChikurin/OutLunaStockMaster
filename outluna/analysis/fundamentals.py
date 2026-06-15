"""基本面分析器。"""

import pandas as pd

from outluna.analysis.base import AnalyzerBase
from outluna.data.gateway import DataGateway
from outluna.data.models import AnalysisContext, AnalyzerResult


class FundamentalsAnalyzer(AnalyzerBase):
    """基本面分析器。

    基于 Kimi Datasource 的财务指标和公司信息，评估股票基本面健康度。
    """

    dimension = "fundamentals"

    def __init__(self, gateway: DataGateway):
        super().__init__(gateway)

    async def analyze(self, symbol: str, context: AnalysisContext | None = None) -> AnalyzerResult:
        """执行基本面分析。"""
        signals: list[str] = []
        score = 50.0  # 基础分 50

        try:
            fin_index = self.gateway.get_financial_index(symbol)
        except Exception as exc:
            return AnalyzerResult(
                dimension=self.dimension,
                data={"error": str(exc)},
                signals=["财务指标获取失败"],
                score=0,
            )

        try:
            company_info = self.gateway.get_company_info(symbol)
        except Exception:
            company_info = {}

        data = {
            "financial_index": fin_index,
            "company_info": company_info,
        }

        # 简单的评分逻辑（后续可扩展）
        if not fin_index.empty:
            latest = fin_index.iloc[-1]
            # 盈利能力：ROE
            roe = self._extract_value(latest, "roe")
            if roe and roe > 0.15:
                score += 15
                signals.append(f"ROE 较高（{roe:.2%}），盈利能力良好")
            elif roe and roe > 0:
                score += 5
                signals.append(f"ROE 为正（{roe:.2%}）")
            elif roe is not None:
                score -= 10
                signals.append(f"ROE 较低或亏损（{roe:.2%}）")

            # 成长性：营收增速
            revenue_growth = self._extract_value(latest, "revenue_growth")
            if revenue_growth and revenue_growth > 0.2:
                score += 10
                signals.append(f"营收增速较快（{revenue_growth:.2%}）")
            elif revenue_growth and revenue_growth > 0:
                score += 5

            # 估值：PE
            pe = self._extract_value(latest, "pe")
            if pe and 0 < pe < 30:
                score += 10
                signals.append(f"PE 处于合理区间（{pe:.2f}）")
            elif pe and pe > 100:
                score -= 10
                signals.append(f"PE 偏高（{pe:.2f}），估值压力大")

        # 公司信息
        market_cap = company_info.get("ths_the_total_market_value_stock")
        if market_cap:
            data["market_cap"] = market_cap

        summary = f"基本面评分：{score:.0f}/100。" + (
            "关键信号：" + "；".join(signals) if signals else "暂无明确信号。"
        )

        return AnalyzerResult(
            dimension=self.dimension,
            data=data,
            signals=signals,
            score=max(0, min(100, score)),
            summary=summary,
        )

    def _extract_value(self, row: pd.Series, key: str) -> float | None:
        """从财务指标行中提取数值。"""
        # 兼容多种可能的列名
        candidates = [key]
        if key == "roe":
            candidates = ["roe", "ROE", "净资产收益率", "ths_roe"]
        elif key == "revenue_growth":
            candidates = ["revenue_growth", "营收增长率", "ths_yysr_growth_rate"]
        elif key == "pe":
            candidates = ["pe", "PE", "市盈率", "ths_pe_ttm"]

        for candidate in candidates:
            if candidate in row.index:
                val = row[candidate]
                if pd.notna(val):
                    try:
                        return float(val)
                    except (ValueError, TypeError):
                        continue
        return None
