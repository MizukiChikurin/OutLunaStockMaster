"""自选股池持久化存储。

自选股池以股票代码列表为核心信息，持久化保存到 ``data/watchlist.json``。
支持添加、删除、查询、清空等操作，并记录最后更新时间。

每只股票携带元数据（加入日期、来源标记），用于支持自动化工作流：
- 来源（source）：``manual`` 表示用户手动添加，``auto`` 表示自动化选股入库；
- 加入日期（added_date）：自动入库的股票按交易日计算保留期，超期自动移除；
  手动添加的股票不受自动移除机制影响。

写入采用原子替换（临时文件 + os.replace）；读取损坏时写路径 fail-closed
（备份损坏文件并抛异常），避免"损坏 -> 回退空结构 -> 覆盖"的静默数据丢失。
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from outluna.config import settings
from outluna.utils.json_io import read_json_strict, write_json_atomic
from outluna.utils.logger import setup_logging
from outluna.utils.symbol import SymbolNormalizer

logger = setup_logging()

#: 股票来源：手动添加
SOURCE_MANUAL = "manual"
#: 股票来源：自动化工作流入库
SOURCE_AUTO = "auto"
#: 自动入库股票的默认保留期（交易日数）：加入日记为第 1 天，第 N+1 个交易日移除
AUTO_KEEP_TRADE_DAYS = 5


@dataclass
class WatchlistItem:
    """自选股池单只股票的元数据。"""

    symbol: str
    added_date: str = ""
    """加入日期，``YYYY-MM-DD`` 格式；空字符串表示未知（按股池更新时间兜底）。"""
    source: str = SOURCE_MANUAL
    """来源标记：``manual`` 手动添加 / ``auto`` 自动化入库。"""

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        return {
            "symbol": self.symbol,
            "added_date": self.added_date,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WatchlistItem:
        """从字典反序列化，容忍缺失字段。"""
        return cls(
            symbol=str(data.get("symbol", "")),
            added_date=str(data.get("added_date", "") or ""),
            source=str(data.get("source", SOURCE_MANUAL) or SOURCE_MANUAL),
        )


@dataclass
class Watchlist:
    """自选股池数据模型。"""

    items: list[WatchlistItem] = field(default_factory=list)
    updated_at: datetime = field(default_factory=datetime.now)

    @property
    def symbols(self) -> list[str]:
        """返回股票代码列表（兼容旧接口）。"""
        return [item.symbol for item in self.items]

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。

        同时冗余写入 ``symbols`` 字段，保证旧版本代码读取新文件时
        仍能获得股票代码列表（双向兼容）。
        """
        return {
            "items": [item.to_dict() for item in self.items],
            "symbols": self.symbols,
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Watchlist:
        """从字典反序列化。

        兼容两种文件格式：
        - 新格式：包含 ``items`` 列表（含元数据）；
        - 旧格式：仅包含 ``symbols`` 代码列表，自动转换为手动来源、
          加入日期取股池更新日期。
        """
        updated_at_str = data.get("updated_at", "")
        try:
            updated_at = datetime.fromisoformat(updated_at_str)
        except ValueError:
            updated_at = datetime.now()

        items_data = data.get("items")
        if isinstance(items_data, list) and items_data:
            items = [WatchlistItem.from_dict(item) for item in items_data if isinstance(item, dict)]
            items = [item for item in items if item.symbol]
            return cls(items=items, updated_at=updated_at)

        # 旧格式兼容：仅有 symbols 列表
        symbols = data.get("symbols", []) or []
        fallback_date = updated_at.date().isoformat()
        items = [
            WatchlistItem(symbol=str(symbol), added_date=fallback_date, source=SOURCE_MANUAL)
            for symbol in symbols
            if symbol
        ]
        return cls(items=items, updated_at=updated_at)


class WatchlistStorage:
    """自选股池存储管理。

    使用 JSON 文件作为持久化介质，默认路径为 ``data/watchlist.json``。
    所有写入操作都会自动将股票代码标准化为内部格式，并去重保存。
    """

    def __init__(self, file_path: Path | None = None):
        """初始化存储，自动创建数据目录和文件。"""
        self.file_path = (
            file_path or (settings.data_dir or settings.project_dir / "data") / "watchlist.json"
        )
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_file()

    def _ensure_file(self) -> None:
        """确保 JSON 文件存在，不存在则写入默认空结构。"""
        if not self.file_path.exists():
            self._save(Watchlist())

    def _load(self, strict: bool = False) -> Watchlist:
        """从文件加载自选股池。

        Args:
            strict: 为 True 时文件损坏会备份并抛出异常（fail-closed），
                用于写入前置读取，防止以空快照覆盖原文件；
                为 False 时损坏仅告警并返回空股池，用于纯读场景。
        """
        if strict:
            return Watchlist.from_dict(read_json_strict(self.file_path))
        try:
            return Watchlist.from_dict(read_json_strict(self.file_path))
        except Exception as exc:
            logger.warning(f"自选股池文件读取失败，将使用空股池：{exc}")
            return Watchlist()

    def _save(self, watchlist: Watchlist) -> None:
        """将自选股池原子写入文件（标准化 + 去重 + 刷新更新时间）。"""
        seen: set[str] = set()
        deduped_items: list[WatchlistItem] = []
        for item in watchlist.items:
            normalized = SymbolNormalizer.normalize(item.symbol)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            item.symbol = normalized
            deduped_items.append(item)
        watchlist.items = deduped_items
        watchlist.updated_at = datetime.now()
        write_json_atomic(self.file_path, watchlist.to_dict())

    def add(
        self,
        symbol: str,
        source: str = SOURCE_MANUAL,
        added_date: str | None = None,
    ) -> bool:
        """添加一只股票到自选股池。

        Args:
            symbol: 原始股票代码，会被自动标准化。
            source: 来源标记，``manual``（手动）或 ``auto``（自动化入库）。
            added_date: 加入日期（``YYYY-MM-DD``），默认取今天。

        Returns:
            如果股票已存在则返回 False，否则返回 True。
        """
        normalized = SymbolNormalizer.normalize(symbol)
        if not normalized:
            raise ValueError(f"股票代码格式错误：{symbol}")
        watchlist = self._load(strict=True)
        if normalized in watchlist.symbols:
            return False
        watchlist.items.append(
            WatchlistItem(
                symbol=normalized,
                added_date=added_date or datetime.now().date().isoformat(),
                source=source,
            )
        )
        self._save(watchlist)
        logger.info(f"自选股池添加：{normalized}（来源：{source}）")
        return True

    def set_source(self, symbol: str, source: str) -> bool:
        """修改已存在股票的来源标记（如 auto 升级为 manual）。

        Returns:
            股票存在并修改成功返回 True，不存在返回 False。
        """
        normalized = SymbolNormalizer.normalize(symbol)
        if not normalized:
            raise ValueError(f"股票代码格式错误：{symbol}")
        watchlist = self._load(strict=True)
        for item in watchlist.items:
            if item.symbol == normalized:
                if item.source == source:
                    return True
                item.source = source
                self._save(watchlist)
                logger.info(f"自选股池来源变更：{normalized} -> {source}")
                return True
        return False

    def refresh_added_date(self, symbol: str, added_date: str | None = None) -> bool:
        """刷新已存在股票的加入日期（用于自动入库股票被重复推荐的场景）。

        Args:
            symbol: 原始股票代码，会被自动标准化。
            added_date: 新的加入日期（``YYYY-MM-DD``），默认取今天。

        Returns:
            股票存在并刷新成功返回 True，不存在返回 False。
        """
        normalized = SymbolNormalizer.normalize(symbol)
        if not normalized:
            raise ValueError(f"股票代码格式错误：{symbol}")
        watchlist = self._load(strict=True)
        for item in watchlist.items:
            if item.symbol == normalized:
                item.added_date = added_date or datetime.now().date().isoformat()
                self._save(watchlist)
                logger.info(f"自选股池刷新加入日期：{normalized} -> {item.added_date}")
                return True
        return False

    def upsert_auto_items(
        self,
        symbols: list[str],
        added_date: str | None = None,
    ) -> dict[str, list[str]]:
        """批量将自动选股结果写入股池（一次读取、一次写入）。

        规则：新股票以 auto 来源加入；已在池中的 auto 股票刷新加入日期；
        手动添加的股票保持原样。

        Args:
            symbols: 待入库股票代码列表（会被标准化、去重）。
            added_date: 加入日期（``YYYY-MM-DD``），默认取今天。

        Returns:
            分类结果字典：``{"added": [...], "refreshed": [...], "kept_manual": [...]}``，
            各值为标准化后的股票代码列表。
        """
        added_date = added_date or datetime.now().date().isoformat()
        watchlist = self._load(strict=True)
        existing = {item.symbol: item for item in watchlist.items}
        result: dict[str, list[str]] = {"added": [], "refreshed": [], "kept_manual": []}
        changed = False
        for symbol in SymbolNormalizer.normalize_list(symbols):
            item = existing.get(symbol)
            if item is None:
                watchlist.items.append(
                    WatchlistItem(symbol=symbol, added_date=added_date, source=SOURCE_AUTO)
                )
                existing[symbol] = watchlist.items[-1]
                result["added"].append(symbol)
                changed = True
            elif item.source == SOURCE_AUTO:
                if item.added_date != added_date:
                    item.added_date = added_date
                    changed = True
                result["refreshed"].append(symbol)
            else:
                result["kept_manual"].append(symbol)
        if changed:
            self._save(watchlist)
        logger.info(
            f"自动入库批量写入：新增 {len(result['added'])}，"
            f"刷新 {len(result['refreshed'])}，手动保留 {len(result['kept_manual'])}"
        )
        return result

    def get_item(self, symbol: str) -> WatchlistItem | None:
        """获取单只股票的元数据，不存在时返回 None。"""
        normalized = SymbolNormalizer.normalize(symbol)
        if not normalized:
            return None
        for item in self._load().items:
            if item.symbol == normalized:
                return item
        return None

    def remove_expired_auto(
        self,
        trade_dates: list[str],
        today: str | None = None,
        keep_days: int = AUTO_KEEP_TRADE_DAYS,
    ) -> list[str]:
        """移除保留期满的自动入库股票。

        保留期口径：加入日记为第 1 个交易日，共保留 ``keep_days`` 个交易日，
        第 ``keep_days + 1`` 个交易日移除。手动添加的股票不受影响。

        Args:
            trade_dates: 严格升序排列的交易日列表（``YYYY-MM-DD`` 字符串，
                字典序与时间序一致），需覆盖加入日期至今天的区间。
            today: 今天日期（``YYYY-MM-DD``），默认取系统今天。
            keep_days: 保留的交易日数量，默认 :data:`AUTO_KEEP_TRADE_DAYS`。

        Returns:
            被移除的股票代码列表。
        """
        today = today or datetime.now().date().isoformat()
        watchlist = self._load(strict=True)
        removed: list[str] = []
        kept_items: list[WatchlistItem] = []
        for item in watchlist.items:
            if item.source != SOURCE_AUTO or not item.added_date:
                kept_items.append(item)
                continue
            # 统计 [加入日期, 今天] 区间内的交易日数量（加入日记为第 1 天）
            lo = bisect_left(trade_dates, item.added_date)
            hi = bisect_right(trade_dates, today)
            held_days = hi - lo
            if held_days > keep_days:
                removed.append(item.symbol)
                logger.info(
                    f"自动入库股票保留期满移除：{item.symbol}"
                    f"（加入：{item.added_date}，已持有 {held_days} 个交易日）"
                )
            else:
                kept_items.append(item)
        if removed:
            watchlist.items = kept_items
            self._save(watchlist)
        return removed

    def remove(self, symbol: str) -> bool:
        """从自选股池中移除一只股票。

        Args:
            symbol: 原始股票代码，会被自动标准化。

        Returns:
            如果股票存在并成功移除返回 True，否则返回 False。
        """
        normalized = SymbolNormalizer.normalize(symbol)
        if not normalized:
            raise ValueError(f"股票代码格式错误：{symbol}")
        watchlist = self._load(strict=True)
        if normalized not in watchlist.symbols:
            return False
        watchlist.items = [item for item in watchlist.items if item.symbol != normalized]
        self._save(watchlist)
        logger.info(f"自选股池移除：{normalized}")
        return True

    def list(self) -> list[str]:
        """返回当前自选股池中的所有股票代码（已标准化）。"""
        watchlist = self._load()
        return watchlist.symbols.copy()

    def clear(self) -> None:
        """清空自选股池。"""
        self._save(Watchlist())
        logger.info("自选股池已清空")

    def exists(self, symbol: str) -> bool:
        """判断某只股票是否已在自选股池中。"""
        normalized = SymbolNormalizer.normalize(symbol)
        return normalized in self._load().symbols

    def info(self) -> dict[str, Any]:
        """返回自选股池的元信息。"""
        watchlist = self._load()
        return {
            "symbols": watchlist.symbols.copy(),
            "count": len(watchlist.items),
            "updated_at": watchlist.updated_at.isoformat(),
            "file_path": str(self.file_path),
        }
