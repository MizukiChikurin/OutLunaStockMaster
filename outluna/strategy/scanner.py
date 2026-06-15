"""全市场扫描器。"""

from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from outluna.data.gateway import DataGateway
from outluna.data.models import ScanReport, ScanResult
from outluna.strategy.base import StrategyBase


class StockScanner:
    """股票扫描器。

    负责加载策略、获取候选股票池、批量拉取 K 线、执行策略匹配。
    """

    def __init__(self, gateway: DataGateway, strategy: StrategyBase):
        self.gateway = gateway
        self.strategy = strategy

    def _get_candidate_symbols(self, universe: list[str] | None = None) -> list[str]:
        """获取候选股票池。"""
        if universe:
            return universe

        # 尝试使用策略粗筛条件
        keyword = self.strategy.get_screen_keyword()
        if keyword:
            try:
                df = self.gateway.screen_stocks(keyword)
                if "symbol" in df.columns:
                    return df["symbol"].tolist()
            except Exception as exc:
                print(f"粗筛失败，回退到全市场列表：{exc}")

        # 默认获取 A 股列表
        return self.gateway.get_stock_list("A")

    def _fetch_ohlcv_batch(
        self,
        symbols: list[str],
        period: str,
        bars: int,
    ) -> dict[str, pd.DataFrame]:
        """批量获取 K 线数据。

        Kimi Datasource 历史行情每次最多 10 只，需分批。
        """
        result: dict[str, pd.DataFrame] = {}
        batch_size = 10
        req = self.strategy.required_data

        # 计算日期范围
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=req.bars * 3)).strftime("%Y-%m-%d")

        for i in range(0, len(symbols), batch_size):
            batch = symbols[i : i + batch_size]
            for symbol in batch:
                try:
                    df = self.gateway.get_ohlcv(
                        symbol,
                        period=req.period,
                        start_date=start_date,
                        end_date=end_date,
                        bars=req.bars,
                        adjust=req.adjust,
                    )
                    if not df.empty and len(df) >= req.bars:
                        result[symbol] = df
                except Exception as exc:
                    print(f"获取 {symbol} K 线失败：{exc}")

        return result

    def scan(
        self,
        universe: list[str] | None = None,
        max_candidates: int | None = None,
    ) -> ScanReport:
        """执行策略扫描。"""
        candidates = self._get_candidate_symbols(universe)
        if max_candidates:
            candidates = candidates[:max_candidates]

        ohlcv_map = self._fetch_ohlcv_batch(candidates, "1d", self.strategy.required_data.bars)

        matches: list[ScanResult] = []
        for symbol, df in ohlcv_map.items():
            result = self.strategy.evaluate(symbol, df)
            if result:
                matches.append(result)

        return ScanReport(
            report_id=self._generate_report_id(),
            strategy_name=self.strategy.name,
            strategy_params=self.strategy.params,
            created_at=datetime.now(),
            matches=matches,
            total_scanned=len(candidates),
        )

    def _generate_report_id(self) -> str:
        """生成报告 ID。"""
        from uuid import uuid4
        return str(uuid4())[:8]
