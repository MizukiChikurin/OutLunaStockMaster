"""技术面分析器。

以 Kimi Datasource 的实时行情与技术指标为主要渠道，
结合历史 K 线数据提取技术面信号。
"""

from outluna.analysis.base import AnalyzerBase
from outluna.analysis.context import AnalysisContext
from outluna.data.gateway import DataGateway
from outluna.data.models import AnalyzerResult


class TechnicalAnalyzer(AnalyzerBase):
    """技术面分析器。"""

    dimension = "technical"

    def __init__(self, gateway: DataGateway):
        super().__init__(gateway)

    async def analyze(self, symbol: str, context: AnalysisContext | None = None) -> AnalyzerResult:
        """执行技术面分析。"""
        signals: list[str] = []
        data: dict = {}

        try:
            realtime = self.gateway.get_realtime_price([symbol])
            data["realtime"] = realtime
            if not realtime.empty:
                latest = realtime.iloc[-1]
                price = latest.get("close") or latest.get("price") or latest.get("latest")
                change_pct = latest.get("change_pct")
                if price is not None:
                    signals.append(f"最新价：{price}")
                if change_pct is not None:
                    try:
                        change_pct_val = float(change_pct)
                        if change_pct_val > 5:
                            signals.append(f"当日涨幅较大（{change_pct_val:.2f}%）")
                        elif change_pct_val < -5:
                            signals.append(f"当日跌幅较大（{change_pct_val:.2f}%）")
                        else:
                            signals.append(f"当日涨跌幅：{change_pct_val:.2f}%")
                    except (ValueError, TypeError):
                        pass
            else:
                signals.append("未获取到实时行情")
        except Exception as exc:
            signals.append(f"实时行情获取失败：{exc}")

        try:
            tech = self.gateway.get_realtime_tech([symbol])
            data["realtime_tech"] = tech
            if not tech.empty:
                signals.append("实时技术指标已获取")
                latest = tech.iloc[-1]
                price = None
                ma5 = None
                ma10 = None
                for col in ["close", "price", "latest"]:
                    if col in latest:
                        price = latest[col]
                        break
                for col in ["ma5", "MA5"]:
                    if col in latest:
                        ma5 = latest[col]
                        break
                for col in ["ma10", "MA10"]:
                    if col in latest:
                        ma10 = latest[col]
                        break
                try:
                    price_val = float(price) if price is not None else None
                    ma5_val = float(ma5) if ma5 is not None else None
                    ma10_val = float(ma10) if ma10 is not None else None
                    if price_val is not None and ma5_val is not None and ma10_val is not None:
                        if price_val > ma5_val > ma10_val:
                            signals.append("价格位于 MA5、MA10 之上，短期趋势偏强")
                        elif price_val < ma5_val < ma10_val:
                            signals.append("价格位于 MA5、MA10 之下，短期趋势偏弱")
                except (ValueError, TypeError):
                    pass
            else:
                signals.append("未获取到实时技术指标")
        except Exception as exc:
            signals.append(f"实时技术指标获取失败：{exc}")

        summary = "关键信号：" + "；".join(signals) if signals else "暂无明确信号。"

        return AnalyzerResult(
            dimension=self.dimension,
            data=data,
            signals=signals,
            summary=summary,
        )
