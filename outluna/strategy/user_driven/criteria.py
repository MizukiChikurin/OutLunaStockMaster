"""用户自定义选股策略的配置模型。"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FilterRule:
    """单条筛选规则。"""

    field: str
    op: str
    value: Any
    description: str = ""

    def __post_init__(self):
        """规范化操作符。"""
        self.op = self.op.strip().lower()


@dataclass
class ScoreDimension:
    """评分维度。"""

    name: str
    weight: float
    rules: list[FilterRule] = field(default_factory=list)
    description: str = ""


@dataclass
class UserStrategyConfig:
    """用户选股策略结构化配置。

    由 LLM 根据用户自然语言要求解析生成，再由执行器严格执行。
    """

    name: str = "用户自定义策略"
    description: str = ""
    # 股票池粗筛：仅保留满足这些条件的股票进入后续分析
    pool_filters: list[FilterRule] = field(default_factory=list)
    # 一票否决：任一条件触发即剔除
    veto_rules: list[FilterRule] = field(default_factory=list)
    # 硬性入选：必须同时满足才可进入评分
    entry_rules: list[FilterRule] = field(default_factory=list)
    # 评分维度
    score_dimensions: list[ScoreDimension] = field(default_factory=list)
    # 推荐最低分
    min_recommend_score: float = 85.0
    # 观察股最低分
    min_watch_score: float = 60.0
    # 最大分析股票数（控制成本）
    max_analyze: int = 20
    # 用户原始要求文本
    original_requirements: str = ""
    # 任务标签，用于生成文件夹名：策略-日期
    task_label: str = ""
    # 数据源优先级：spot/tech/history/flow/news
    required_data_sources: list[str] = field(default_factory=lambda: ["spot", "tech"])

    @property
    def total_weight(self) -> float:
        """计算评分维度总权重。"""
        return sum(d.weight for d in self.score_dimensions)

    def normalize_weights(self) -> None:
        """将权重归一化为满分100。"""
        total = self.total_weight
        if total > 0 and total != 100:
            for dim in self.score_dimensions:
                dim.weight = dim.weight / total * 100
