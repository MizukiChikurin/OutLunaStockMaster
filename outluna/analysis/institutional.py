"""主力动向分析器。

以 Kimi Datasource 的机构持仓、股东信息为主要分析渠道，
以 akshare 的资金流向、龙虎榜、融资融券、北向资金为补充，
提取股票主力动向信号。
"""

from outluna.analysis.base import AnalyzerBase
from outluna.analysis.context import AnalysisContext
from outluna.data.gateway import DataGateway
from outluna.data.models import AnalyzerResult


class InstitutionalAnalyzer(AnalyzerBase):
    """主力动向分析器。"""

    dimension = "institutional"

    def __init__(self, gateway: DataGateway):
        super().__init__(gateway)

    async def analyze(self, symbol: str, context: AnalysisContext | None = None) -> AnalyzerResult:
        """执行主力动向分析。"""
        signals: list[str] = []
        data: dict = {}
        has_main_data = False

        try:
            holder_info = self.gateway.get_holder_info(symbol)
            data["holder_info"] = holder_info
            if not holder_info.empty:
                has_main_data = True
                signals.append(f"机构/股东数据已获取（{len(holder_info)} 条）")
                if "institutional_holding" in holder_info.columns or "hold_ratio" in holder_info.columns:
                    latest_holder = holder_info.iloc[-1]
                    ratio = latest_holder.get("hold_ratio") or latest_holder.get("institutional_holding")
                    if ratio is not None:
                        try:
                            ratio_val = float(ratio)
                            if ratio_val > 0.3:
                                signals.append(f"机构/大股东持股比例较高（{ratio_val:.2%}）")
                            elif ratio_val > 0.1:
                                signals.append(f"机构/大股东持股比例适中（{ratio_val:.2%}）")
                        except (ValueError, TypeError):
                            pass
            else:
                signals.append("未获取到机构持仓数据")
        except Exception as exc:
            signals.append(f"机构持仓获取失败：{exc}")

        try:
            capital_flow = self.gateway.get_capital_flow(symbol, days=20)
            data["capital_flow"] = capital_flow
            if not capital_flow.empty and "net_inflow" in capital_flow.columns:
                has_main_data = True
                recent_net = capital_flow["net_inflow"].sum()
                if recent_net > 0:
                    signals.append(f"近 20 日资金净流入：{recent_net:,.0f}")
                else:
                    signals.append(f"近 20 日资金净流出：{recent_net:,.0f}")
        except Exception:
            pass

        try:
            dragon_tiger = self.gateway.get_dragon_tiger(symbol, days=5)
            data["dragon_tiger"] = dragon_tiger
            if not dragon_tiger.empty:
                has_main_data = True
                signals.append(f"近 5 日龙虎榜上榜 {len(dragon_tiger)} 次")
        except Exception:
            pass

        try:
            margin = self.gateway.get_margin_balance(symbol, days=20)
            data["margin"] = margin
            if not margin.empty:
                has_main_data = True
                signals.append("融资融券数据已获取")
        except Exception:
            pass

        try:
            northbound = self.gateway.get_northbound_flow(symbol, days=20)
            data["northbound"] = northbound
            if not northbound.empty:
                has_main_data = True
                signals.append("北向资金数据已获取")
        except Exception:
            pass

        if not has_main_data:
            signals.append("主力动向数据暂不完整，以 Kimi Datasource 机构持仓和实时行情为主")

        summary = "关键信号：" + "；".join(signals) if signals else "暂无明确信号。"

        return AnalyzerResult(
            dimension=self.dimension,
            data=data,
            signals=signals,
            summary=summary,
        )
