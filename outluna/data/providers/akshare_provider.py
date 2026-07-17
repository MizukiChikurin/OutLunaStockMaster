"""akshare 数据提供商适配器。"""

from typing import Any, cast

import pandas as pd

from outluna.data.providers import DataProvider
from outluna.utils.symbol import SymbolNormalizer


class AkShareProvider(DataProvider):
    """AkShare 数据提供商，用于补充 A 股特色数据。

    注意：AkShare 接口和列名可能随版本变化，调用失败时会返回空结果。
    """

    name = "akshare"

    def __init__(self):
        self._ak = None
        try:
            import akshare as ak
            self._ak = ak
        except ImportError:
            pass

    def _ensure_imported(self) -> None:
        """确保 akshare 已安装。"""
        if self._ak is None:
            raise ImportError("akshare 未安装，请执行 pip install akshare")

    def _call_ak_func(self, func_name: str, *args: Any, **kwargs: Any) -> pd.DataFrame:
        """安全调用 akshare 函数。"""
        self._ensure_imported()
        func = getattr(self._ak, func_name, None)
        if not func:
            raise RuntimeError(f"akshare 不存在接口：{func_name}")
        return cast(pd.DataFrame, func(*args, **kwargs))

    def _standardize_kline(self, df: pd.DataFrame) -> pd.DataFrame:
        """标准化 K 线列名与日期格式。"""
        if df.empty:
            return df
        column_map = {
            "日期": "date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "amount",
            "振幅": "amplitude",
            "涨跌幅": "pct_change",
            "涨跌额": "change",
            "换手率": "turnover",
        }
        df = df.rename(columns={k: v for k, v in column_map.items() if k in df.columns})
        if "date" in df.columns:
            df["date"] = self._parse_dates(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
        cols = [c for c in ["date", "open", "high", "low", "close", "volume"] if c in df.columns]
        return df[cols]

    def _parse_dates(self, series: pd.Series) -> pd.Series:
        """将日期列统一解析为 datetime，兼容多种时间戳与字符串格式。"""
        if pd.api.types.is_datetime64_any_dtype(series):
            return series

        def _is_reasonable(parsed: pd.Series) -> bool:
            """判断解析后的日期是否落在合理年份范围。"""
            if parsed.empty:
                return True
            try:
                year = int(parsed.max().year)
                return 1971 <= year <= 2099
            except Exception:
                return False

        candidates: list[pd.Series] = []

        try:
            candidates.append(pd.to_datetime(series))
        except Exception:
            pass

        try:
            candidates.append(pd.to_datetime(series, unit="s"))
        except Exception:
            pass

        try:
            candidates.append(pd.to_datetime(series, unit="ms"))
        except Exception:
            pass

        try:
            candidates.append(pd.to_datetime(series.astype(str), format="%Y%m%d"))
        except Exception:
            pass

        for parsed in candidates:
            if _is_reasonable(parsed):
                return parsed

        if candidates:
            return candidates[0]
        return series

    def get_ohlcv(
        self,
        symbol: str,
        period: str = "1d",
        start_date: str | None = None,
        end_date: str | None = None,
        bars: int = 100,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        """获取 A 股历史 K 线。"""
        self._ensure_imported()
        code = symbol.split(".")[0]

        period_map = {"1d": "daily", "1w": "weekly", "1m": "monthly"}
        ak_period = period_map.get(period, "daily")

        df = self._call_ak_func(
            "stock_zh_a_hist",
            symbol=code,
            period=ak_period,
            start_date=start_date or "19700101",
            end_date=end_date or "20500101",
            adjust=adjust,
        )
        return self._standardize_kline(df)

    def get_stock_list(self, market: str = "A") -> list[str]:
        """获取 A 股股票列表，返回统一标准化的内部格式代码。"""
        self._ensure_imported()
        try:
            df = self._call_ak_func("stock_zh_a_spot_em")
        except Exception:
            # 网络不可用或接口临时关闭时，返回本地兜底列表
            return self._fallback_stock_list()

        codes: list[str] = []
        if "代码" in df.columns:
            for code in df["代码"].astype(str).tolist():
                # akshare 返回无后缀的 6 位代码，根据前缀补充交易所后缀
                normalized = SymbolNormalizer.normalize(code)
                if normalized:
                    codes.append(normalized)
            return codes
        return []

    def get_a_share_spot(self) -> pd.DataFrame:
        """获取 A 股全市场实时行情快照。

        优先使用东方财富接口，失败时降级到 Sina 接口 ``stock_zh_a_spot``。
        """
        self._ensure_imported()
        try:
            df = self._call_ak_func("stock_zh_a_spot_em")
        except Exception:
            df = self._call_ak_func("stock_zh_a_spot")

        if df.empty:
            return df

        # 统一列名
        column_map = {
            "代码": "代码",
            "名称": "名称",
            "最新价": "最新价",
            "涨跌额": "涨跌额",
            "涨跌幅": "涨跌幅",
            "买入": "买入",
            "卖出": "卖出",
            "昨收": "昨收",
            "今开": "今开",
            "最高": "最高",
            "最低": "最低",
            "成交量": "成交量",
            "成交额": "成交额",
            "时间戳": "时间戳",
        }
        df = df.rename(columns={k: v for k, v in column_map.items() if k in df.columns})
        return df

    def _fallback_stock_list(self) -> list[str]:
        """网络不可用时返回本地沪深300成分股样本。"""
        return [
            "000001.SZ", "000002.SZ", "000063.SZ", "000100.SZ", "000333.SZ",
            "000338.SZ", "000568.SZ", "000596.SZ", "000625.SZ", "000651.SZ",
            "000725.SZ", "000768.SZ", "000858.SZ", "000895.SZ", "001979.SZ",
            "002001.SZ", "002007.SZ", "002008.SZ", "002024.SZ", "002027.SZ",
            "002049.SZ", "002120.SZ", "002142.SZ", "002230.SZ", "002236.SZ",
            "002271.SZ", "002304.SZ", "002352.SZ", "002410.SZ", "002415.SZ",
            "002460.SZ", "002475.SZ", "002594.SZ", "002714.SZ", "002812.SZ",
            "300003.SZ", "300014.SZ", "300015.SZ", "300033.SZ", "300059.SZ",
            "300122.SZ", "300124.SZ", "300142.SZ", "300274.SZ", "300408.SZ",
            "300413.SZ", "300433.SZ", "300498.SZ", "300750.SZ", "600000.SH",
            "600009.SH", "600016.SH", "600028.SH", "600030.SH", "600031.SH",
            "600036.SH", "600048.SH", "600050.SH", "600104.SH", "600276.SH",
            "600309.SH", "600346.SH", "600406.SH", "600436.SH", "600438.SH",
            "600519.SH", "600547.SH", "600570.SH", "600585.SH", "600588.SH",
            "600600.SH", "600660.SH", "600690.SH", "600703.SH", "600745.SH",
            "600809.SH", "600837.SH", "600887.SH", "600893.SH", "600900.SH",
            "601012.SH", "601066.SH", "601088.SH", "601100.SH", "601111.SH",
            "601138.SH", "601166.SH", "601211.SH", "601288.SH", "601318.SH",
            "601319.SH", "601336.SH", "601398.SH", "601601.SH", "601628.SH",
            "601633.SH", "601668.SH", "601688.SH", "601728.SH", "601857.SH",
            "601888.SH", "601899.SH", "601901.SH", "601933.SH", "601985.SH",
            "601988.SH", "601989.SH", "603259.SH", "603288.SH", "603501.SH",
            "603659.SH", "603986.SH", "688001.SH", "688002.SH", "688003.SH",
        ]

    def get_news(self, symbol: str, days: int = 7, limit: int = 20) -> list[dict[str, Any]]:
        """获取个股新闻。"""
        self._ensure_imported()
        code = symbol.split(".")[0]
        try:
            df = self._call_ak_func("stock_news_em", symbol=code)
        except Exception:
            return []
        if df.empty:
            return []

        # 列名兼容处理
        title_col = "新闻标题" if "新闻标题" in df.columns else "title"
        content_col = "新闻内容" if "新闻内容" in df.columns else "content"
        time_col = "发布时间" if "发布时间" in df.columns else "pub_time"
        source_col = "文章来源" if "文章来源" in df.columns else "source"

        records = []
        for _, row in df.head(limit).iterrows():
            records.append(
                {
                    "title": str(row.get(title_col, "")),
                    "content": str(row.get(content_col, "")),
                    "published_at": str(row.get(time_col, "")),
                    "source": str(row.get(source_col, "")),
                }
            )
        return records

    def get_capital_flow(self, symbol: str, days: int = 20) -> pd.DataFrame:
        """获取个股资金流向。"""
        self._ensure_imported()
        code = symbol.split(".")[0]
        market = "sh" if code.startswith("6") else "sz"
        try:
            df = self._call_ak_func("stock_individual_fund_flow", stock=code, market=market)
        except Exception:
            return pd.DataFrame()
        if df.empty:
            return df

        column_map = {
            "日期": "date",
            "主力净流入": "main_inflow",
            "小单净流入": "retail_inflow",
            "中单净流入": "mid_inflow",
            "大单净流入": "large_inflow",
            "净流入": "net_inflow",
        }
        df = df.rename(columns={k: v for k, v in column_map.items() if k in df.columns})
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").tail(days)

    def get_dragon_tiger(self, symbol: str, days: int = 5) -> pd.DataFrame:
        """获取龙虎榜数据。"""
        self._ensure_imported()
        code = symbol.split(".")[0]
        try:
            df = self._call_ak_func("stock_lhb_detail_daily_sina")
        except Exception:
            return pd.DataFrame()
        if df.empty or "代码" not in df.columns:
            return df
        return df[df["代码"].astype(str) == code].tail(days)

    def get_margin_balance(self, symbol: str, days: int = 20) -> pd.DataFrame:
        """获取融资融券余额。"""
        self._ensure_imported()
        code = symbol.split(".")[0]
        try:
            if code.startswith(("0", "3")):
                df = self._call_ak_func("stock_margin_detail_szse", symbol=code)
            else:
                df = self._call_ak_func("stock_margin_detail_sse", symbol=code)
        except Exception:
            return pd.DataFrame()
        return df.tail(days)

    def get_northbound_flow(self, symbol: str, days: int = 20) -> pd.DataFrame:
        """获取沪深股通持股数据。"""
        self._ensure_imported()
        code = symbol.split(".")[0]
        try:
            # 沪深股通持股数据
            df = self._call_ak_func("stock_hsgt_hist_em", symbol=code)
        except Exception:
            return pd.DataFrame()
        if df.empty:
            return df
        if "日期" in df.columns:
            df = df.rename(columns={"日期": "date"})
            df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").tail(days)

    def get_individual_info(self, symbol: str) -> dict[str, Any]:
        """获取个股概况。"""
        self._ensure_imported()
        code = symbol.split(".")[0]
        try:
            df = self._call_ak_func("stock_individual_info_em", symbol=code)
        except Exception:
            return {}
        if df.empty:
            return {}
        return dict(zip(df["item"].tolist(), df["value"].tolist(), strict=False))
