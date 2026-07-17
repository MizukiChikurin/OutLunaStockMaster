"""A 股交易日历工具。

通过 akshare 获取历史交易日历，并缓存到本地 JSON 文件（每日刷新一次）。
网络不可用或 akshare 不可用时，回退使用本地缓存；缓存也不可用时，
以"周一至周五"作为粗略兜底。

所有日期均使用 ``YYYY-MM-DD`` 字符串表示，字典序与时间序一致，便于比较。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from outluna.config import settings
from outluna.utils.logger import setup_logging

logger = setup_logging()


class TradeCalendar:
    """A 股交易日历。

    Args:
        cache_path: 本地缓存文件路径，默认 ``<cache_dir>/trade_calendar.json``。
    """

    def __init__(self, cache_path: Path | None = None):
        """初始化交易日历，确定缓存路径并置空内存缓存。"""
        self._cache_path = cache_path or (settings.cache_dir / "trade_calendar.json")
        self._dates: list[str] | None = None
        self._loaded_for: str | None = None

    def get_trade_dates(self) -> list[str]:
        """获取升序排列的全部交易日列表（``YYYY-MM-DD``）。

        优先使用当日内存缓存；否则依次尝试：akshare 网络获取 ->
        本地缓存文件 -> 周一至周五兜底。
        """
        today = date.today().isoformat()
        if self._dates is not None and self._loaded_for == today:
            return self._dates

        dates = self._fetch_from_akshare()
        if dates:
            self._save_cache(dates)
        else:
            dates = self._load_cache()
        if not dates:
            logger.warning("交易日历网络与缓存均不可用，回退为周一至周五")
            dates = self._weekday_fallback()

        self._dates = dates
        self._loaded_for = today
        return dates

    def is_trade_day(self, day: str | date) -> bool:
        """判断指定日期是否为交易日。"""
        day_str = day.isoformat() if isinstance(day, date) else str(day)
        return day_str in set(self.get_trade_dates())

    def today_is_trade_day(self) -> bool:
        """判断今天是否为交易日。"""
        return self.is_trade_day(date.today())

    def _fetch_from_akshare(self) -> list[str]:
        """从 akshare 拉取交易日历，失败时返回空列表。"""
        try:
            import akshare as ak

            df = ak.tool_trade_date_hist_sina()
            column = "trade_date" if "trade_date" in df.columns else df.columns[0]
            dates = sorted({self._to_date_str(value) for value in df[column].tolist()})
            return [d for d in dates if d]
        except Exception as exc:
            logger.warning(f"akshare 交易日历获取失败：{exc}")
            return []

    @staticmethod
    def _to_date_str(value: object) -> str:
        """将 akshare 返回的日期单元格转换为 ``YYYY-MM-DD`` 字符串。"""
        if isinstance(value, date):
            return value.isoformat()
        text = str(value).strip()
        return text[:10] if len(text) >= 10 else text

    def _load_cache(self) -> list[str]:
        """从本地缓存文件读取交易日历，失败时返回空列表。"""
        try:
            if not self._cache_path.exists():
                return []
            with open(self._cache_path, encoding="utf-8") as f:
                data = json.load(f)
            dates = data.get("dates", [])
            if isinstance(dates, list) and dates:
                return sorted(str(d) for d in dates)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(f"交易日历缓存读取失败：{exc}")
        return []

    def _save_cache(self, dates: list[str]) -> None:
        """将交易日历写入本地缓存文件。"""
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._cache_path, "w", encoding="utf-8") as f:
                json.dump(
                    {"dates": dates, "fetched_at": date.today().isoformat()},
                    f,
                    ensure_ascii=False,
                )
        except OSError as exc:
            logger.warning(f"交易日历缓存写入失败：{exc}")

    @staticmethod
    def _weekday_fallback() -> list[str]:
        """兜底方案：生成过去一年至今的周一至周五日期列表。"""
        today = date.today()
        start = today - timedelta(days=370)
        dates: list[str] = []
        current = start
        while current <= today:
            if current.weekday() < 5:
                dates.append(current.isoformat())
            current += timedelta(days=1)
        return dates
