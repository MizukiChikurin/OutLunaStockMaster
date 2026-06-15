"""吞没形态策略。"""

import pandas as pd

from outluna.data.models import DataRequirement
from outluna.strategy.base import StrategyBase, registry


@registry.register
class EngulfingStrategy(StrategyBase):
    """吞没形态策略：识别阳线/阴线完全吞没前一根 K 线的形态。"""

    name = "吞没形态"
    description = "识别当前 K 线实体完全覆盖前一根 K 线实体的反转形态。"
    version = "1.0"

    def __init__(self, params: dict | None = None):
        self.bullish_only: bool = True  # True=只看阳线吞没阴线，False=也看阴线吞没阳线
        super().__init__(params)

    def match(self, symbol: str, df: pd.DataFrame) -> bool:
        """判断是否出现吞没形态。"""
        if len(df) < 2:
            return False

        prev = df.iloc[-2]
        curr = df.iloc[-1]

        prev_open = float(prev["open"])
        prev_close = float(prev["close"])
        prev_body_top = max(prev_open, prev_close)
        prev_body_bottom = min(prev_open, prev_close)

        curr_open = float(curr["open"])
        curr_close = float(curr["close"])
        curr_body_top = max(curr_open, curr_close)
        curr_body_bottom = min(curr_open, curr_close)

        # 当前实体完全覆盖前一根实体
        is_engulfing = curr_body_top >= prev_body_top and curr_body_bottom <= prev_body_bottom
        if not is_engulfing:
            return False

        if self.bullish_only:
            # 阳线吞没阴线：前跌后涨
            return prev_close < prev_open and curr_close > curr_open
        return True

    @property
    def required_data(self) -> DataRequirement:
        """吞没形态需要最近 5 根日 K 线。"""
        return DataRequirement(period="1d", bars=5)

    def get_screen_keyword(self) -> str | None:
        """吞没形态暂不定义粗筛条件。"""
        return None
