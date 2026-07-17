"""策略引擎包入口。"""

# 自动注册用户自定义选股策略与固定策略选股
import outluna.strategy.fixed  # noqa: F401
import outluna.strategy.user_driven  # noqa: F401
from outluna.strategy.base import StrategyBase, StrategyRegistry, registry
from outluna.strategy.scanner import StockScanner

__all__ = ["StrategyBase", "StrategyRegistry", "registry", "StockScanner"]
