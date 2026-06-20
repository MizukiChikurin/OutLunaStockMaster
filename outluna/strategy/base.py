"""策略引擎基类与注册表。"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

import pandas as pd

from outluna.data.models import DataRequirement, ScanResult


class StrategyBase(ABC):
    """选股策略基类。

    所有选股策略必须继承此类，并实现 match 方法和 required_data 属性。
    """

    name: str = ""
    description: str = ""
    version: str = "1.0"

    def __init__(self, params: dict[str, Any] | None = None):
        """初始化策略参数。"""
        self.params = params or {}
        self._apply_params()

    def _apply_params(self) -> None:
        """将参数字典应用到实例属性。"""
        for key, value in self.params.items():
            if hasattr(self, key):
                setattr(self, key, value)

    @abstractmethod
    def match(self, symbol: str, df: pd.DataFrame) -> bool:
        """判断单只股票是否匹配策略特征。"""

    @property
    @abstractmethod
    def required_data(self) -> DataRequirement:
        """返回策略所需的数据规格。"""

    def get_screen_keyword(self) -> str | None:
        """返回用于 Kimi 智能选股引擎的粗筛条件，None 表示不使用粗筛。"""
        return None

    def evaluate(self, symbol: str, df: pd.DataFrame) -> ScanResult | None:
        """评估单只股票，返回扫描结果或 None。"""
        if self.match(symbol, df):
            trigger_data = (
                df.iloc[-1].to_dict() if not df.empty else {}
            )
            return ScanResult(
                symbol=symbol,
                strategy_name=self.name,
                matched_at=datetime.now(),
                match_score=1.0,
                trigger_data={str(k): v for k, v in trigger_data.items()},
            )
        return None


class StrategyRegistry:
    """策略注册表。"""

    def __init__(self):
        self._strategies: dict[str, type[StrategyBase]] = {}

    def register(self, strategy_cls: type[StrategyBase]) -> type[StrategyBase]:
        """注册策略类。"""
        if not strategy_cls.name:
            raise ValueError("策略类必须设置 name 属性")
        self._strategies[strategy_cls.name] = strategy_cls
        return strategy_cls

    def get(self, name: str) -> type[StrategyBase]:
        """根据名称获取策略类。"""
        if name not in self._strategies:
            raise KeyError(f"未找到策略：{name}")
        return self._strategies[name]

    def list_strategies(self) -> list[dict[str, str]]:
        """列出所有已注册策略。"""
        return [
            {"name": cls.name, "description": cls.description, "version": cls.version}
            for cls in self._strategies.values()
        ]

    def build(self, name: str, params: dict[str, Any] | None = None) -> StrategyBase:
        """构建策略实例。"""
        cls = self.get(name)
        return cls(params)


# 全局策略注册表
registry = StrategyRegistry()
