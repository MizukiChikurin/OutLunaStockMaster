"""基本面分析器。"""

import pandas as pd

from outluna.analysis.base import AnalyzerBase
from outluna.analysis.context import AnalysisContext
from outluna.data.gateway import DataGateway
from outluna.data.models import AnalyzerResult


class FundamentalsAnalyzer(AnalyzerBase):
    """基本面分析器。

    基于 Kimi Datasource 的财务指标、公司信息和盈利预测，
    提取基本面关键信号，供 LLM 进行综合研判。
    """

    dimension = "fundamentals"

    def __init__(self, gateway: DataGateway):
        super().__init__(gateway)

    async def analyze(self, symbol: str, context: AnalysisContext | None = None) -> AnalyzerResult:
        """执行基本面分析。"""
        signals: list[str] = []

        try:
            fin_index = self.gateway.get_financial_index(symbol)
        except Exception as exc:
            return AnalyzerResult(
                dimension=self.dimension,
                data={"error": str(exc)},
                signals=["财务指标获取失败"],
                summary="财务指标获取失败，无法完成基本面分析。",
            )

        try:
            company_info = self.gateway.get_company_info(symbol)
        except Exception:
            company_info = {}

        try:
            forecast = self.gateway.get_forecast(symbol)
        except Exception:
            forecast = pd.DataFrame()

        data = {
            "financial_index": fin_index,
            "company_info": company_info,
            "forecast": forecast,
        }

        if not fin_index.empty:
            latest = fin_index.iloc[-1]
            roe = self._extract_value(latest, "roe")
            if roe and roe > 0.15:
                signals.append(f"ROE 较高（{roe:.2%}），盈利能力良好")
            elif roe and roe > 0:
                signals.append(f"ROE 为正（{roe:.2%}）")
            elif roe is not None:
                signals.append(f"ROE 较低或亏损（{roe:.2%}）")

            revenue_growth = self._extract_value(latest, "revenue_growth")
            if revenue_growth and revenue_growth > 0.2:
                signals.append(f"营收增速较快（{revenue_growth:.2%}）")
            elif revenue_growth and revenue_growth > 0:
                signals.append(f"营收增速为正（{revenue_growth:.2%}）")

            pe = self._extract_value(latest, "pe")
            if pe and 0 < pe < 30:
                signals.append(f"PE 处于合理区间（{pe:.2f}）")
            elif pe and pe > 100:
                signals.append(f"PE 偏高（{pe:.2f}），估值压力大")

        if not forecast.empty:
            forecast_latest = forecast.iloc[-1]
            forecast_profit = self._extract_value(forecast_latest, "net_profit_forecast")
            if forecast_profit and forecast_profit > 0:
                signals.append(f"盈利预测净利润为正（{forecast_profit:,.0f}）")
            else:
                forecast_growth = self._extract_value(forecast_latest, "profit_growth_forecast")
                if forecast_growth and forecast_growth > 0:
                    signals.append(f"盈利预测净利润增速为正（{forecast_growth:.2%}）")

        market_cap = company_info.get("ths_the_total_market_value_stock")
        if market_cap:
            data["market_cap"] = market_cap

        summary = "关键信号：" + "；".join(signals) if signals else "暂无明确信号。"

        return AnalyzerResult(
            dimension=self.dimension,
            data=data,
            signals=signals,
            summary=summary,
        )

    def _extract_value(self, row: pd.Series, key: str) -> float | None:
        """从财务指标行中提取数值。"""
        candidates = [key]
        if key == "roe":
            candidates = ["roe", "ROE", "净资产收益率", "ths_roe"]
        elif key == "revenue_growth":
            candidates = ["revenue_growth", "营收增长率", "ths_yysr_growth_rate"]
        elif key == "pe":
            candidates = ["pe", "PE", "市盈率", "ths_pe_ttm"]
        elif key == "net_profit_forecast":
            candidates = ["net_profit_forecast", "forecast_net_profit", "预测净利润", "ths_net_profit_forecast"]
        elif key == "profit_growth_forecast":
            candidates = ["profit_growth_forecast", "forecast_profit_growth", "预测净利润增速", "ths_profit_growth_forecast"]

        for candidate in candidates:
            if candidate in row.index:
                value = row[candidate]
                if value is not None and pd.notna(value):
                    try:
                        return float(value)
                    except (ValueError, TypeError):
                        continue
        return None
