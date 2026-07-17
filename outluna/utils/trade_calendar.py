"""A 股交易日历工具。

通过 akshare 获取历史交易日历，并缓存到本地 JSON 文件（每日刷新一次）。
网络不可用或 akshare 不可用时，回退使用本地缓存；缓存也不可用时，
以"周一至周五"作为粗略兜底。

数据完整性策略：
- 网络拉取按 ``_RETRY_SECONDS`` 节流，失败后可重试；
- 拉取结果未覆盖今天且今天为工作日时，视为上游数据未刷新：不锁存、
  不落盘，并在交易日判定中乐观按交易日处理（避免当日任务静默跳过）；
- 缓存文件仅在数据覆盖今天时写入，避免不完整数据污染兜底。

所有日期均使用 ``YYYY-MM-DD`` 字符串表示，字典序与时间序一致，便于比较。
"""

from __future__ import annotations

import json
import time
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

    #: 网络拉取最小间隔（秒），失败或不完整数据场景下的重试节流
    _RETRY_SECONDS = 600

    def __init__(self, cache_path: Path | None = None):
        """初始化交易日历，确定缓存路径并置空内存缓存。"""
        self._cache_path = cache_path or (settings.cache_dir / "trade_calendar.json")
        self._dates: list[str] | None = None
        self._loaded_for: str | None = None
        self._source = "none"  # akshare / cache / fallback / none
        self._covers_today = False
        self._last_fetch_attempt = 0.0

    @property
    def is_fallback(self) -> bool:
        """当前日历是否来自"周一至周五"兜底（数据不可信，节假日会被当作交易日）。"""
        return self._source == "fallback"

    def get_trade_dates(self) -> list[str]:
        """获取升序排列的全部交易日列表（``YYYY-MM-DD``）。

        优先使用当日内存缓存；否则按节流尝试 akshare 网络获取，
        失败后依次回退本地缓存、周一至周五兜底。
        """
        today = date.today().isoformat()
        if self._dates is not None and self._loaded_for == today:
            return self._dates

        dates: list[str] = []
        now_ts = time.time()
        if now_ts - self._last_fetch_attempt >= self._RETRY_SECONDS:
            self._last_fetch_attempt = now_ts
            dates = self._fetch_from_akshare()

        if dates:
            if self._covers(dates, today):
                self._save_cache(dates)
                self._memorize(dates, source="akshare", covers_today=True)
            else:
                # 上游暂未包含今天：使用本次结果但不锁存、不落盘，稍后重试
                logger.warning("交易日历暂不含今日数据，将稍后重试刷新")
                self._memorize(dates, source="akshare", covers_today=False, lock=False)
            return dates

        # 节流窗口内或网络失败：优先复用已有的最佳结果（可能未覆盖今天），
        # 避免退化为更不可信的缓存/兜底数据
        if self._dates is not None:
            return self._dates

        cached = self._load_cache()
        if cached:
            covers = self._covers(cached, today)
            # 缓存过期（未覆盖今天的工作日）时不锁存，下个周期继续尝试网络
            self._memorize(cached, source="cache", covers_today=covers, lock=covers)
            return cached

        logger.warning("交易日历网络与缓存均不可用，回退为周一至周五")
        fallback = self._weekday_fallback()
        self._memorize(fallback, source="fallback", covers_today=True)
        return fallback

    def is_trade_day(self, day: str | date) -> bool:
        """判断指定日期是否为交易日。

        数据未覆盖今天且今天为工作日时，对"今天"的判定乐观按交易日处理
        并输出告警，避免因上游数据未刷新导致当日任务全天静默跳过。
        """
        day_str = day.isoformat() if isinstance(day, date) else str(day)
        dates = self.get_trade_dates()
        if day_str in set(dates):
            return True
        today_str = date.today().isoformat()
        if day_str == today_str and not self._covers_today and date.today().weekday() < 5:
            logger.warning("交易日历数据未覆盖今天，按工作日乐观执行今日任务")
            return True
        return False

    @staticmethod
    def _covers(dates: list[str], today: str) -> bool:
        """判断日历数据是否完整覆盖今天（周末历史数据天然不含今天，视为完整）。"""
        if not dates:
            return False
        if dates[-1] >= today:
            return True
        return date.today().weekday() >= 5

    def _memorize(
        self,
        dates: list[str],
        source: str,
        covers_today: bool,
        lock: bool = True,
    ) -> None:
        """更新内存缓存状态；``lock=False`` 时不按日锁存以便后续重试刷新。"""
        self._dates = dates
        self._source = source
        self._covers_today = covers_today
        self._loaded_for = date.today().isoformat() if lock else None

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
        """将交易日历写入本地缓存文件（仅在数据覆盖今天时调用）。"""
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
