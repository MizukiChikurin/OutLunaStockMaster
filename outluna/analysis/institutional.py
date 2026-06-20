"""主力动向分析器。"""

from outluna.analysis.base import AnalyzerBase
from outluna.analysis.context import AnalysisContext
from outluna.data.gateway import DataGateway
from outluna.data.models import AnalyzerResult


class InstitutionalAnalyzer(AnalyzerBase):
    """主力动向分析器。

    基于 akshare 补充的 A 股特色数据，分析资金流向、龙虎榜、融资融券、北向资金。
    """

    dimension = "institutional"

    def __init__(self, gateway: DataGateway):
        super().__init__(gateway)

    async def analyze(self, symbol: str, context: AnalysisContext | None = None) -> AnalyzerResult:
        """执行主力动向分析。"""
        signals: list[str] = []
        score = 50.0
        data: dict = {}

        # 1. 资金流向
        try:
            capital_flow = self.gateway.get_capital_flow(symbol, days=20)
            data["capital_flow"] = capital_flow
            if not capital_flow.empty:
                recent_net = capital_flow["net_inflow"].sum() if "net_inflow" in capital_flow.columns else 0
                if recent_net > 0:
                    score += 10
                    signals.append(f"近 20 日资金净流入：{recent_net:,.0f}")
                else:
                    score -= 5
                    signals.append(f"近 20 日资金净流出：{recent_net:,.0f}")
        except Exception as exc:
            signals.append(f"资金流向获取失败：{exc}")

        # 2. 龙虎榜
        try:
            dragon_tiger = self.gateway.get_dragon_tiger(symbol, days=5)
            data["dragon_tiger"] = dragon_tiger
            if not dragon_tiger.empty:
                signals.append(f"近 5 日龙虎榜上榜 {len(dragon_tiger)} 次")
        except Exception as exc:
            signals.append(f"龙虎榜获取失败：{exc}")

        # 3. 融资融券
        try:
            margin = self.gateway.get_margin_balance(symbol, days=20)
            data["margin"] = margin
            if not margin.empty:
                signals.append("融资融券数据已获取")
        except Exception as exc:
            signals.append(f"融资融券获取失败：{exc}")

        # 4. 北向资金
        try:
            northbound = self.gateway.get_northbound_flow(symbol, days=20)
            data["northbound"] = northbound
            if not northbound.empty:
                signals.append("北向资金数据已获取")
        except Exception as exc:
            signals.append(f"北向资金获取失败：{exc}")

        summary = f"主力动向评分：{score:.0f}/100。" + (
            "关键信号：" + "；".join(signals) if signals else "暂无明确信号。"
        )

        return AnalyzerResult(
            dimension=self.dimension,
            data=data,
            signals=signals,
            score=max(0, min(100, score)),
            summary=summary,
        )
