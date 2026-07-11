"""自选股池持久化存储。

自选股池以股票代码列表为核心信息，持久化保存到 ``data/watchlist.json``。
支持添加、删除、查询、清空等操作，并记录最后更新时间。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from outluna.config import settings
from outluna.utils.logger import setup_logging
from outluna.utils.symbol import SymbolNormalizer

logger = setup_logging()


@dataclass
class Watchlist:
    """自选股池数据模型。"""

    symbols: list[str] = field(default_factory=list)
    updated_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        return {
            "symbols": self.symbols,
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Watchlist:
        """从字典反序列化。"""
        updated_at_str = data.get("updated_at", "")
        try:
            updated_at = datetime.fromisoformat(updated_at_str)
        except ValueError:
            updated_at = datetime.now()
        symbols = data.get("symbols", []) or []
        return cls(symbols=symbols, updated_at=updated_at)


class WatchlistStorage:
    """自选股池存储管理。

    使用 JSON 文件作为持久化介质，默认路径为 ``data/watchlist.json``。
    所有写入操作都会自动将股票代码标准化为内部格式，并去重保存。
    """

    def __init__(self, file_path: Path | None = None):
        """初始化存储，自动创建数据目录和文件。"""
        self.file_path = file_path or (settings.data_dir or settings.project_dir / "data") / "watchlist.json"
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_file()

    def _ensure_file(self) -> None:
        """确保 JSON 文件存在，不存在则写入默认空结构。"""
        if not self.file_path.exists():
            self._save(Watchlist())

    def _load(self) -> Watchlist:
        """从文件加载自选股池。"""
        try:
            with open(self.file_path, encoding="utf-8") as f:
                data = json.load(f)
            return Watchlist.from_dict(data)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(f"自选股池文件读取失败，将使用空股池：{exc}")
            return Watchlist()

    def _save(self, watchlist: Watchlist) -> None:
        """将自选股池保存到文件。"""
        watchlist.symbols = SymbolNormalizer.normalize_list(watchlist.symbols)
        watchlist.updated_at = datetime.now()
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(watchlist.to_dict(), f, ensure_ascii=False, indent=2)

    def add(self, symbol: str) -> bool:
        """添加一只股票到自选股池。

        Args:
            symbol: 原始股票代码，会被自动标准化。

        Returns:
            如果股票已存在则返回 False，否则返回 True。
        """
        normalized = SymbolNormalizer.normalize(symbol)
        if not normalized:
            raise ValueError(f"股票代码格式错误：{symbol}")
        watchlist = self._load()
        if normalized in watchlist.symbols:
            return False
        watchlist.symbols.append(normalized)
        self._save(watchlist)
        logger.info(f"自选股池添加：{normalized}")
        return True

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
        watchlist = self._load()
        if normalized not in watchlist.symbols:
            return False
        watchlist.symbols.remove(normalized)
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
            "count": len(watchlist.symbols),
            "updated_at": watchlist.updated_at.isoformat(),
            "file_path": str(self.file_path),
        }
