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
    screening_code: str = Field(
        default="",
        description="可调用 akshare 接口的 Python 初筛代码，必须输出 result 变量为股票代码列表或 DataFrame",
    )
    min_recommend_score: float = Field(
        default=0.0, description="推荐最低分，0 表示不强制分数门槛，所有通过初筛的股票均可推荐"
    )
    min_watch_score: float = Field(
        default=60.0, description="观察股最低分",
    )
    max_analyze: int = Field(
        default=100, ge=0, le=100, description="最大分析股票数，0 表示分析股票池粗筛后的全部股票；默认 100，设置正数可限制数据源调用成本"
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

    AKSHARE_API_DOCS = """
screening_code 可使用的 akshare 接口（推荐）：
- ak.stock_zh_a_spot_em(): 全市场 A 股实时行情快照，返回 DataFrame，含列：代码、名称、最新价、涨跌幅、涨跌额、成交量、成交额、量比、换手率等。
- ak.stock_zh_a_hist(symbol="600519", period="daily", start_date="20260101", end_date="20261231", adjust="qfq"): 个股历史日线，返回 DataFrame，含：日期、开盘、收盘、最高、最低、成交量、成交额、振幅、涨跌幅等。
- ak.stock_zh_a_hist_min_em(symbol="600519", period="5", adjust="qfq"): 1/5/15/30/60 分钟 K 线。
- ak.stock_zh_a_minute(symbol="600519"): 当日分时数据。
- ak.stock_cyq_em(symbol="600519"): 个股筹码分布，返回价格-持仓比例。
- ak.stock_individual_fund_flow(symbol="600519", market="sh"): 个股主力资金流向。
- ak.stock_a_lg_indicator(): 龙虎榜数据。
- ak.stock_individual_info_em(symbol="600519"): 公司基本信息、财务指标。

通用要求：
1. screening_code 是粗筛，用于快速缩小股票池，控制后续数据源调用成本；
2. 可直接调用上述 akshare 接口，输入参数中的股票代码使用 akshare 格式（如 '600519'，无交易所后缀）；
3. 输出变量 result 必须是以下之一：
   - 股票代码列表，如 ['600519', '000001']；
   - 包含 '代码' 列的 pandas DataFrame；
   - 股票代码的 pandas Series。
4. 绝对禁止返回空列表、空 DataFrame 或 result = []；
5. 若无法使用 akshare 接口精确表达，可将相关条件留到 entry_rules/veto_rules/score_dimensions 中由后续数据源补充。
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
            "硬性入选（entry_rules）。不要生成评分维度（score_dimensions），"
            "即使原要求中包含“评分”“打分”“权重”等字样，也统一将其细化为可执行的硬性入选规则，"
            "由执行器直接判断通过/不通过，不再做量化评分；\n"
            "   注意：只有用户明确使用“一票否决”“剔除”“排除”等字样要求时才生成 veto_rules，"
            "否则必须将 veto_rules 留空，避免默认剔除股票；\n"
            "3. 只使用下方列出的可用字段和操作符；\n"
            "4. 若用户要求中某条件无法精确量化，请在 description 中说明，并给出最贴近的量化表达；\n"
            "5. max_analyze 不得超过 100，默认 100，表示分析股票池粗筛后按成交额排序取前 100 只；"
            "只有当用户明确要求全量分析或指定其他数量时才修改；\n"
            "6. 止损线/支撑位/密集成交区/散户止损等模糊概念，"
            "应使用 dist_to_20d_low_pct 或 dist_to_60d_low_pct 等字段量化："
            "例如 'dist_to_20d_low_pct between 0 and 3' 表示价格接近20日低点。\n"
            "7. 请根据用户要求总结一个简短 task_label（2-6个汉字），"
            "用于生成报告文件夹名，例如：散户止损、低位启动、量价齐升。\n"
            "8. 必须生成一段 screening_code（Python 代码），用于执行初筛并快速缩小股票池。\n"
            "   - screening_code 可直接调用 akshare 接口（见下方接口列表），\n"
            "     而不再限于实时快照字段；\n"
            "   - 它是粗筛，不要把所有条件都塞进来；主要用于快速缩小股票池，\n"
            "     控制后续数据源调用成本；\n"
            "   - 把用户要求中能用 akshare 接口快速表达的条件（如成交额、涨跌幅、\n"
            "     历史均线、资金流向、筹码分布等）放到 screening_code 中；\n"
            "   - 如果用户要求很复杂，screening_code 至少必须做以下基础过滤：\n"
            "     剔除无价/零成交、成交额大于一定阈值、排除极端涨跌幅、\n"
            "     按成交额排序取前 100 或前 200 只；\n"
            "   - 绝对禁止返回空列表、空 DataFrame 或 result = []；\n"
            "   - 只有确实无法使用 akshare 接口表达的条件才放到 entry_rules/veto_rules 中；\n"
            "   - 输出变量 result 必须是股票代码列表或包含 '代码' 列的 DataFrame。\n"
            "   - 示例（复杂要求时的最小化粗筛）：\n"
            "     df = ak.stock_zh_a_spot_em()\n"
            "     df = df[df['最新价'].notna() & (df['最新价'] > 0)]\n"
            "     df = df[df['成交额'].notna() & (df['成交额'] > 1e8)]\n"
            "     df = df[(df['涨跌幅'] >= -9) & (df['涨跌幅'] <= 9)]\n"
            "     df = df[(df['最新价'] >= 2) & (df['最新价'] <= 200)]\n"
            "     df = df.sort_values('成交额', ascending=False).head(100)\n"
            "     result = df['代码'].tolist()\n\n"
            f"{self.FIELD_DOCS}\n\n"
            f"{self.AKSHARE_API_DOCS}\n\n"
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
            max_analyze=output.max_analyze,
            required_data_sources=output.required_data_sources,
            original_requirements=requirements_text,
            task_label=output.task_label or UserRequirementParser._guess_task_label(requirements_text),
            screening_code=output.screening_code,
        )
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
