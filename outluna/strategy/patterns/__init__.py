"""内置 K 线形态策略集合。"""

# 自动导入并注册所有内置策略
from outluna.strategy.patterns.doji import DojiStrategy
from outluna.strategy.patterns.engulfing import EngulfingStrategy
from outluna.strategy.patterns.short_term_swing import ShortTermSwingStrategy

__all__ = ["DojiStrategy", "EngulfingStrategy", "ShortTermSwingStrategy"]
