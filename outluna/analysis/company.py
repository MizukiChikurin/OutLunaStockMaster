"""企业画像分析器。"""

from typing import Any

from outluna.analysis.base import AnalyzerBase
from outluna.analysis.context import AnalysisContext
from outluna.data.gateway import DataGateway
from outluna.data.models import AnalyzerResult


class CompanyAnalyzer(AnalyzerBase):
    """企业画像分析器。

    基于 Kimi Datasource 的公司信息和天眼查数据，评估企业背景与风险。
    """

    dimension = "company"

    def __init__(self, gateway: DataGateway):
        super().__init__(gateway)

    async def analyze(self, symbol: str, context: AnalysisContext | None = None) -> AnalyzerResult:
        """执行企业画像分析。"""
        signals: list[str] = []
        score = 50.0

        try:
            company_info = self.gateway.get_company_info(symbol)
        except Exception as exc:
            return AnalyzerResult(
                dimension=self.dimension,
                data={"error": str(exc)},
                signals=["公司信息获取失败"],
                score=0,
            )

        data: dict[str, Any] = {"company_info": company_info}

        # 公司属性
        name = company_info.get("ths_stock_short_name_stock", "")
        controller = company_info.get("ths_actual_controller_stock", "")
        controller_type = company_info.get("ths_actual_controller_type_stock", "")
        scale = company_info.get("ths_company_saclle_type_stock", "")
        business = company_info.get("ths_main_businuess_stock", "") or company_info.get(
            "ths_operating_scope_stock", ""
        )

        if name:
            signals.append(f"公司名称：{name}")
        if controller:
            signals.append(f"实控人：{controller}")
        if controller_type and "国资" in controller_type:
            score += 10
            signals.append(f"实控人为{controller_type}，背景较稳")
        if scale:
            signals.append(f"企业规模：{scale}")
        if business:
            signals.append(f"主营业务：{business[:50]}")

        # 股东结构
        try:
            holder_info = self.gateway.get_holder_info(symbol)
            data["holder_info"] = holder_info
            if not holder_info.empty:
                signals.append(f"机构/股东数据已获取（{len(holder_info)} 条）")
        except Exception as exc:
            signals.append(f"股东信息获取失败：{exc}")

        summary = f"企业画像评分：{score:.0f}/100。" + (
            "关键信号：" + "；".join(signals) if signals else "暂无明确信号。"
        )

        return AnalyzerResult(
            dimension=self.dimension,
            data=data,
            signals=signals,
            score=max(0, min(100, score)),
            summary=summary,
        )
