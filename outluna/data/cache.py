"""数据缓存管理。"""

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import pandas as pd

from outluna.config import settings


class DataCache:
    """本地数据缓存管理器。

    支持 DataFrame 和 JSON 对象的本地缓存，按 TTL 自动过期。
    """

    def __init__(self, cache_dir: Path | None = None, ttl_hours: int | None = None):
        self.cache_dir = cache_dir or settings.cache_dir
        # ttl_hours 为 0 时，使用 0 秒；为 None 时使用配置默认值
        if ttl_hours is None:
            self.ttl_seconds = settings.cache_ttl_hours * 3600
        else:
            self.ttl_seconds = ttl_hours * 3600
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _key_to_path(self, key: str, suffix: str) -> Path:
        """将缓存键转换为文件路径。"""
        hashed = hashlib.md5(key.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{hashed}{suffix}"

    def _is_expired(self, path: Path) -> bool:
        """判断缓存文件是否过期。"""
        if not path.exists():
            return True
        if self.ttl_seconds <= 0:
            return False
        return time.time() - path.stat().st_mtime > self.ttl_seconds

    def get_df(self, key: str) -> pd.DataFrame | None:
        """获取缓存的 DataFrame。"""
        path = self._key_to_path(key, ".parquet")
        if self._is_expired(path):
            return None
        try:
            return pd.read_parquet(path)
        except Exception:
            return None

    def set_df(self, key: str, df: pd.DataFrame) -> None:
        """设置 DataFrame 缓存。"""
        path = self._key_to_path(key, ".parquet")
        df.to_parquet(path, index=False)

    def get_json(self, key: str) -> Any | None:
        """获取缓存的 JSON 对象。"""
        path = self._key_to_path(key, ".json")
        if self._is_expired(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def set_json(self, key: str, value: Any) -> None:
        """设置 JSON 缓存。"""
        path = self._key_to_path(key, ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, default=str)

    def clear(self) -> None:
        """清空所有缓存。"""
        for path in self.cache_dir.iterdir():
            if path.is_file():
                path.unlink()
