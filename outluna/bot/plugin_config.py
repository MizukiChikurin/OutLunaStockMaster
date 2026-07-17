"""AstrBot 插件私有配置加载器。

OutLuna 作为 AstrBot 插件运行时，环境变量或 .env 文件可能不便于配置。
本模块读取插件 data 目录下的 outluna_config.json，并将配置覆盖到全局 settings。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from outluna.utils.logger import setup_logging

logger = setup_logging()

DEFAULT_PLUGIN_CONFIG: dict[str, Any] = {
    "prefer_kimi_api": True,
    "image_tables_enabled": True,
}


def load_plugin_config(plugin_data_dir: Path) -> dict[str, Any]:
    """加载插件目录下的 data/outluna_config.json。

    若配置文件不存在，会自动创建一份默认配置并启用 Kimi API 数据源。

    Args:
        plugin_data_dir: 插件 data 目录路径。

    Returns:
        配置字典；解析失败时返回空字典。
    """
    config_path = plugin_data_dir / "outluna_config.json"
    if not config_path.exists():
        try:
            plugin_data_dir.mkdir(parents=True, exist_ok=True)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_PLUGIN_CONFIG, f, ensure_ascii=False, indent=2)
            logger.info(f"已创建默认插件配置：{config_path}")
            return DEFAULT_PLUGIN_CONFIG.copy()
        except OSError as exc:
            logger.warning(f"创建默认插件配置失败：{exc}")
            return {}
    try:
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError) as exc:
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
                logger.warning(f"应用插件配置 {key} 失败：{exc}")


def relocate_settings_paths(
    plugin_data_dir: Path,
    settings: Any,
    old_data_dir: Path | None = None,
) -> None:
    """将基于文件系统的配置路径重定位到插件数据目录。

    AstrBot 插件模式下，所有持久化数据（缓存、数据库、自选股池、报告）
    都应保存在 AstrBot 提供的 ``data/plugin_data/outluna`` 目录中，
    避免插件更新导致数据丢失。

    若提供了旧数据目录，会自动将关键数据文件迁移到新目录，避免用户
    需要重新登录 Kimi 数据源或丢失历史数据。

    Args:
        plugin_data_dir: AstrBot 插件数据目录路径。
        settings: OutLuna 全局 Settings 实例。
        old_data_dir: 可选的旧数据目录路径，用于迁移已有数据文件。
    """
    plugin_data_dir = plugin_data_dir.resolve()
    if old_data_dir is not None and old_data_dir.exists():
        _migrate_data_files(old_data_dir, plugin_data_dir)

    settings.project_dir = plugin_data_dir
    settings.data_dir = plugin_data_dir
    # 触发 model_post_init 中的目录初始化和路径派生
    settings.model_post_init(None)


def _migrate_data_files(old_data_dir: Path, new_data_dir: Path) -> None:
    """将旧数据目录中的关键文件迁移到新数据目录。

    迁移列表包括：Kimi OAuth 凭证、SQLite 数据库、自选股池、插件配置。
    仅当新目录不存在同名文件时才执行迁移，避免覆盖。

    另外会将旧数据目录 ``strategies/`` 下的固定策略文件（选股策略_*.json）
    同步到新目录，详见 :func:`_migrate_strategies_dir`。
    """
    files_to_migrate = [
        "kimi_api_credentials.json",
        "outluna.db",
        "watchlist.json",
        "outluna_config.json",
    ]
    for file_name in files_to_migrate:
        old_path = old_data_dir / file_name
        new_path = new_data_dir / file_name
        if old_path.exists() and not new_path.exists():
            try:
                new_data_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(old_path, new_path)
                logger.info(f"已迁移数据文件：{old_path} -> {new_path}")
            except OSError as exc:
                logger.warning(f"迁移数据文件 {file_name} 失败：{exc}")
    _migrate_strategies_dir(old_data_dir, new_data_dir)


def _migrate_strategies_dir(old_data_dir: Path, new_data_dir: Path) -> None:
    """将旧数据目录 ``strategies/`` 下的固定策略文件迁移到新数据目录。

    策略文件属于插件随包配置，与凭证/自选股池等用户数据不同：
    插件包内的版本视为权威版本，启动时覆盖同名文件，保证插件更新后策略同步更新；
    内容完全一致的文件会跳过以避免无意义写入；
    用户在插件数据目录中自行新增的策略文件不受影响。

    Args:
        old_data_dir: 插件包内的数据目录（插件根目录/data）。
        new_data_dir: AstrBot 插件数据目录（data/plugin_data/outluna）。
    """
    old_strategies = old_data_dir / "strategies"
    if not old_strategies.exists():
        return
    new_strategies = new_data_dir / "strategies"
    try:
        new_strategies.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning(f"创建策略目录 {new_strategies} 失败：{exc}")
        return
    for old_path in sorted(old_strategies.glob("*.json")):
        new_path = new_strategies / old_path.name
        try:
            if new_path.exists() and new_path.read_bytes() == old_path.read_bytes():
                continue
            shutil.copy2(old_path, new_path)
            logger.info(f"已迁移策略文件：{old_path} -> {new_path}")
        except OSError as exc:
            logger.warning(f"迁移策略文件 {old_path.name} 失败：{exc}")


def _migrate_kimi_credentials(old_data_dir: Path, new_data_dir: Path) -> None:
    """将 Kimi OAuth 凭证文件从旧数据目录迁移到新目录（兼容旧入口）。

    仅当新目录不存在凭证文件且旧目录存在时执行迁移。
    """
    old_cred = old_data_dir / "kimi_api_credentials.json"
    new_cred = new_data_dir / "kimi_api_credentials.json"
    if old_cred.exists() and not new_cred.exists():
        try:
            new_data_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old_cred, new_cred)
            logger.info(f"已迁移 Kimi 凭证：{old_cred} -> {new_cred}")
        except OSError as exc:
            logger.warning(f"迁移 Kimi 凭证失败：{exc}")
