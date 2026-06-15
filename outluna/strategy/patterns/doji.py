"""十字星策略。"""

import pandas as pd

from outluna.data.models import DataRequirement
from outluna.strategy.base import StrategyBase, registry


@registry.register
class DojiStrategy(StrategyBase):
    """十字星策略：识别实体部分极小、多空平衡的 K 线形态。"""

    name = "十字星"
    description = "识别 K 线实体极小、开盘价与收盘价接近的形态，反映多空力量均衡。"
    version = "1.0"

    def __init__(self, params: dict | None = None):
        self.body_ratio: float = 0.1
        self.min_amplitude: float = 0.01
        super().__init__(params)

    def match(self, symbol: str, df: pd.DataFrame) -> bool:
        """判断最新一根 K 线是否为十字星。"""
        if len(df) < 1:
            return False

        latest = df.iloc[-1]
        open_price = float(latest["open"])
        high_price = float(latest["high"])
        low_price = float(latest["low"])
        close_price = float(latest["close"])

        body = abs(close_price - open_price)
        range_total = high_price - low_price

        if range_total == 0:
            return False

        amplitude = range_total / close_price
        return (body / range_total <= self.body_ratio) and (amplitude >= self.min_amplitude)

    @property
    def required_data(self) -> DataRequirement:
        """十字星只需最近 5 根日 K 线。"""
        return DataRequirement(period="1d", bars=5)

    def get_screen_keyword(self) -> str | None:
        """十字星暂不定义粗筛条件。"""
        return None
