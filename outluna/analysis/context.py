"""分析上下文。"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from outluna.data.models import AnalyzerResult


@dataclass
class AnalysisContext:
    """分析上下文，用于在多个分析器之间传递数据。"""

    symbol: str
    start_time: datetime = field(default_factory=datetime.now)
    results: dict[str, AnalyzerResult] = field(default_factory=dict)
    kline_data: Any = None
    summary: str = ""

    def add_result(self, result: AnalyzerResult) -> None:
        """添加分析结果。"""
        self.results[result.dimension] = result

    def get_result(self, dimension: str) -> AnalyzerResult | None:
        """获取指定维度的分析结果。"""
        return self.results.get(dimension)

    def to_report_data(self) -> dict[str, Any]:
        """转换为报告数据。"""
        return {
            "symbol": self.symbol,
            "results": self.results,
            "summary": self.summary,
        }
