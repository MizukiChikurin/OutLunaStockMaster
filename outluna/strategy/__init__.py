"""策略引擎包入口。"""

# 自动注册内置策略
import outluna.strategy.patterns  # noqa: F401
import outluna.strategy.user_driven  # noqa: F401
from outluna.strategy.base import StrategyBase, StrategyRegistry, registry
from outluna.strategy.scanner import StockScanner

__all__ = ["StrategyBase", "StrategyRegistry", "registry", "StockScanner"]
