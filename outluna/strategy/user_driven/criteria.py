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
class UserStrategyConfig:
    """用户选股策略结构化配置。

    由 LLM 根据用户自然语言要求解析生成，再由执行器严格执行。
    选股只做股票池粗筛、一票否决和硬性入选，不生成量化评分，
    将原始数据与规则结果统一交给 LLM 处理。
    """

    name: str = "用户自定义策略"
    description: str = ""
    # 股票池粗筛：仅保留满足这些条件的股票进入后续分析
    pool_filters: list[FilterRule] = field(default_factory=list)
    # 一票否决：任一条件触发即剔除
    veto_rules: list[FilterRule] = field(default_factory=list)
    # 硬性入选：必须同时满足才可进入候选
    entry_rules: list[FilterRule] = field(default_factory=list)
    # 最大分析股票数：0 表示分析股票池粗筛后的全部股票；正数表示按成交额排序后取前 N 只
    max_analyze: int = 100
    # 用户原始要求文本
    original_requirements: str = ""
    # 任务标签，用于生成文件夹名：策略-日期
    task_label: str = ""
    # 数据源优先级：spot/tech/history/flow/news
    required_data_sources: list[str] = field(default_factory=lambda: ["spot", "tech"])
    # LLM 生成的快照初筛 Python 代码（仅使用 akshare 快照字段）
    screening_code: str = ""
