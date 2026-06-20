"""回测模拟持仓与资金管理。"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Position:
    """持仓记录。"""

    symbol: str
    buy_date: datetime
    buy_price: float
    shares: int
    cost: float = 0.0
    highest_price: float = 0.0

    def __post_init__(self):
        if self.cost == 0:
            self.cost = self.buy_price * self.shares
        if self.highest_price == 0:
            self.highest_price = self.buy_price

    def update_high(self, price: float) -> None:
        """更新最高价（用于移动止损）。"""
        if price > self.highest_price:
            self.highest_price = price

    def market_value(self, price: float) -> float:
        """当前市值。"""
        return price * self.shares

    def pnl_ratio(self, price: float) -> float:
        """盈亏比例。"""
        if self.buy_price == 0:
            return 0.0
        return (price - self.buy_price) / self.buy_price


@dataclass
class Portfolio:
    """模拟投资组合。"""

    initial_capital: float
    cash: float = field(init=False)
    positions: dict[str, Position] = field(default_factory=dict)
    transaction_cost_rate: float = 0.0005  # 佣金+滑点，默认万5
    min_cost: float = 5.0  # 最低佣金

    def __post_init__(self):
        self.cash = self.initial_capital

    def buy(self, symbol: str, date: datetime, price: float, weight: float = 1.0) -> Position | None:
        """按权重买入。"""
        if weight <= 0 or weight > 1:
            return None
        if symbol in self.positions:
            return None

        # 可用资金按比例分配
        available = self.cash * weight
        # 预留交易成本
        cost_estimate = max(available * self.transaction_cost_rate, self.min_cost)
        usable = available - cost_estimate

        # A股按手取整：每手 100 股
        shares = int(usable / (price * 100)) * 100

        if shares <= 0:
            return None

        total_cost = shares * price
        commission = max(total_cost * self.transaction_cost_rate, self.min_cost)
        total_outflow = total_cost + commission

        if total_outflow > self.cash:
            return None

        self.cash -= total_outflow
        position = Position(
            symbol=symbol,
            buy_date=date,
            buy_price=price,
            shares=shares,
            cost=total_outflow,
        )
        self.positions[symbol] = position
        return position

    def sell(self, symbol: str, date: datetime, price: float, reason: str = "") -> tuple[float, str]:
        """卖出持仓。"""
        position = self.positions.pop(symbol, None)
        if not position:
            return 0.0, "无持仓"

        gross = position.shares * price
        cost = max(gross * self.transaction_cost_rate, self.min_cost)
        net = gross - cost
        self.cash += net

        pnl = net - position.cost
        return pnl, reason

    def update_positions(self, date: datetime, prices: dict[str, float]) -> None:
        """更新持仓最高价。"""
        for symbol, position in self.positions.items():
            if symbol in prices:
                position.update_high(prices[symbol])

    def total_value(self, prices: dict[str, float]) -> float:
        """当前总资产。"""
        market_value = sum(
            position.market_value(prices.get(symbol, 0)) for symbol, position in self.positions.items()
        )
        return self.cash + market_value

    def daily_returns(self, prices: dict[str, float]) -> float:
        """当日收益率（基于总资产）。"""
        return self.total_value(prices)
