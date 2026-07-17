"""自动化工作流调度器。

基于 asyncio 的轻量轮询调度器，在 AstrBot 插件 ``initialize()`` 时启动、
``terminate()`` 时停止。每 ``tick_seconds`` 检查一次当前时间，对开启
自动化功能的群聊执行：

1. 追踪股池推送：命中配置的推送时间点时执行；
2. 封盘后自动选股：命中配置的选股时间时执行；
3. 过期清理：每个交易日首次检查时，移除保留期满的自动入库股票。

仅在 A 股交易日执行任务。同一任务槽位（日期 + 群 + 时间点）当日只执行一次。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime

from outluna.bot.auto_config import AutoWorkflowStorage
from outluna.utils.logger import setup_logging
from outluna.utils.trade_calendar import TradeCalendar

logger = setup_logging()

#: 追踪股池推送回调类型：参数为群聊 unified_msg_origin
TrackCallback = Callable[[str], Awaitable[None]]
#: 自动选股回调类型：参数为群聊 unified_msg_origin
ScanCallback = Callable[[str], Awaitable[None]]
#: 过期清理回调类型
CleanupCallback = Callable[[], Awaitable[None]]


class AutoScheduler:
    """自动化工作流调度器。

    Args:
        storage: 自动化配置存储。
        trade_calendar: 交易日历，用于跳过非交易日。
        on_track: 追踪股池推送回调。
        on_scan: 自动选股回调。
        on_cleanup: 过期股票清理回调（可选）。
        tick_seconds: 轮询间隔秒数，默认 20 秒，需远小于 60 秒以保证
            每个 ``HH:MM`` 分钟内至少命中一次。
    """

    def __init__(
        self,
        storage: AutoWorkflowStorage,
        trade_calendar: TradeCalendar,
        on_track: TrackCallback,
        on_scan: ScanCallback,
        on_cleanup: CleanupCallback | None = None,
        tick_seconds: int = 20,
    ):
        """初始化调度器，注入配置、交易日历与各类任务回调。"""
        self._storage = storage
        self._trade_calendar = trade_calendar
        self._on_track = on_track
        self._on_scan = on_scan
        self._on_cleanup = on_cleanup
        self._tick_seconds = tick_seconds
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        # 当日已执行的任务槽位：(日期, 群聊umo, 槽位标识)
        self._executed: set[tuple[str, str, str]] = set()
        self._last_cleanup_date: str | None = None

    async def start(self) -> None:
        """启动调度循环。"""
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop(), name="outluna-auto-scheduler")
        logger.info(f"自动化工作流调度器已启动（间隔 {self._tick_seconds} 秒）")

    async def stop(self) -> None:
        """停止调度循环并等待任务退出。"""
        if self._task is None:
            return
        self._stop_event.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        logger.info("自动化工作流调度器已停止")

    async def _loop(self) -> None:
        """调度主循环：按间隔执行检查，异常时记录并继续。"""
        while not self._stop_event.is_set():
            try:
                await self._tick(datetime.now())
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(f"自动化调度检查异常：{exc}")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._tick_seconds)
            except TimeoutError:
                continue

    async def _tick(self, now: datetime) -> None:
        """执行一次调度检查。

        Args:
            now: 当前时间（注入参数便于测试）。
        """
        today = now.date().isoformat()
        hhmm = now.strftime("%H:%M")

        # 跨天时清理历史执行记录，只保留今天的槽位
        self._executed = {key for key in self._executed if key[0] == today}

        # 非交易日不执行任何任务
        if not self._trade_calendar.is_trade_day(today):
            return

        # 每个交易日首次检查时执行过期清理（先于推送，保证推送数据已清理）
        if self._last_cleanup_date != today:
            self._last_cleanup_date = today
            if self._on_cleanup is not None:
                await self._safe_run("过期清理", self._on_cleanup)

        for umo, config in self._storage.list_groups().items():
            if not config.enabled:
                continue
            if hhmm in config.track_times:
                slot = f"track:{hhmm}"
                if (today, umo, slot) not in self._executed:
                    self._executed.add((today, umo, slot))
                    await self._safe_run(f"追踪股池推送[{umo} {slot}]", self._on_track, umo)
            if hhmm == config.scan_time:
                slot = "scan"
                if (today, umo, slot) not in self._executed:
                    self._executed.add((today, umo, slot))
                    await self._safe_run(f"自动选股[{umo}]", self._on_scan, umo)

    async def _safe_run(self, label: str, callback: Callable, *args) -> None:
        """执行任务回调并捕获异常，避免单个任务失败影响调度循环。"""
        try:
            logger.info(f"自动化任务开始：{label}")
            await callback(*args)
            logger.info(f"自动化任务完成：{label}")
        except Exception as exc:
            logger.error(f"自动化任务失败：{label} - {exc}")
