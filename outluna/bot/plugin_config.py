"""AstrBot 插件私有配置加载器。

OutLuna 作为 AstrBot 插件运行时，环境变量或 .env 文件可能不便于配置。
本模块读取插件 data 目录下的 outluna_config.json，并将配置覆盖到全局 settings。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_plugin_config(plugin_data_dir: Path) -> dict[str, Any]:
    """加载插件目录下的 data/outluna_config.json。

    Args:
        plugin_data_dir: 插件 data 目录路径。

    Returns:
        配置字典；文件不存在或解析失败时返回空字典。
    """
    config_path = plugin_data_dir / "outluna_config.json"
    if not config_path.exists():
        return {}
    try:
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError) as exc:
        from outluna.utils.logger import setup_logging

        logger = setup_logging()
        logger.warning(f"读取插件配置失败：{exc}")
    return {}


def apply_plugin_config(plugin_data_dir: Path, settings: Any) -> None:
    """将插件配置覆盖到全局 settings。

    Args:
        plugin_data_dir: 插件 data 目录路径。
        settings: OutLuna 全局 Settings 实例。
    """
    config = load_plugin_config(plugin_data_dir)
    for key, value in config.items():
        if not isinstance(key, str) or not key:
            continue
        if hasattr(settings, key):
            try:
                setattr(settings, key, value)
            except Exception as exc:
                from outluna.utils.logger import setup_logging

                logger = setup_logging()
                logger.warning(f"应用插件配置 {key} 失败：{exc}")
