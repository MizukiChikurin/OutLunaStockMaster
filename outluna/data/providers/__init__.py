"""数据提供商基类。"""

from typing import Any

import pandas as pd


class DataProvider:
    """数据提供商抽象基类。"""

    name: str = ""

    def get_ohlcv(
        self,
        symbol: str,
        period: str = "1d",
        start_date: str | None = None,
        end_date: str | None = None,
        bars: int = 100,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        """获取 K 线数据。"""
        return pd.DataFrame()

    def get_ohlcv_multi(
        self,
        symbols: list[str],
        period: str = "1d",
        start_date: str | None = None,
        end_date: str | None = None,
        bars: int = 100,
        adjust: str = "qfq",
    ) -> dict[str, pd.DataFrame]:
        """批量获取 K 线数据，默认逐个调用单只接口。"""
        result: dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            df = self.get_ohlcv(symbol, period, start_date, end_date, bars, adjust)
            if not df.empty:
                result[symbol] = df
        return result

    def get_stock_list(self, market: str = "A") -> list[str]:
        """获取市场股票列表。"""
        return []

    def screen_stocks(
        self,
        keyword: str,
        market: str = "stock",
        cross_days: int = 1,
    ) -> pd.DataFrame:
        """智能选股，默认不支持则返回空 DataFrame。"""
        return pd.DataFrame()

    def get_financials(self, symbol: str) -> dict[str, Any]:
        """获取财务报表摘要。"""
        return {}

    def get_financial_index(self, symbol: str) -> pd.DataFrame:
        """获取财务指标。"""
        return pd.DataFrame()

    def get_company_info(self, symbol: str) -> dict[str, Any]:
        """获取公司信息。"""
        return {}

    def get_holder_info(self, symbol: str) -> pd.DataFrame:
        """获取股东信息。"""
        return pd.DataFrame()

    def get_news(self, symbol: str, days: int = 7, limit: int = 20) -> list[dict[str, Any]]:
        """获取新闻。"""
        return []

    def get_capital_flow(self, symbol: str, days: int = 20) -> pd.DataFrame:
        """获取资金流向。"""
        return pd.DataFrame()

    def get_realtime_price(self, symbols: list[str]) -> pd.DataFrame:
        """获取实时行情。"""
        return pd.DataFrame()

    def get_realtime_tech(self, symbols: list[str], indicator: str = "MA") -> pd.DataFrame:
        """获取实时技术指标。"""
        return pd.DataFrame()

    def get_a_share_spot(self) -> pd.DataFrame:
        """获取 A 股全市场实时行情快照。"""
        return pd.DataFrame()

    def get_announcements(self, symbol: str, days: int = 30) -> pd.DataFrame:
        """获取公司公告。"""
        return pd.DataFrame()

    def get_forecast(self, symbol: str) -> pd.DataFrame:
        """获取业绩预测。"""
        return pd.DataFrame()

    def get_company_risk(self, company_full_name: str) -> dict[str, Any]:
        """获取企业风险。"""
        return {}

    def get_dragon_tiger(self, symbol: str, days: int = 5) -> pd.DataFrame:
        """获取龙虎榜数据。"""
        return pd.DataFrame()

    def get_margin_balance(self, symbol: str, days: int = 20) -> pd.DataFrame:
        """获取融资融券余额。"""
        return pd.DataFrame()

    def get_northbound_flow(self, symbol: str, days: int = 20) -> pd.DataFrame:
        """获取北向资金流向。"""
        return pd.DataFrame()
