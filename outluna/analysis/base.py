"""分析器基类。"""

from abc import ABC, abstractmethod
from typing import Any

from outluna.data.gateway import DataGateway
from outluna.data.models import AnalysisContext, AnalyzerResult


class AnalyzerBase(ABC):
    """分析器基类。"""

    dimension: str = ""

    def __init__(self, gateway: DataGateway):
        self.gateway = gateway

    @abstractmethod
    async def analyze(self, symbol: str, context: AnalysisContext | None = None) -> AnalyzerResult:
        """执行分析。"""

    def _safe_get(self, data: dict[str, Any], key: str, default: Any = "") -> Any:
        """安全获取字典值。"""
        return data.get(key, default) if isinstance(data, dict) else default
