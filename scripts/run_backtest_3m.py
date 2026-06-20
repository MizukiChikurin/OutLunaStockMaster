#!/usr/bin/env python3
"""三个月真实行情回测脚本。

按验收标准模拟运行三个月，评估指定策略的投资结果，
输出回测报告并保存到 data/reports 目录。

用法：
    python scripts/run_backtest_3m.py [策略名] [股票池数量]

示例：
    python scripts/run_backtest_3m.py 十字星 100
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 将项目根目录加入路径，确保脚本可直接运行
sys.path.insert(0, str(Path(__file__).parent.parent))

from outluna.backtest.engine import run_backtest
from outluna.report.generator import ReportGenerator
from outluna.utils.logger import setup_logging

logger = setup_logging()


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="运行三个月策略回测")
    parser.add_argument(
        "strategy",
        nargs="?",
        default="十字星",
        help="策略名称（默认：十字星）",
    )
    parser.add_argument(
        "--universe-size",
        type=int,
        default=100,
        help="候选股票池数量上限（默认：100）",
    )
    parser.add_argument(
        "--initial-capital",
        type=float,
        default=100000.0,
        help="初始资金（默认：100000）",
    )
    parser.add_argument(
        "--hold-days",
        type=int,
        default=5,
        help="默认持有天数（默认：5）",
    )
    parser.add_argument(
        "--stop-loss",
        type=float,
        default=0.08,
        help="止损比例（默认：0.08）",
    )
    parser.add_argument(
        "--take-profit",
        type=float,
        default=0.15,
        help="止盈比例（默认：0.15）",
    )
    return parser.parse_args()


async def main() -> int:
    """脚本主入口。"""
    args = parse_args()

    end_date = datetime.now()
    start_date = end_date - timedelta(days=90)

    logger.info(
        f"开始三个月回测：策略={args.strategy}，"
        f"区间={start_date.date()} ~ {end_date.date()}"
    )

    report = run_backtest(
        strategy_name=args.strategy,
        start_date=start_date,
        end_date=end_date,
        initial_capital=args.initial_capital,
        hold_days=args.hold_days,
        stop_loss=args.stop_loss,
        take_profit=args.take_profit,
        max_universe=args.universe_size,
    )

    generator = ReportGenerator()
    generator.save(report)

    print("=" * 50)
    print(f"策略：{report.strategy_name}")
    print(f"回测区间：{report.start_date.date()} ~ {report.end_date.date()}")
    print(f"初始资金：{report.initial_capital:,.2f}")
    print("-" * 50)
    print(f"总收益率：{report.metrics.total_return:.2%}")
    print(f"年化收益率：{report.metrics.annualized_return:.2%}")
    print(f"基准收益率（沪深300）：{report.metrics.benchmark_return:.2%}")
    print(f"超额收益（Alpha）：{report.metrics.alpha:.2%}")
    print(f"胜率：{report.metrics.win_rate:.2%}")
    print(f"盈亏比：{report.metrics.profit_factor:.2f}")
    print(f"最大回撤：{report.metrics.max_drawdown:.2%}")
    print(f"夏普比率：{report.metrics.sharpe_ratio:.2f}")
    print(f"总交易次数：{report.metrics.total_trades}")
    print("=" * 50)
    print(f"报告已保存：{report.report_id}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception:
        logger.exception("回测执行失败")
        sys.exit(1)
