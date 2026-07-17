"""自动化工作流调度器。

基于 asyncio 的轻量轮询调度器，在 AstrBot 插件 ``initialize()`` 时启动、
``terminate()`` 时停止。每 ``tick_seconds`` 检查一次当前时间，对开启
自动化功能的群聊执行：

1. 追踪股池推送：命中配置的推送时间点时执行；
2. 封盘后自动选股：命中配置的选股时间时执行；
3. 过期清理：每个交易日首次检查时，移除保留期满的自动入库股票。

仅在 A 股交易日执行任务。同一任务槽位（日期 + 群 + 时间点）当日只执行一次，
执行状态持久化到 ``auto_scheduler_state.json``，进程重启/插件热重载后不重复执行。
交易日历处于兜底模式（数据不可信）时，跳过选股入库与过期清理，仅保留推送。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from outluna.bot.auto_config import AutoWorkflowStorage
from outluna.utils.json_io import read_json_strict, write_json_atomic
from outluna.utils.logger import setup_logging
from outluna.utils.trade_calendar import TradeCalendar

logger = setup_logging()

#: 追踪股池推送回调类型：参数为群聊 unified_msg_origin
TrackCallback = Callable[[str], Awaitable[None]]
#: 自动选股回调类型：参数为群聊 unified_msg_origin
ScanCallback = Callable[[str], Awaitable[None]]
#: 过期清理回调类型
CleanupCallback = Callable[[], Awaitable[None]]

#: 清理任务失败后的重试间隔（秒）
_CLEANUP_RETRY_SECONDS = 600


class AutoScheduler:
    """自动化工作流调度器。

    Args:
        storage: 自动化配置存储。
        trade_calendar: 交易日历，用于跳过非交易日。
        on_track: 追踪股池推送回调。
        on_scan: 自动选股回调。
        on_cleanup: 过期股票清理回调（可选）。
        state_path: 执行状态持久化文件路径；为 None 时状态仅保存在内存。
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
        state_path: Path | None = None,
        tick_seconds: int = 20,
    ):
        """初始化调度器，注入配置、交易日历与各类任务回调。"""
        self._storage = storage
        self._trade_calendar = trade_calendar
        self._on_track = on_track
        self._on_scan = on_scan
        self._on_cleanup = on_cleanup
        self._state_path = state_path
        self._tick_seconds = tick_seconds
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        # 当日执行状态：{"date": str, "slots": {umo: [slot...]}, "cleanup_done": bool}
        self._state: dict[str, Any] = {"date": "", "slots": {}, "cleanup_done": False}
        self._cleanup_retry_after = 0.0

    async def start(self) -> None:
        """启动调度循环，并从状态文件恢复当日已执行槽位。"""
        if self._task is not None and not self._task.done():
            return
        self._load_state()
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
        self._rollover_state(today)

        # 非交易日不执行任何任务（日历拉取为阻塞网络调用，放入线程执行）
        if not await asyncio.to_thread(self._trade_calendar.is_trade_day, today):
            return

        # 兜底日历数据不可信：跳过选股入库与过期清理，仅保留推送类任务
        fallback = self._trade_calendar.is_fallback
        if fallback:
            logger.warning("交易日历处于兜底模式，今日跳过自动选股与过期清理")

        # 每个交易日执行一次过期清理（先于推送，保证推送数据已清理）；
        # 失败后按间隔重试，避免静默跳过一整天
        if not fallback and self._on_cleanup is not None and not self._state["cleanup_done"]:
            if time.time() >= self._cleanup_retry_after:
                if await self._safe_run("过期清理", self._on_cleanup):
                    self._state["cleanup_done"] = True
                    self._save_state()
                else:
                    self._cleanup_retry_after = time.time() + _CLEANUP_RETRY_SECONDS
                    logger.warning(f"过期清理失败，{_CLEANUP_RETRY_SECONDS} 秒后重试")

        for umo, config in self._storage.list_groups().items():
            if not config.enabled:
                continue
            slots = self._state["slots"].setdefault(umo, [])
            if hhmm in config.track_times:
                slot = f"track:{hhmm}"
                if slot not in slots:
                    slots.append(slot)
                    self._save_state()
                    await self._safe_run(f"追踪股池推送[{umo} {slot}]", self._on_track, umo)
            if not fallback and hhmm == config.scan_time:
                slot = "scan"
                if slot not in slots:
                    slots.append(slot)
                    self._save_state()
                    await self._safe_run(f"自动选股[{umo}]", self._on_scan, umo)

    async def _safe_run(self, label: str, callback: Callable, *args) -> bool:
        """执行任务回调并捕获异常，避免单个任务失败影响调度循环。

        Returns:
            任务是否成功完成。
        """
        try:
            logger.info(f"自动化任务开始：{label}")
            await callback(*args)
            logger.info(f"自动化任务完成：{label}")
            return True
        except Exception as exc:
            logger.error(f"自动化任务失败：{label} - {exc}")
            return False

    def _rollover_state(self, today: str) -> None:
        """跨天时重置当日执行状态。"""
        if self._state["date"] != today:
            self._state = {"date": today, "slots": {}, "cleanup_done": False}

    def _load_state(self) -> None:
        """从状态文件恢复当日执行状态；文件缺失、损坏或已跨天时使用空状态。"""
        today = datetime.now().date().isoformat()
        self._state = {"date": today, "slots": {}, "cleanup_done": False}
        if self._state_path is None:
            return
        try:
            data = read_json_strict(self._state_path)
        except Exception as exc:
            logger.warning(f"调度状态文件读取失败，按全新状态启动：{exc}")
            return
        if data.get("date") != today:
            return
        slots = data.get("slots")
        if isinstance(slots, dict):
            self._state["slots"] = {
                str(umo): [str(s) for s in slot_list]
                for umo, slot_list in slots.items()
                if isinstance(slot_list, list)
            }
        self._state["cleanup_done"] = bool(data.get("cleanup_done", False))
        logger.info(f"调度状态已恢复：{today}，清理完成={self._state['cleanup_done']}")

    def _save_state(self) -> None:
        """将当日执行状态原子写入状态文件。"""
        if self._state_path is None:
            return
        try:
            write_json_atomic(self._state_path, self._state)
        except OSError as exc:
            logger.warning(f"调度状态文件写入失败：{exc}")
