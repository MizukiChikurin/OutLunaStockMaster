"""固定策略选股入口。

使用预设的选股策略文件（选股策略_策略名称.json）执行选股，
粗筛代码与 LLM 提示词均来自策略文件，不再由 LLM 实时生成。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from outluna.config import settings
from outluna.strategy.base import registry
from outluna.strategy.user_driven import UserDrivenStrategy
from outluna.strategy.user_driven.criteria import FilterRule, UserStrategyConfig
from outluna.utils.logger import setup_logging

logger = setup_logging()


class FixedStrategyConfigError(RuntimeError):
    """固定策略配置异常。"""


@registry.register
class FixedStrategy(UserDrivenStrategy):
    """固定策略选股策略。

    通过读取策略文件中的 screening_code 和 prompt 执行选股，
    其余流程与用户自定义选股保持一致。
    """

    name = "固定策略选股"
    description = "根据预设策略文件执行选股，粗筛代码和 LLM 提示词均来自策略文件。"
    version = "1.0"

    def __init__(self, params: dict | None = None):
        """初始化固定策略参数。"""
        self.preset_name: str = ""
        self.strategy_file: Path | None = None
        self.preset_prompt: str = ""
        super().__init__(params)

    def _apply_params(self) -> None:
        """应用策略参数，包括策略名称和文件路径。"""
        super()._apply_params()
        if "preset_name" in self.params:
            self.preset_name = str(self.params["preset_name"])
        if "strategy_file" in self.params:
            self.strategy_file = Path(self.params["strategy_file"])
        if "prompt" in self.params:
            self.preset_prompt = str(self.params["prompt"])

    @staticmethod
    def strategy_dir() -> Path:
        """返回固定策略文件存放目录。"""
        return (settings.data_dir or settings.project_dir / "data") / "strategies"

    @classmethod
    def list_presets(cls) -> list[str]:
        """列出所有可用的固定策略名称（从文件名中解析）。"""
        directory = cls.strategy_dir()
        if not directory.exists():
            return []
        names = []
        for file in directory.glob("选股策略_*.json"):
            name = file.stem[len("选股策略_") :]
            if name:
                names.append(name)
        return sorted(names)

    def _resolve_file(self) -> Path:
        """根据 preset_name 或 strategy_file 定位策略文件。"""
        if self.strategy_file is not None:
            return self.strategy_file
        if not self.preset_name:
            raise FixedStrategyConfigError("未提供固定策略名称，请使用 /固定策略 <策略名称>")
        directory = self.strategy_dir()
        directory.mkdir(parents=True, exist_ok=True)
        exact = directory / f"选股策略_{self.preset_name}.json"
        if exact.exists():
            return exact
        for file in directory.glob("选股策略_*.json"):
            if file.stem == f"选股策略_{self.preset_name}" or file.stem == self.preset_name:
                return file
        raise FixedStrategyConfigError(
            f"未找到固定策略文件：{exact}。可用策略：{', '.join(self.list_presets()) or '无'}"
        )

    def _load_config(self) -> UserStrategyConfig:
        """加载策略文件并转换为内部配置。"""
        path = self._resolve_file()
        logger.info(f"加载固定策略文件：{path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise FixedStrategyConfigError(f"读取策略文件失败：{exc}") from exc
        self.preset_prompt = str(data.get("prompt", ""))
        return self._to_config(data)

    @staticmethod
    def _to_config(data: dict[str, Any]) -> UserStrategyConfig:
        """将策略文件字典转换为 UserStrategyConfig。"""
        screening_code = data.get("screening_code", "")
        task_label = data.get("task_label") or data.get("name", "固定策略")
        return UserStrategyConfig(
            name=data.get("name", "固定策略"),
            description=data.get("description", ""),
            task_label=task_label,
            max_analyze=int(data.get("max_analyze", 100)),
            required_data_sources=list(data.get("required_data_sources", ["spot", "tech"])),
            original_requirements=data.get("description", ""),
            screening_code=screening_code,
            pool_filters=[FixedStrategy._to_filter_rule(r) for r in data.get("pool_filters", [])],
            veto_rules=[FixedStrategy._to_filter_rule(r) for r in data.get("veto_rules", [])],
            entry_rules=[FixedStrategy._to_filter_rule(r) for r in data.get("entry_rules", [])],
        )

    @staticmethod
    def _to_filter_rule(data: dict[str, Any]) -> FilterRule:
        """将字典转换为 FilterRule。"""
        return FilterRule(
            field=data["field"],
            op=data["op"],
            value=data["value"],
            description=data.get("description", ""),
        )

    async def prepare_data(self, gateway) -> dict[str, Any] | None:
        """加载固定策略配置并执行选股。"""
        self.config = self._load_config()
        if not self.preset_prompt:
            self.preset_prompt = self.config.description
        return await self._execute_config(gateway)

    def _build_reason_system_prompt(self) -> str:
        """使用策略文件中的预设提示词进行 LLM 细筛。"""
        if self.preset_prompt:
            return self.preset_prompt
        return super()._build_reason_system_prompt()
