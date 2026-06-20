from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """OutLuna 全局配置。

    配置优先级：环境变量 > .env 文件 > 默认值。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="OUTLUNA_",
        extra="ignore",
        case_sensitive=False,
    )

    # 项目目录
    project_dir: Path = Field(default_factory=lambda: Path(__file__).parent.parent.parent)

    # 数据缓存
    cache_dir: Path = Field(default=Path("./data/cache"))
    cache_ttl_hours: int = Field(default=24)

    # 报告存储
    report_dir: Path = Field(default=Path("./data/reports"))
    db_path: Path = Field(default=Path("./data/outluna.db"))

    # 数据源配置
    prefer_kimi_datasource: bool = Field(default=True)
    kimi_datasource_home: Path = Field(default=Path("E:\\kimi-datasource"))
    akshare_enabled: bool = Field(default=True)
    yfinance_enabled: bool = Field(default=True)

    # LLM 配置（用于综合研判，可选）
    llm_provider: str = Field(default="openai")
    llm_model: str = Field(default="gpt-4o")
    llm_api_key: str = Field(default="")
    llm_base_url: str = Field(default="")

    # 回测默认参数
    default_initial_capital: float = Field(default=100000.0)
    default_position_limit: int = Field(default=5)
    default_hold_days: int = Field(default=5)
    default_stop_loss: float = Field(default=0.08)
    default_take_profit: float = Field(default=0.15)

    def model_post_init(self, __context: Any) -> None:
        """确保关键目录存在。"""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


# 全局配置实例
settings = Settings()
