"""统一数据网关。"""

import time
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar, cast

import pandas as pd

from outluna.config import settings
from outluna.data.cache import DataCache
from outluna.data.providers import DataProvider
from outluna.data.providers.akshare_provider import AkShareProvider
from outluna.data.providers.kimi_api_provider import KimiApiDataSourceProvider
from outluna.data.providers.kimi_provider import KimiAuthError, KimiDataSourceProvider
from outluna.data.providers.yfinance_provider import YFinanceProvider
from outluna.utils.logger import setup_logging
from outluna.utils.symbol import SymbolNormalizer

logger = setup_logging()

T = TypeVar("T")


def with_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """指数退避重试装饰器。"""

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exception: Exception | None = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exception = exc
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(
                            f"{func.__name__} 调用失败（第 {attempt + 1} 次），{delay}s 后重试：{exc}"
                        )
                        time.sleep(delay)
            if last_exception:
                raise last_exception
            raise RuntimeError("重试耗尽但无异常信息")

        return wrapper

    return decorator


class DataGateway:
    """统一数据网关，所有数据访问的单一入口。

    以 Kimi Datasource 为核心数据源，akshare 和 yfinance 作为补充。
    集成本地缓存、限流、重试、降级机制。
    """

    def __init__(
        self,
        providers: dict[str, DataProvider] | None = None,
        cache: DataCache | None = None,
    ):
        self.providers = providers or self._build_default_providers()
        self.cache = cache or DataCache()
        self._last_call_time: dict[str, float] = {}
        self._min_interval = 0.1  # 同一 provider 最小调用间隔（秒）
        self._call_stats: dict[str, dict[str, Any]] = {
            name: {"success": 0, "fail": 0, "total_time": 0.0}
            for name in self.providers
        }

    def _build_default_providers(self) -> dict[str, DataProvider]:
        """构建默认数据提供商集合。"""
        providers: dict[str, DataProvider] = {}
        if settings.prefer_kimi_api:
            try:
                providers["kimi_api"] = KimiApiDataSourceProvider()
            except Exception as exc:
                logger.warning(f"Kimi API 数据源初始化失败：{exc}")
        if settings.prefer_kimi_datasource:
            try:
                providers["kimi"] = KimiDataSourceProvider()
            except Exception as exc:
                logger.warning(f"Kimi Datasource 初始化失败：{exc}")
        if settings.akshare_enabled:
            try:
                providers["akshare"] = AkShareProvider()
            except Exception as exc:
                logger.warning(f"AkShare 初始化失败：{exc}")
        if settings.yfinance_enabled:
            try:
                providers["yfinance"] = YFinanceProvider()
            except Exception as exc:
                logger.warning(f"yfinance 初始化失败：{exc}")
        return providers

    def _get_kimi_provider(self) -> DataProvider | None:
        """返回当前启用的 Kimi 数据源提供商（API 优先）。"""
        return self.providers.get("kimi_api") or self.providers.get("kimi")

    def _rate_limit(self, provider_name: str) -> None:
        """简单的调用频率控制。"""
        now = time.time()
        last = self._last_call_time.get(provider_name, 0)
        elapsed = now - last
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_call_time[provider_name] = time.time()

    def _record_call(self, provider_name: str, success: bool, duration: float) -> None:
        """记录调用统计。"""
        stats = self._call_stats.setdefault(provider_name, {"success": 0, "fail": 0, "total_time": 0.0})
        if success:
            stats["success"] += 1
        else:
            stats["fail"] += 1
        stats["total_time"] += duration

    def _call_with_fallback(
        self,
        method_name: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """按优先级调用各 provider，失败或返回空结果则降级。"""
        last_exception: Exception | None = None
        auth_error: KimiAuthError | None = None
        for name, provider in self.providers.items():
            method = getattr(provider, method_name, None)
            if not method:
                continue
            try:
                self._rate_limit(name)
                start = time.time()
                result = method(*args, **kwargs)
                # 对列表/字典/DataFrame 做空结果判断，空结果继续降级
                if self._is_empty_result(result):
                    duration = time.time() - start
                    self._record_call(name, False, duration)
                    logger.debug(f"[{name}] {method_name} 返回空结果")
                    continue
                self._record_call(name, True, time.time() - start)
                return result
            except KimiAuthError as exc:
                duration = time.time() - start if "start" in locals() else 0
                self._record_call(name, False, duration)
                logger.warning(f"[{name}] {method_name} Kimi 凭证错误：{exc}")
                auth_error = exc
                last_exception = exc
                continue
            except Exception as exc:
                duration = time.time() - start if "start" in locals() else 0
                self._record_call(name, False, duration)
                logger.warning(f"[{name}] {method_name} 调用失败：{exc}")
                last_exception = exc
                continue

        # 所有 provider 均失败，若期间发生 Kimi 凭证错误，优先抛出该错误以触发自动刷新
        if auth_error is not None:
            raise auth_error
        error_msg = f"所有数据提供商调用失败：{method_name}"
        if last_exception:
            raise RuntimeError(f"{error_msg}，最后错误：{last_exception}")
        raise RuntimeError(error_msg)

    @staticmethod
    def _is_empty_result(result: Any) -> bool:
        """判断 provider 返回的结果是否为空。"""
        if result is None:
            return True
        if isinstance(result, pd.DataFrame):
            return result.empty
        if isinstance(result, dict):
            return not result
        if isinstance(result, list):
            return not result
        return False

    def _cache_key(self, method: str, *args: Any, **kwargs: Any) -> str:
        """生成缓存键。"""
        return f"{method}:{args}:{kwargs}"

    def _normalize_symbol(self, symbol: str) -> str:
        """将输入股票代码统一标准化为内部格式。"""
        return SymbolNormalizer.normalize(symbol)

    def _normalize_symbols(self, symbols: list[str]) -> list[str]:
        """批量将输入股票代码统一标准化为内部格式。"""
        return SymbolNormalizer.normalize_list(symbols)

    def _call_with_fallback_df(self, method_name: str, *args: Any, **kwargs: Any) -> pd.DataFrame:
        """调用降级链并转换为 DataFrame。"""
        return cast(pd.DataFrame, self._call_with_fallback(method_name, *args, **kwargs))

    def _call_with_fallback_list(self, method_name: str, *args: Any, **kwargs: Any) -> list[str]:
        """调用降级链并转换为字符串列表。"""
        return cast(list[str], self._call_with_fallback(method_name, *args, **kwargs))

    def _call_with_fallback_dict(self, method_name: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """调用降级链并转换为字典。"""
        return cast(dict[str, Any], self._call_with_fallback(method_name, *args, **kwargs))

    def _call_with_fallback_news(self, method_name: str, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        """调用降级链并转换为新闻列表。"""
        return cast(list[dict[str, Any]], self._call_with_fallback(method_name, *args, **kwargs))

    # ==================== 智能选股 ====================

    @with_retry(max_retries=2, exceptions=(RuntimeError,))
    def screen_stocks(
        self,
        keyword: str,
        market: str = "stock",
        cross_days: int = 1,
    ) -> pd.DataFrame:
        """按自然语言条件筛选股票。"""
        key = self._cache_key("screen_stocks", keyword, market, cross_days)
        cached = self.cache.get_df(key)
        if cached is not None:
            return cached

        df = self._call_with_fallback_df("screen_stocks", keyword, market, cross_days)
        self.cache.set_df(key, df)
        return df

    # ==================== 行情数据 ====================

    def get_ohlcv(
        self,
        symbol: str,
        period: str = "1d",
        start_date: str | None = None,
        end_date: str | None = None,
        bars: int = 100,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        """获取 K 线数据，带缓存。输入代码会自动标准化。"""
        symbol = self._normalize_symbol(symbol)
        key = self._cache_key("get_ohlcv", symbol, period, start_date, end_date, bars, adjust)
        cached = self.cache.get_df(key)
        if cached is not None:
            return cached

        df = self._call_with_fallback_df(
            "get_ohlcv", symbol, period, start_date, end_date, bars, adjust
        )
        if not df.empty:
            self.cache.set_df(key, df)
        return df

    def get_ohlcv_multi(
        self,
        symbols: list[str],
        period: str = "1d",
        start_date: str | None = None,
        end_date: str | None = None,
        bars: int = 100,
        adjust: str = "qfq",
    ) -> dict[str, pd.DataFrame]:
        """批量获取 K 线数据，优先使用 Kimi Datasource 的 10 只/次批量接口。"""
        symbols = self._normalize_symbols(symbols)
        if not symbols:
            return {}

        # 优先使用 Kimi 的批量接口，降低成本
        kimi_provider = self._get_kimi_provider()
        if kimi_provider is not None:
            method = getattr(kimi_provider, "get_ohlcv_multi", None)
            if method:
                try:
                    self._rate_limit(kimi_provider.name)
                    start = time.time()
                    batch_result = cast(
                        dict[str, pd.DataFrame],
                        method(symbols, period, start_date, end_date, bars, adjust),
                    )
                    self._record_call(kimi_provider.name, True, time.time() - start)
                    return batch_result
                except Exception as exc:
                    self._record_call(kimi_provider.name, False, 0.0)
                    logger.warning(f"Kimi 批量 K 线调用失败，降级为逐只调用：{exc}")

        # 降级：逐个 provider 逐只获取
        fallback_result: dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            df = self.get_ohlcv(symbol, period, start_date, end_date, bars, adjust)
            if not df.empty:
                fallback_result[symbol] = df
        return fallback_result

    def get_realtime_price(self, symbols: list[str]) -> pd.DataFrame:
        """获取实时行情，Kimi 限制最多 3 只/次。输入代码会自动标准化。"""
        symbols = self._normalize_symbols(symbols)
        if not symbols:
            return pd.DataFrame()

        kimi_provider = self._get_kimi_provider()
        if kimi_provider is not None and len(symbols) <= 3:
            return kimi_provider.get_realtime_price(symbols)

        # 分批调用 Kimi，每次最多 3 只
        results = []
        for i in range(0, len(symbols), 3):
            batch = symbols[i : i + 3]
            if kimi_provider is not None:
                try:
                    df = kimi_provider.get_realtime_price(batch)
                    results.append(df)
                    continue
                except Exception as exc:
                    logger.warning(f"Kimi 实时行情分批调用失败：{exc}")
            # 降级到其他 provider
            df = self._call_with_fallback_df("get_realtime_price", batch)
            results.append(df)

        return pd.concat(results, ignore_index=True) if results else pd.DataFrame()

    def get_realtime_tech(self, symbols: list[str], indicator: str = "MA") -> pd.DataFrame:
        """获取实时技术指标。输入代码会自动标准化。"""
        symbols = self._normalize_symbols(symbols)
        if not symbols:
            return pd.DataFrame()

        results = []
        for i in range(0, len(symbols), 3):
            batch = symbols[i : i + 3]
            kimi_provider = self._get_kimi_provider()
            if kimi_provider is not None:
                try:
                    df = kimi_provider.get_realtime_tech(batch, indicator)
                    results.append(df)
                    continue
                except Exception as exc:
                    logger.warning(f"Kimi 实时技术指标分批调用失败：{exc}")
            # 其他 provider 一般不支持，返回空
            logger.warning(f"无可用数据源提供 {batch} 的技术指标")

        return pd.concat(results, ignore_index=True) if results else pd.DataFrame()

    def get_close_summary(self, symbols: list[str]) -> pd.DataFrame:
        """获取日线收盘/开盘汇总数据，输入代码会自动标准化。

        主要用于获取今日开盘价等日线汇总信息，以 Kimi Datasource 为核心。
        """
        symbols = self._normalize_symbols(symbols)
        if not symbols:
            return pd.DataFrame()

        results = []
        for i in range(0, len(symbols), 3):
            batch = symbols[i : i + 3]
            kimi_provider = self._get_kimi_provider()
            if kimi_provider is not None:
                try:
                    df = kimi_provider.get_close_summary(batch)
                    results.append(df)
                    continue
                except Exception as exc:
                    logger.warning(f"Kimi close_summary 分批调用失败：{exc}")
            # 降级到其他 provider
            df = self._call_with_fallback_df("get_close_summary", batch)
            results.append(df)

        return pd.concat(results, ignore_index=True) if results else pd.DataFrame()

    def get_stock_list(self, market: str = "A") -> list[str]:
        """获取市场股票列表，返回统一标准化的内部格式代码。"""
        key = self._cache_key("get_stock_list", market)
        cached = self.cache.get_json(key)
        if cached is not None:
            return SymbolNormalizer.normalize_list(cast(list[str], cached))

        result = self._call_with_fallback_list("get_stock_list", market)
        normalized = SymbolNormalizer.normalize_list(result)
        self.cache.set_json(key, normalized)
        return normalized

    # ==================== A股快照 ====================

    def get_a_share_spot(self) -> pd.DataFrame:
        """获取 A 股全市场实时行情快照。"""
        return self._call_with_fallback_df("get_a_share_spot")

    # ==================== 财务与公司信息 ====================

    def get_financials(self, symbol: str) -> dict[str, Any]:
        """获取财务报表摘要。输入代码会自动标准化。"""
        symbol = self._normalize_symbol(symbol)
        key = self._cache_key("get_financials", symbol)
        cached = self.cache.get_json(key)
        if cached is not None:
            return cast(dict[str, Any], cached)

        result = self._call_with_fallback_dict("get_financials", symbol)
        self.cache.set_json(key, result)
        return result

    def get_financial_index(self, symbol: str) -> pd.DataFrame:
        """获取财务指标。输入代码会自动标准化。"""
        symbol = self._normalize_symbol(symbol)
        key = self._cache_key("get_financial_index", symbol)
        cached = self.cache.get_df(key)
        if cached is not None:
            return cached

        df = self._call_with_fallback_df("get_financial_index", symbol)
        self.cache.set_df(key, df)
        return df

    def get_company_info(self, symbol: str) -> dict[str, Any]:
        """获取公司信息。输入代码会自动标准化。"""
        symbol = self._normalize_symbol(symbol)
        key = self._cache_key("get_company_info", symbol)
        cached = self.cache.get_json(key)
        if cached is not None:
            return cast(dict[str, Any], cached)

        result = self._call_with_fallback_dict("get_company_info", symbol)
        self.cache.set_json(key, result)
        return result

    def get_holder_info(self, symbol: str) -> pd.DataFrame:
        """获取股东信息。输入代码会自动标准化。"""
        symbol = self._normalize_symbol(symbol)
        key = self._cache_key("get_holder_info", symbol)
        cached = self.cache.get_df(key)
        if cached is not None:
            return cached

        df = self._call_with_fallback_df("get_holder_info", symbol)
        self.cache.set_df(key, df)
        return df

    def get_forecast(self, symbol: str) -> pd.DataFrame:
        """获取盈利预测。Kimi Datasource 特有接口，不走 fallback。"""
        symbol = self._normalize_symbol(symbol)
        kimi_provider = self._get_kimi_provider()
        if kimi_provider is None:
            raise RuntimeError("Kimi Datasource 未启用，无法获取盈利预测")
        method = getattr(kimi_provider, "get_forecast", None)
        if not method:
            raise RuntimeError("Kimi Datasource 未实现盈利预测查询")
        return cast(pd.DataFrame, method(symbol))

    def get_announcements(self, symbol: str, days: int = 30) -> pd.DataFrame:
        """获取公司公告。Kimi Datasource 特有接口，不走 fallback。"""
        symbol = self._normalize_symbol(symbol)
        kimi_provider = self._get_kimi_provider()
        if kimi_provider is None:
            raise RuntimeError("Kimi Datasource 未启用，无法获取公司公告")
        method = getattr(kimi_provider, "get_announcements", None)
        if not method:
            raise RuntimeError("Kimi Datasource 未实现公司公告查询")
        return cast(pd.DataFrame, method(symbol, days=days))

    # ==================== 企业风险 ====================

    def get_company_risk(self, company_full_name: str) -> dict[str, Any]:
        """查询企业风险。"""
        kimi_provider = self._get_kimi_provider()
        if kimi_provider is None:
            raise RuntimeError("Kimi Datasource 未启用，无法查询企业风险")
        method = getattr(kimi_provider, "get_company_risk", None)
        if not method:
            raise RuntimeError("Kimi Datasource 未实现企业风险查询")
        return cast(dict[str, Any], method(company_full_name))

    # ==================== 新闻与主力数据 ====================

    def get_news(self, symbol: str, days: int = 7, limit: int = 20) -> list[dict[str, Any]]:
        """获取个股新闻。输入代码会自动标准化。"""
        symbol = self._normalize_symbol(symbol)
        return self._call_with_fallback_news("get_news", symbol, days, limit)

    def get_capital_flow(self, symbol: str, days: int = 20) -> pd.DataFrame:
        """获取资金流向。输入代码会自动标准化。"""
        symbol = self._normalize_symbol(symbol)
        return self._call_with_fallback_df("get_capital_flow", symbol, days)

    def get_dragon_tiger(self, symbol: str, days: int = 5) -> pd.DataFrame:
        """获取龙虎榜数据。输入代码会自动标准化。"""
        symbol = self._normalize_symbol(symbol)
        return self._call_with_fallback_df("get_dragon_tiger", symbol, days)

    def get_margin_balance(self, symbol: str, days: int = 20) -> pd.DataFrame:
        """获取融资融券余额。输入代码会自动标准化。"""
        symbol = self._normalize_symbol(symbol)
        return self._call_with_fallback_df("get_margin_balance", symbol, days)

    def get_northbound_flow(self, symbol: str, days: int = 20) -> pd.DataFrame:
        """获取北向资金流向。输入代码会自动标准化。"""
        symbol = self._normalize_symbol(symbol)
        return self._call_with_fallback_df("get_northbound_flow", symbol, days)

    def get_call_stats(self) -> dict[str, dict[str, Any]]:
        """获取调用统计。"""
        return self._call_stats.copy()

    def health_check(self) -> dict[str, Any]:
        """检查各数据提供商健康状态。"""
        from datetime import datetime, timedelta

        status: dict[str, Any] = {}
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        for name, provider in self.providers.items():
            method = getattr(provider, "get_ohlcv", None)
            if not method:
                status[name] = {"available": False, "reason": "无 get_ohlcv 方法"}
                continue
            try:
                # 用上证 50 ETF 做轻量探测，传入 start_date/end_date
                df = method("510050.SH", start_date=start_date, end_date=end_date, bars=2)
                status[name] = {"available": not df.empty, "sample_rows": len(df)}
            except Exception as exc:
                status[name] = {"available": False, "reason": str(exc)}
        return status
