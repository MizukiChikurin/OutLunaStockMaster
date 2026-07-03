"""用户选股要求解析器。

使用 LLM 将自然语言选股要求解析为结构化的 ``UserStrategyConfig``，
确保后续执行器能够严格、可复现地执行筛选。
"""

from __future__ import annotations

import json
from typing import Any, cast

from pydantic import BaseModel, Field

from outluna.llm.base import LLMProvider
from outluna.llm.openai_provider import OpenAILLMProvider
from outluna.strategy.user_driven.criteria import (
    FilterRule,
    ScoreDimension,
    UserStrategyConfig,
)
from outluna.utils.logger import setup_logging

logger = setup_logging()


class FilterRuleOutput(BaseModel):
    """LLM 输出的单条筛选规则。"""

    field: str = Field(description="指标字段名，必须是预定义字段之一")
    op: str = Field(description="操作符：>、>=、<、<=、==、!=、between、not_between、in、not_in")
    value: Any = Field(description="阈值，between/not_between 使用 [min, max] 列表")
    description: str = Field(default="", description="规则说明")


class ScoreDimensionOutput(BaseModel):
    """LLM 输出的评分维度。"""

    name: str = Field(description="维度名称，如位置安全度、量价结构")
    weight: float = Field(description="权重，所有维度权重之和应为100")
    rules: list[FilterRuleOutput] = Field(default_factory=list, description="该维度的评分规则")
    description: str = Field(default="", description="维度说明")


class UserStrategyConfigOutput(BaseModel):
    """LLM 输出的策略配置。"""

    name: str = Field(default="用户自定义策略", description="策略名称")
    description: str = Field(default="", description="策略一句话说明")
    pool_filters: list[FilterRuleOutput] = Field(
        default_factory=list,
        description="股票池粗筛条件，减少后续分析范围",
    )
    veto_rules: list[FilterRuleOutput] = Field(
        default_factory=list,
        description="一票否决条件，任一触发即剔除",
    )
    entry_rules: list[FilterRuleOutput] = Field(
        default_factory=list,
        description="硬性入选条件，必须全部满足",
    )
    score_dimensions: list[ScoreDimensionOutput] = Field(
        default_factory=list,
        description="评分维度与权重",
    )
    min_recommend_score: float = Field(
        default=85.0, description="推荐最低分",
    )
    min_watch_score: float = Field(
        default=60.0, description="观察股最低分",
    )
    max_analyze: int = Field(
        default=20, ge=1, le=50, description="最大分析股票数，控制数据源调用成本，不得超过50",
    )
    task_label: str = Field(
        default="", description="任务标签，用于生成文件夹名，例如：散户止损、低位启动",
    )
    required_data_sources: list[str] = Field(
        default_factory=lambda: ["spot", "tech"],
        description="需要用到的数据源：spot/tech/history/flow/news",
    )


class UserRequirementParser:
    """用户选股要求解析器。"""

    # 预定义可用字段说明，供 LLM 参考
    FIELD_DOCS = """
可用字段（field）说明：
- symbol: 股票代码（内部格式，如 600519.SH）
- name: 股票名称
- price: 最新价
- change_pct: 当日涨跌幅（%）
- open: 开盘价
- high: 最高价
- low: 最低价
- turnover: 当日成交额（元）
- volume: 当日成交量（股）
- turnover_5d_avg: 5日平均成交额（元）
- turnover_20d_avg: 20日平均成交额（元）
- change_pct_5d: 近5日累计涨跌幅（%）
- change_pct_10d: 近10日累计涨跌幅（%）
- change_pct_20d: 近20日累计涨跌幅（%）
- price_position_60d: 当前价格处于近60日高低点区间的位置（0-1）
- price_position_20d: 当前价格处于近20日高低点区间的位置（0-1）
- dist_to_20d_low_pct: 当前价相对近20日最低价的涨幅百分比，正值表示高于最低价，值越小越接近近期支撑位
- dist_to_20d_high_pct: 当前价相对近20日最高价的回撤百分比，正值且越小表示越接近前期高点
- dist_to_60d_low_pct: 当前价相对近60日最低价的涨幅百分比
- dist_to_60d_high_pct: 当前价相对近60日最高价的回撤百分比
- ma5/ma10/ma20/ma60: 均线价格
- rsi6/rsi12/rsi14/rsi24: RSI 指标
- k/d/j: KDJ 指标
- macd/dif/dea: MACD 指标
- atr14: ATR(14)
- main_inflow_3d: 近3日主力净流入天数
- main_inflow_5d: 近5日主力净流入天数
- market_cap: 流通市值（元）

操作符（op）说明：
- >、>=、<、<=、==、!=：数值比较
- between：在 [min, max] 区间（包含边界）
- not_between：不在 [min, max] 区间
- in：在列表中
- not_in：不在列表中
"""

    def __init__(self, llm_provider: LLMProvider | None = None):
        self.llm_provider = llm_provider or OpenAILLMProvider()

    async def parse(self, requirements_text: str) -> UserStrategyConfig:
        """解析用户选股要求文本为结构化配置。"""
        if not self.llm_provider.available:
            raise RuntimeError(
                "未配置可用的 LLM Provider，无法解析用户选股要求。"
                "在 AstrBot 中使用时，请确保 AstrBot 已配置 LLM；"
                "在 CLI 中使用时，请在 .env 中设置 OUTLUNA_LLM_API_KEY。"
            )

        output = await self._call_llm(requirements_text)
        return UserRequirementParser._to_config(output, requirements_text)

    async def _call_llm(self, requirements_text: str) -> UserStrategyConfigOutput:
        """调用 LLM 解析。"""
        system_prompt = self._build_system_prompt()
        try:
            return cast(
                UserStrategyConfigOutput,
                await self.llm_provider.generate_structured(
                    system_prompt=system_prompt,
                    user_prompt=requirements_text,
                    response_format=UserStrategyConfigOutput,
                    temperature=0.2,
                ),
            )
        except Exception as exc:
            logger.error(f"解析用户选股要求失败：{exc}")
            raise RuntimeError(f"解析用户选股要求失败：{exc}") from exc


    def _build_system_prompt(self) -> str:
        """构建系统提示词。"""
        return (
            "你是一位量化选股策略工程师。你的任务是将用户用自然语言描述的选股要求，"
            "严格、准确地解析为结构化的选股配置。\n\n"
            "要求：\n"
            "1. 忠实还原用户要求，不添加用户未提及的规则；\n"
            "2. 将条件分类为：股票池粗筛（pool_filters）、一票否决（veto_rules）、"
            "硬性入选（entry_rules）、评分维度（score_dimensions）；\n"
            "3. 评分维度权重之和应为 100；\n"
            "4. 只使用下方列出的可用字段和操作符；\n"
            "5. 若用户要求中某条件无法精确量化，请在 description 中说明，并给出最贴近的量化表达；\n"
            "6. max_analyze 不得超过 50，建议默认 20，用于控制数据源调用成本和响应时间；\n"
            "7. 当用户未明确给出评分维度与细则时，score_dimensions 应设为空数组，"
            "不要生成空规则的评分维度，避免所有股票得分一致；\n"
            "8. 止损线/支撑位/密集成交区/散户止损等模糊概念，"
            "应使用 dist_to_20d_low_pct 或 dist_to_60d_low_pct 等字段量化："
            "例如 'dist_to_20d_low_pct between 0 and 3' 表示价格接近20日低点。\n"
            "9. 请根据用户要求总结一个简短 task_label（2-6个汉字），"
            "用于生成报告文件夹名，例如：散户止损、低位启动、量价齐升。\n\n"
            f"{self.FIELD_DOCS}\n\n"
            "注意：输出必须是合法 JSON，字段类型严格匹配。"
        )

    @staticmethod
    def _to_config(
        output: UserStrategyConfigOutput,
        requirements_text: str,
    ) -> UserStrategyConfig:
        """将 LLM 输出转换为内部配置对象。"""
        config = UserStrategyConfig(
            name=output.name,
            description=output.description,
            pool_filters=[UserRequirementParser._to_filter_rule(r) for r in output.pool_filters],
            veto_rules=[UserRequirementParser._to_filter_rule(r) for r in output.veto_rules],
            entry_rules=[UserRequirementParser._to_filter_rule(r) for r in output.entry_rules],
            score_dimensions=[UserRequirementParser._to_score_dimension(d) for d in output.score_dimensions],
            min_recommend_score=output.min_recommend_score,
            min_watch_score=output.min_watch_score,
            max_analyze=output.max_analyze,
            required_data_sources=output.required_data_sources,
            original_requirements=requirements_text,
            task_label=output.task_label or UserRequirementParser._guess_task_label(requirements_text),
        )
        config.normalize_weights()
        return config

    @staticmethod
    def _to_filter_rule(rule: FilterRuleOutput) -> FilterRule:
        """转换规则对象。"""
        return FilterRule(
            field=rule.field,
            op=rule.op,
            value=rule.value,
            description=rule.description,
        )

    @staticmethod
    def _to_score_dimension(dim: ScoreDimensionOutput) -> ScoreDimension:
        """转换评分维度对象。"""
        return ScoreDimension(
            name=dim.name,
            weight=dim.weight,
            rules=[UserRequirementParser._to_filter_rule(r) for r in dim.rules],
            description=dim.description,
        )

    @staticmethod
    def parse_json(json_text: str) -> UserStrategyConfig:
        """从 JSON 文本直接解析配置（用于测试或本地配置）。"""
        data = json.loads(json_text)
        output = UserStrategyConfigOutput(**data)
        return UserRequirementParser._to_config(output, data.get("original_requirements", ""))

    @staticmethod
    def _guess_task_label(requirements_text: str) -> str:
        """当 LLM 未返回任务标签时，从要求文本中猜测一个简短标签。"""
        text = requirements_text.strip()
        if not text:
            return "用户选股"
        # 简单关键词匹配
        keywords = {
            "散户止损": ["散户止损", "止损线", "支撑位", "stop loss", "止损"],
            "低位启动": ["低位", "启动", "底部", "反弹"],
            "量价齐升": ["量", "价", "放量", "缩量"],
            "突破": ["突破", "新高"],
            "超跌": ["超跌", "反弹", "止跌"],
            "金叉": ["金叉", "MACD", "KDJ"],
            "RSI": ["RSI"],
        }
        for label, words in keywords.items():
            for word in words:
                if word in text:
                    return label
        return "用户选股"
