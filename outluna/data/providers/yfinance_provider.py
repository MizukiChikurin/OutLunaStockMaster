"""yfinance 数据提供商适配器。"""

from typing import Any, cast

import pandas as pd

from outluna.data.providers import DataProvider
from outluna.utils.symbol import SymbolNormalizer


class YFinanceProvider(DataProvider):
    """yfinance 数据提供商，用于补充全球市场和分钟级数据。"""

    name = "yfinance"

    def __init__(self):
        self._yf = None
        try:
            import yfinance as yf
            self._yf = yf
        except ImportError:
            pass

    def _ensure_imported(self) -> None:
        """确保 yfinance 已安装。"""
        if self._yf is None:
            raise ImportError("yfinance 未安装，请执行 pip install yfinance")

    def _normalize_symbol(self, symbol: str) -> str:
        """将内部格式代码转换为 yfinance 格式。"""
        return SymbolNormalizer.to_yfinance(symbol)

    def get_ohlcv(
        self,
        symbol: str,
        period: str = "1d",
        start_date: str | None = None,
        end_date: str | None = None,
        bars: int = 100,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        """获取历史 K 线。"""
        self._ensure_imported()
        ticker = self._normalize_symbol(symbol)
        ticker_obj = self._yf.Ticker(ticker)

        interval_map = {
            "1d": "1d",
            "1w": "1wk",
            "1m": "1mo",
            "1h": "1h",
            "15m": "15m",
            "5m": "5m",
            "1min": "1m",
        }
        interval = interval_map.get(period, "1d")

        hist_kwargs: dict[str, Any] = {
            "interval": interval,
            "auto_adjust": adjust == "qfq",
        }
        if start_date and end_date:
            hist_kwargs["start"] = start_date
            hist_kwargs["end"] = end_date
        else:
            hist_kwargs["period"] = f"{bars}d" if interval in ("1d", "1wk", "1mo") else "1mo"

        df = cast(pd.DataFrame, ticker_obj.history(**hist_kwargs))
        if df.empty:
            return df
        df = df.reset_index()
        df = df.rename(
            columns={
                "Date": "date",
                "Datetime": "date",
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            }
        )
        df["date"] = pd.to_datetime(df["date"])
        return cast(pd.DataFrame, df[["date", "open", "high", "low", "close", "volume"]])

    def get_stock_list(self, market: str = "A") -> list[str]:
        """yfinance 不直接提供市场列表，返回空。"""
        return []

    def get_financials(self, symbol: str) -> dict[str, Any]:
        """获取财务报表。"""
        self._ensure_imported()
        ticker = self._normalize_symbol(symbol)
        ticker_obj = self._yf.Ticker(ticker)
        return {
            "income_stmt": ticker_obj.income_stmt,
            "balance_sheet": ticker_obj.balance_sheet,
            "cashflow": ticker_obj.cashflow,
        }

    def get_company_info(self, symbol: str) -> dict[str, Any]:
        """获取公司信息。"""
        self._ensure_imported()
        ticker = self._normalize_symbol(symbol)
        ticker_obj = self._yf.Ticker(ticker)
        return cast(dict[str, Any], ticker_obj.info)

    def get_recommendations(self, symbol: str) -> pd.DataFrame:
        """获取分析师评级。"""
        self._ensure_imported()
        ticker = self._normalize_symbol(symbol)
        ticker_obj = self._yf.Ticker(ticker)
        return cast(pd.DataFrame, ticker_obj.recommendations)

    def get_options_chain(self, symbol: str, date: str | None = None) -> dict[str, Any]:
        """获取期权链（仅美股）。"""
        self._ensure_imported()
        ticker = self._normalize_symbol(symbol)
        ticker_obj = self._yf.Ticker(ticker)
        if date:
            return cast(dict[str, Any], ticker_obj.option_chain(date))
        return cast(dict[str, Any], ticker_obj.option_chain())
