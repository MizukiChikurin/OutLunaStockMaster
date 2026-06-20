"""回测引擎。"""

from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

import pandas as pd

from outluna.backtest.metrics import calculate_metrics
from outluna.backtest.portfolio import Portfolio, Position
from outluna.config import settings
from outluna.data.gateway import DataGateway
from outluna.data.models import BacktestReport, TradeRecord
from outluna.strategy import registry
from outluna.strategy.base import StrategyBase


class BacktestEngine:
    """回测引擎。

    按交易日遍历，每日执行策略扫描，命中后模拟买入，持有期间检查止盈止损。
    """

    def __init__(
        self,
        strategy: StrategyBase,
        start_date: datetime,
        end_date: datetime,
        initial_capital: float | None = None,
        position_limit: int | None = None,
        hold_days: int | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        trailing_stop: float | None = None,
        gateway: DataGateway | None = None,
        max_universe: int | None = None,
    ):
        self.strategy = strategy
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = initial_capital or settings.default_initial_capital
        self.position_limit = position_limit or settings.default_position_limit
        self.hold_days = hold_days or settings.default_hold_days
        self.stop_loss = stop_loss or settings.default_stop_loss
        self.take_profit = take_profit or settings.default_take_profit
        self.trailing_stop = trailing_stop or 0.0
        self.gateway = gateway or DataGateway()
        self.max_universe = max_universe or 100

    def _generate_trade_dates(self) -> list[datetime]:
        """生成交易日列表（简化版：周一至周五）。"""
        dates = []
        current = self.start_date
        while current <= self.end_date:
            if current.weekday() < 5:
                dates.append(current)
            current += timedelta(days=1)
        return dates

    def _get_price_at_date(self, df: pd.DataFrame, date: datetime) -> float | None:
        """获取指定日期的收盘价。"""
        if df.empty or "date" not in df.columns or "close" not in df.columns:
            return None
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
        target = pd.Timestamp(date).normalize()
        row = df[df["date"] == target]
        if row.empty:
            return None
        return float(row.iloc[-1]["close"])

    def run(self, universe: list[str] | None = None) -> BacktestReport:
        """执行回测。"""
        portfolio = Portfolio(initial_capital=self.initial_capital)
        trade_log: list[TradeRecord] = []
        equity_curve: list[dict[str, Any]] = []

        dates = self._generate_trade_dates()
        if len(dates) < 2:
            raise ValueError("回测区间至少需要两个交易日")

        # 预加载所有候选股票的历史数据
        symbols = universe or self.gateway.get_stock_list("A")
        # 限制候选数量，避免调用过多
        symbols = symbols[: self.max_universe]

        price_data: dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            try:
                df = self.gateway.get_ohlcv(
                    symbol,
                    start_date=self.start_date.strftime("%Y-%m-%d"),
                    end_date=self.end_date.strftime("%Y-%m-%d"),
                    bars=252,
                )
                if not df.empty:
                    price_data[symbol] = df
            except Exception as exc:
                print(f"预加载 {symbol} 数据失败：{exc}")

        for _idx, date in enumerate(dates):
            # 当前可交易价格
            today_prices: dict[str, float] = {}
            for symbol, df in price_data.items():
                price = self._get_price_at_date(df, date)
                if price:
                    today_prices[symbol] = price

            # 1. 检查止盈止损和到期平仓
            symbols_to_sell: list[tuple[str, str]] = []
            for symbol, position in list(portfolio.positions.items()):
                if symbol not in today_prices:
                    continue
                price = today_prices[symbol]
                hold_days = (date - position.buy_date).days
                pnl_ratio = position.pnl_ratio(price)

                # 最高价回撤止损
                trailing_trigger = False
                if self.trailing_stop > 0 and position.highest_price > position.buy_price:
                    trailing_trigger = (position.highest_price - price) / position.highest_price >= self.trailing_stop

                if pnl_ratio <= -self.stop_loss:
                    symbols_to_sell.append((symbol, f"止损（亏损 {pnl_ratio:.2%}）"))
                elif pnl_ratio >= self.take_profit:
                    symbols_to_sell.append((symbol, f"止盈（盈利 {pnl_ratio:.2%}）"))
                elif trailing_trigger:
                    symbols_to_sell.append((symbol, f"移动止损（回撤 {self.trailing_stop:.2%}）"))
                elif hold_days >= self.hold_days:
                    symbols_to_sell.append((symbol, f"到期平仓（持有 {hold_days} 天）"))

            for symbol, reason in symbols_to_sell:
                price = today_prices[symbol]
                pnl, _ = portfolio.sell(symbol, date, price, reason)
                trade_log.append(
                    TradeRecord(
                        symbol=symbol,
                        action="sell",
                        date=date,
                        price=price,
                        shares=0,
                        reason=reason,
                        pnl=pnl,
                    )
                )

            # 2. 执行策略扫描寻找买入信号
            available_slots = self.position_limit - len(portfolio.positions)
            if available_slots > 0:
                matched_symbols: list[str] = []
                for symbol, df in price_data.items():
                    if symbol in portfolio.positions:
                        continue
                    # 截取到当前日期的数据
                    target = pd.Timestamp(date).normalize()
                    df_before = df[pd.to_datetime(df["date"]).dt.normalize() <= target]
                    if len(df_before) >= self.strategy.required_data.bars:
                        if self.strategy.match(symbol, df_before):
                            matched_symbols.append(symbol)
                    if len(matched_symbols) >= available_slots * 3:
                        break

                # 按代码排序后等权买入（可扩展为按得分排序）
                for symbol in matched_symbols[:available_slots]:
                    price = today_prices.get(symbol)
                    if not price:
                        continue
                    weight = 1.0 / self.position_limit
                    new_position: Position | None = portfolio.buy(symbol, date, price, weight)
                    if new_position is not None:
                        trade_log.append(
                            TradeRecord(
                                symbol=symbol,
                                action="buy",
                                date=date,
                                price=price,
                                shares=new_position.shares,
                                reason=f"{self.strategy.name} 策略信号",
                            )
                        )

            # 3. 更新持仓最高价
            portfolio.update_positions(date, today_prices)

            # 4. 记录每日净值
            total_value = portfolio.total_value(today_prices)
            equity_curve.append(
                {
                    "date": date,
                    "total_value": total_value,
                    "cash": portfolio.cash,
                }
            )

        equity_df = pd.DataFrame(equity_curve)

        # 加载基准数据
        benchmark_curve = self._load_benchmark_curve()

        metrics = calculate_metrics(self.initial_capital, equity_df, trade_log, benchmark_curve=benchmark_curve)

        return BacktestReport(
            report_id=str(uuid4())[:8],
            strategy_name=self.strategy.name,
            start_date=self.start_date,
            end_date=self.end_date,
            initial_capital=self.initial_capital,
            metrics=metrics,
            trade_log=trade_log,
            equity_curve=equity_df,
        )

    def _load_benchmark_curve(self) -> pd.DataFrame:
        """加载基准指数（沪深300）行情。"""
        try:
            df = self.gateway.get_ohlcv(
                "000300.SH",
                start_date=self.start_date.strftime("%Y-%m-%d"),
                end_date=self.end_date.strftime("%Y-%m-%d"),
                bars=252,
            )
            if not df.empty and {"date", "close"}.issubset(df.columns):
                return df[["date", "close"]]
        except Exception as exc:
            print(f"加载基准数据失败：{exc}")
        return pd.DataFrame()


def run_backtest(
    strategy_name: str,
    start_date: datetime,
    end_date: datetime,
    universe: list[str] | None = None,
    **kwargs: Any,
) -> BacktestReport:
    """便捷函数：运行回测。"""
    strategy = registry.build(strategy_name)
    engine = BacktestEngine(strategy, start_date, end_date, **kwargs)
    return engine.run(universe)
