"""全市场扫描器。"""

from datetime import datetime, timedelta
from typing import Any, cast

import pandas as pd

from outluna.data.gateway import DataGateway
from outluna.data.models import ScanReport, ScanResult
from outluna.strategy.base import StrategyBase
from outluna.utils.logger import setup_logging

logger = setup_logging()


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

        优先调用数据网关的批量接口（Kimi Datasource 每次最多 10 只），
        若批量接口不可用则自动降级为逐只获取。
        """
        req = self.strategy.required_data

        # 计算日期范围
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=req.bars * 3)).strftime("%Y-%m-%d")

        try:
            ohlcv_map = self.gateway.get_ohlcv_multi(
                symbols,
                period=req.period,
                start_date=start_date,
                end_date=end_date,
                bars=req.bars,
                adjust=req.adjust,
            )
            # 过滤满足最小 K 线数量的结果
            return {
                symbol: df
                for symbol, df in ohlcv_map.items()
                if not df.empty and len(df) >= req.bars
            }
        except Exception as exc:
            print(f"批量获取 K 线失败，降级为逐只获取：{exc}")

        # 降级：逐只获取
        result: dict[str, pd.DataFrame] = {}
        for symbol in symbols:
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
            except Exception as inner_exc:
                print(f"获取 {symbol} K 线失败：{inner_exc}")
        return result

    async def scan(
        self,
        universe: list[str] | None = None,
        max_candidates: int | None = None,
    ) -> ScanReport:
        """执行策略扫描。"""
        # 若策略实现了批量数据准备与评估，优先走批量流程
        batch_data = await self._try_prepare_batch_data()
        if batch_data is not None:
            return await self._run_batch_scan(batch_data)

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
            strategy_params=self.strategy.serializable_params,
            created_at=datetime.now(),
            matches=matches,
            total_scanned=len(candidates),
        )

    async def _try_prepare_batch_data(self) -> Any | None:
        """尝试调用策略的批量数据准备方法。"""
        try:
            data = await self.strategy.prepare_data(self.gateway)
            if data is not None:
                return data
        except Exception as exc:
            logger.warning(f"策略批量数据准备失败，回退到传统扫描：{exc}")
        return None

    async def _run_batch_scan(self, data: Any) -> ScanReport:
        """执行策略批量评估。"""
        matches = await self.strategy.evaluate_batch(data)
        # 若策略提供了报告构建方法，则使用它以获得完整分类报告
        if hasattr(self.strategy, "build_scan_report"):
            report = self.strategy.build_scan_report(self._generate_report_id())
            return cast(ScanReport, report)
        return ScanReport(
            report_id=self._generate_report_id(),
            strategy_name=self.strategy.name,
            strategy_params=self.strategy.serializable_params,
            created_at=datetime.now(),
            matches=matches,
            total_scanned=len(matches),
        )

    def _generate_report_id(self) -> str:
        """生成报告 ID。"""
        from uuid import uuid4
        return str(uuid4())[:8]
