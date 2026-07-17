"""自动化工作流配置存储。

按群聊（以 ``unified_msg_origin`` 为键）持久化自动化工作流配置，
配置文件为 ``<data_dir>/auto_workflow.json``。支持两种配置方式：
1. 聊天命令（``/自动化 ...``）实时修改并保存；
2. 手动编辑 JSON 文件，调度器每个检查周期重新读取，自动生效。

写入采用原子替换；保存前的读取为 fail-closed（文件损坏时备份并抛异常），
避免以空快照覆盖全部群配置。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from outluna.utils.json_io import read_json_strict, write_json_atomic
from outluna.utils.logger import setup_logging

logger = setup_logging()

#: 默认追踪股池推送时间点
DEFAULT_TRACK_TIMES = ["09:30", "13:30"]
#: 默认自动选股执行时间（封盘后）
DEFAULT_SCAN_TIME = "16:00"
#: 默认自动入库评分阈值
DEFAULT_SCORE_THRESHOLD = 70.0
#: 时间格式（HH:MM）合法值范围，由 is_valid_time 校验


def is_valid_time(text: str) -> bool:
    """校验 ``HH:MM`` 时间字符串是否合法。"""
    parts = text.split(":")
    if len(parts) != 2:
        return False
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    if len(parts[0]) != 2 or len(parts[1]) != 2:
        return False
    return 0 <= hour <= 23 and 0 <= minute <= 59


@dataclass
class GroupAutoConfig:
    """单个群聊的自动化工作流配置。"""

    enabled: bool = False
    """是否开启自动化工作流。"""
    track_times: list[str] = field(default_factory=lambda: list(DEFAULT_TRACK_TIMES))
    """追踪股池推送时间点列表（``HH:MM``）。"""
    scan_time: str = DEFAULT_SCAN_TIME
    """封盘后自动选股执行时间（``HH:MM``）。"""
    strategy: str = ""
    """自动选股使用的固定策略名称，空字符串表示未配置。"""
    score_threshold: float = DEFAULT_SCORE_THRESHOLD
    """自动入库的 LLM 评分阈值（大于等于该分数入库）。"""

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        return {
            "enabled": self.enabled,
            "track_times": list(self.track_times),
            "scan_time": self.scan_time,
            "strategy": self.strategy,
            "score_threshold": self.score_threshold,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GroupAutoConfig:
        """从字典反序列化，缺失字段使用默认值。"""
        config = cls()
        if not isinstance(data, dict):
            return config
        config.enabled = bool(data.get("enabled", False))
        track_times = data.get("track_times")
        if isinstance(track_times, list):
            # 显式配置（包括显式空列表，表示关闭盘中推送）优先于默认值
            config.track_times = [str(t) for t in track_times if is_valid_time(str(t))]
        scan_time = str(data.get("scan_time", "") or "")
        if is_valid_time(scan_time):
            config.scan_time = scan_time
        config.strategy = str(data.get("strategy", "") or "")
        try:
            threshold = float(data.get("score_threshold", DEFAULT_SCORE_THRESHOLD))
            if 0 <= threshold <= 100:
                config.score_threshold = threshold
        except (TypeError, ValueError):
            pass
        return config


class AutoWorkflowStorage:
    """自动化工作流配置存储（JSON 文件，按群聊键控）。"""

    def __init__(self, file_path: Path):
        """初始化存储，自动创建数据目录和文件。"""
        self.file_path = file_path
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_file()

    def _ensure_file(self) -> None:
        """确保 JSON 文件存在，不存在则写入默认空结构。"""
        if not self.file_path.exists():
            write_json_atomic(self.file_path, {"groups": {}})

    def _read(self, strict: bool = False) -> dict[str, Any]:
        """读取整个配置文件。

        Args:
            strict: 为 True 时文件损坏会备份并抛出异常（fail-closed），
                用于保存前置读取，防止以空快照覆盖全部群配置；
                为 False 时损坏仅告警并返回空结构，用于纯读场景。
        """
        try:
            data = read_json_strict(self.file_path)
        except Exception as exc:
            if strict:
                raise
            logger.warning(f"自动化配置读取失败，将使用空配置：{exc}")
            return {"groups": {}}
        groups = data.get("groups")
        if isinstance(groups, dict):
            return data
        return {"groups": {}}

    def get_group(self, umo: str) -> GroupAutoConfig:
        """获取指定群聊的配置，不存在时返回默认配置（不落盘）。"""
        data = self._read()
        raw = data["groups"].get(umo)
        if raw is None:
            return GroupAutoConfig()
        return GroupAutoConfig.from_dict(raw)

    def save_group(self, umo: str, config: GroupAutoConfig) -> None:
        """保存指定群聊的配置（读取失败时拒绝写入，防止覆盖其他群）。"""
        data = self._read(strict=True)
        data["groups"][umo] = config.to_dict()
        write_json_atomic(self.file_path, data)
        logger.info(f"自动化配置已保存：{umo} -> enabled={config.enabled}")

    def list_groups(self) -> dict[str, GroupAutoConfig]:
        """返回所有已配置群聊的字典（``umo -> 配置``）。"""
        data = self._read()
        result: dict[str, GroupAutoConfig] = {}
        for umo, raw in data["groups"].items():
            if isinstance(raw, dict):
                result[umo] = GroupAutoConfig.from_dict(raw)
        return result
