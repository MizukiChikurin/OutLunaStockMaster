"""策略引擎基类与注册表。"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

import pandas as pd

from outluna.data.models import DataRequirement, ScanResult


def _is_json_serializable(value: Any) -> bool:
    """粗略判断值是否可被 json 序列化。"""
    if value is None:
        return True
    if isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, (list, tuple)):
        return all(_is_json_serializable(v) for v in value)
    if isinstance(value, dict):
        return all(_is_json_serializable(v) for v in value.values())
    return False


class StrategyBase(ABC):
    """选股策略基类。

    所有选股策略必须继承此类，并实现 match 方法和 required_data 属性。
    """

    name: str = ""
    description: str = ""
    version: str = "1.0"

    def __init__(self, params: dict[str, Any] | None = None):
        """初始化策略参数。"""
        self._raw_params = params or {}
        self.params = self._build_serializable_params(self._raw_params)
        self._apply_params()

    @staticmethod
    def _build_serializable_params(params: dict[str, Any]) -> dict[str, Any]:
        """过滤掉不可 JSON 序列化的对象（如 LLM Provider），避免保存报告时出错。"""
        return {k: v for k, v in params.items() if _is_json_serializable(v)}

    def _apply_params(self) -> None:
        """将参数字典应用到实例属性。"""
        for key, value in self._raw_params.items():
            if hasattr(self, key):
                setattr(self, key, value)

    @property
    def serializable_params(self) -> dict[str, Any]:
        """返回可用于报告保存的可序列化参数。"""
        return self.params

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

    async def prepare_data(self, gateway) -> Any:
        """准备策略所需的原始数据。

        默认实现返回 None，表示仍使用扫描器的传统 OHLCV 逐只获取流程。
        子类可重写此方法，通过 gateway 一次性获取全市场快照、技术指标等，
        再由 evaluate_batch 完成批量评估。
        """
        return None

    async def evaluate_batch(self, data: Any) -> list[ScanResult]:
        """批量评估股票并返回扫描结果列表。

        当 prepare_data 返回非 None 数据时，扫描器会调用本方法替代逐只 match。
        默认实现返回空列表，子类应重写以实现批量选股逻辑。
        """
        return []

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
