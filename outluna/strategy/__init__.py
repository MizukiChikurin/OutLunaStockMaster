"""策略引擎包入口。"""

from outluna.strategy.base import StrategyBase, StrategyRegistry, registry
from outluna.strategy.scanner import StockScanner

# 自动注册内置策略
import outluna.strategy.patterns  # noqa: F401

__all__ = ["StrategyBase", "StrategyRegistry", "registry", "StockScanner"]
