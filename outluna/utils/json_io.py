"""JSON 文件安全读写工具。

提供两类能力，防止"文件损坏 -> 读取回退空结构 -> 写覆盖"导致的数据静默丢失：
1. 原子写入：先写临时文件再 ``os.replace`` 替换，避免写入中途崩溃留下截断文件；
2. 严格读取：解析失败时备份损坏文件并抛出异常，写路径据此放弃本次写入（fail-closed）。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from outluna.utils.logger import setup_logging

logger = setup_logging()


class JsonFileCorruptedError(RuntimeError):
    """JSON 文件损坏且已备份时抛出，写路径应捕获并放弃本次写入。"""


def write_json_atomic(file_path: Path, data: Any) -> None:
    """原子方式写入 JSON 文件（临时文件 + os.replace）。

    Args:
        file_path: 目标文件路径。
        data: 可 JSON 序列化的数据。
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = file_path.with_name(f"{file_path.name}.{os.getpid()}.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, file_path)


def read_json_strict(file_path: Path) -> dict[str, Any]:
    """严格读取 JSON 文件，损坏时备份并抛出 :class:`JsonFileCorruptedError`。

    Args:
        file_path: 目标文件路径。

    Returns:
        解析后的字典；文件不存在时返回空字典。

    Raises:
        JsonFileCorruptedError: 文件存在但解析失败（已自动备份为 ``.corrupt``）。
    """
    if not file_path.exists():
        return {}
    try:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        backup_corrupt_file(file_path)
        raise JsonFileCorruptedError(f"JSON 文件损坏（已备份）：{file_path} - {exc}") from exc
    if not isinstance(data, dict):
        backup_corrupt_file(file_path)
        raise JsonFileCorruptedError(f"JSON 文件内容不是对象（已备份）：{file_path}")
    return data


def backup_corrupt_file(file_path: Path) -> Path | None:
    """将损坏的文件重命名为带时间戳的 ``.corrupt`` 备份，便于人工恢复。

    Returns:
        备份文件路径；备份失败时返回 None。
    """
    backup_path = file_path.with_name(f"{file_path.name}.{int(time.time())}.corrupt")
    try:
        os.replace(file_path, backup_path)
        logger.warning(f"已备份损坏文件：{file_path} -> {backup_path}")
        return backup_path
    except OSError as exc:
        logger.error(f"损坏文件备份失败：{file_path} - {exc}")
        return None
