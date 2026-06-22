"""股票代码标准化工具。

统一不同数据源之间的股票代码格式，避免多数据源混用时因代码格式不一致导致查询失败。
"""

from __future__ import annotations

import re


class SymbolNormalizer:
    """股票代码标准化器。

    内部标准格式为 ``{代码}.{交易所后缀}``，其中 A 股后缀为 SH/SZ/BJ，
    港股后缀为 HK，美股后缀为 US。各数据提供商在收到内部格式后，
    再转换为自身所需的格式（如 yfinance 的 .SS、akshare 的无后缀）。
    """

    # A 股代码前缀与交易所映射
    _A_SHARE_PREFIX_MAP: dict[tuple[str, ...], str] = {
        ("600", "601", "603", "605", "688", "689"): "SH",
        ("000", "001", "002", "003", "300", "301"): "SZ",
        ("430", "831", "832", "833", "870"): "BJ",
    }

    @classmethod
    def normalize(cls, symbol: str) -> str:
        """将任意格式的股票代码标准化为内部格式。

        支持格式示例：
            - ``600519`` → ``600519.SH``
            - ``600519.SS`` → ``600519.SH``
            - ``000001.SZ`` → ``000001.SZ``
            - ``0700.HK`` / ``700.HK`` → ``0700.HK``
            - ``AAPL`` → ``AAPL.US``

        Args:
            symbol: 原始股票代码，可能包含或不包含交易所后缀。

        Returns:
            标准化后的内部格式代码。
        """
        if not symbol or not isinstance(symbol, str):
            return ""

        symbol = symbol.strip().upper()
        if not symbol:
            return ""

        # 已包含后缀时，统一后缀命名
        if "." in symbol:
            code, suffix = symbol.rsplit(".", 1)
            code = code.strip()
            suffix = suffix.strip()
            exchange = cls._standardize_suffix(suffix)
            if exchange:
                return cls._format_with_exchange(code, exchange)
            # 无法识别的后缀，保持原样返回
            return symbol

        # 无后缀时，根据代码特征推断交易所
        return cls._infer_exchange(symbol)

    @classmethod
    def normalize_list(cls, symbols: list[str]) -> list[str]:
        """批量标准化股票代码列表，过滤空值。"""
        return [normalized for s in symbols if (normalized := cls.normalize(s))]

    @classmethod
    def _standardize_suffix(cls, suffix: str) -> str:
        """将各种交易所后缀统一为内部后缀。"""
        suffix_map: dict[str, str] = {
            "SH": "SH",
            "SS": "SH",  # yfinance 使用 .SS 表示上海
            "SZ": "SZ",
            "BJ": "BJ",
            "BEIJING": "BJ",
            "HK": "HK",
            "HKG": "HK",
            "US": "US",
            "O": "US",  # NASDAQ
            "N": "US",  # NYSE
            "A": "US",  # AMEX
            "NASDAQ": "US",
            "NYSE": "US",
            "AMEX": "US",
        }
        return suffix_map.get(suffix, "")

    @classmethod
    def _infer_exchange(cls, code: str) -> str:
        """根据代码特征推断交易所。"""
        # A 股：6 位数字
        if re.fullmatch(r"\d{6}", code):
            for prefixes, exchange in cls._A_SHARE_PREFIX_MAP.items():
                if code.startswith(prefixes):
                    return cls._format_with_exchange(code, exchange)
            # 无法识别的 6 位数字，默认上海主板
            return cls._format_with_exchange(code, "SH")

        # 港股：纯数字，通常为 4-5 位
        if re.fullmatch(r"\d{4,5}", code):
            return cls._format_with_exchange(code.zfill(4), "HK")

        # 其他：默认美股
        return cls._format_with_exchange(code, "US")

    @classmethod
    def _format_with_exchange(cls, code: str, exchange: str) -> str:
        """拼接代码与交易所后缀。"""
        return f"{code}.{exchange}"

    @classmethod
    def to_akshare(cls, symbol: str) -> str:
        """将内部格式转换为 akshare 所需的纯 6 位代码（无后缀）。"""
        return symbol.split(".")[0] if "." in symbol else symbol

    @classmethod
    def to_yfinance(cls, symbol: str) -> str:
        """将内部格式转换为 yfinance 格式。"""
        if not symbol:
            return symbol
        if symbol.endswith(".SH"):
            return symbol.replace(".SH", ".SS")
        if symbol.endswith(".BJ"):
            return symbol.replace(".BJ", ".SS")
        # 港股、美股等保持内部格式即可
        return symbol

    @classmethod
    def to_kimi(cls, symbol: str) -> str:
        """将内部格式转换为 Kimi Datasource 格式（与内部格式一致）。"""
        return symbol


def normalize_symbol(symbol: str) -> str:
    """标准化单只股票代码的便捷函数。"""
    return SymbolNormalizer.normalize(symbol)


def normalize_symbols(symbols: list[str]) -> list[str]:
    """批量标准化股票代码的便捷函数。"""
    return SymbolNormalizer.normalize_list(symbols)
