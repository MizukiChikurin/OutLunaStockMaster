"""回测绩效指标计算。"""

import math

import pandas as pd

from outluna.data.models import BacktestMetrics, TradeRecord


def calculate_metrics(
    initial_capital: float,
    equity_curve: pd.DataFrame,
    trade_log: list[TradeRecord],
    risk_free_rate: float = 0.03,
    benchmark_curve: pd.DataFrame | None = None,
) -> BacktestMetrics:
    """计算回测绩效指标。"""
    if equity_curve.empty or "total_value" not in equity_curve.columns:
        return BacktestMetrics()

    values = equity_curve["total_value"].values
    if len(values) < 2 or initial_capital <= 0:
        return BacktestMetrics()

    total_return = (values[-1] - initial_capital) / initial_capital

    # 交易日数量估算
    trading_days = len(values)
    years = max(trading_days / 252, 1 / 252)
    annualized_return = (1 + total_return) ** (1 / years) - 1

    # 每日收益率
    daily_returns = pd.Series(values).pct_change().dropna()

    # 最大回撤
    cumulative = pd.Series(values)
    running_max = cumulative.cummax()
    drawdown = (cumulative - running_max) / running_max
    max_drawdown = drawdown.min()

    # 夏普比率
    if len(daily_returns) > 1 and daily_returns.std() != 0:
        excess_return = daily_returns.mean() - risk_free_rate / 252
        sharpe_ratio = excess_return / daily_returns.std() * math.sqrt(252)
    else:
        sharpe_ratio = 0.0

    # 胜率、盈亏比（基于每笔卖出的实际盈亏）
    completed_trades = [t for t in trade_log if t.action == "sell"]
    total_trades = len(completed_trades)

    wins = [t for t in completed_trades if t.pnl > 0]
    losses = [t for t in completed_trades if t.pnl <= 0]
    win_rate = len(wins) / total_trades if total_trades > 0 else 0.0

    gross_profit = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # 基准对比
    benchmark_return = 0.0
    if benchmark_curve is not None and not benchmark_curve.empty and "close" in benchmark_curve.columns:
        bench_values = benchmark_curve["close"].dropna()
        if len(bench_values) >= 2 and bench_values.iloc[0] > 0:
            benchmark_return = (bench_values.iloc[-1] - bench_values.iloc[0]) / bench_values.iloc[0]

    alpha = total_return - benchmark_return

    return BacktestMetrics(
        total_return=total_return,
        annualized_return=annualized_return,
        win_rate=win_rate,
        profit_factor=profit_factor,
        max_drawdown=max_drawdown,
        sharpe_ratio=sharpe_ratio,
        total_trades=total_trades,
        alpha=alpha,
        benchmark_return=benchmark_return,
    )


def calculate_drawdown_series(equity_curve: pd.DataFrame) -> pd.DataFrame:
    """计算回撤序列。"""
    if equity_curve.empty or "total_value" not in equity_curve.columns:
        return pd.DataFrame()
    values = equity_curve["total_value"]
    running_max = values.cummax()
    drawdown = (values - running_max) / running_max
    return pd.DataFrame({"date": equity_curve["date"], "drawdown": drawdown.values})
