"""用户自定义选股策略入口。

接收用户自然语言选股要求，使用 LLM 解析为结构化配置，
再由执行器严格按配置执行选股。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, cast

from pydantic import BaseModel, Field

from outluna.data.models import DataRequirement, ExecutionTrace, ScanReport, ScanResult
from outluna.data.providers.kimi_provider import KimiAuthError
from outluna.llm.base import LLMProvider
from outluna.strategy.base import StrategyBase, registry
from outluna.strategy.user_driven.criteria import UserStrategyConfig
from outluna.strategy.user_driven.executor import StrategyExecutor
from outluna.strategy.user_driven.parser import UserRequirementParser
from outluna.utils.logger import setup_logging

logger = setup_logging()


class StockReasonOutput(BaseModel):
    """LLM 为单只股票生成的推荐理由。"""

    reasons: list[str] = Field(
        default_factory=list,
        description="推荐理由列表，每条理由应结合技术指标说明为什么符合用户要求",
    )


@registry.register
class UserDrivenStrategy(StrategyBase):
    """用户自定义选股策略。

    接收用户自然语言选股要求，使用 LLM 解析为结构化配置，
    再由执行器严格按配置执行选股。
    """

    name = "用户自定义选股"
    description = "根据用户提供的选股要求，自动解析并严格执行筛选。"
    version = "1.0"

    BASE_TIMEOUT_SECONDS = 60
    MAX_TIMEOUT_SECONDS = 3600

    AUTH_REFRESH_WAIT_SECONDS = 8

    def __init__(self, params: dict | None = None):
        self.requirements_text: str = ""
        self.config: UserStrategyConfig | None = None
        self.llm_provider: LLMProvider | None = None
        self.on_auth_error: Callable[[str], Awaitable[None]] | None = None
        super().__init__(params)

    def _apply_params(self) -> None:
        """应用策略参数。"""
        super()._apply_params()
        if "requirements_text" in self.params:
            self.requirements_text = str(self.params["requirements_text"])
        if "llm_provider" in self.params:
            self.llm_provider = self.params["llm_provider"]
        if "on_auth_error" in self.params:
            self.on_auth_error = self.params["on_auth_error"]

    def _compute_timeout(self, max_analyze: int) -> int:
        """根据分析范围动态计算执行超时。

        全量分析（max_analyze <= 0）给最大超时；按数量分析则按每只股票约 3 秒估算。
        """
        if max_analyze <= 0:
            return self.MAX_TIMEOUT_SECONDS
        estimated = max_analyze * 3 + 30
        return max(self.BASE_TIMEOUT_SECONDS, min(self.MAX_TIMEOUT_SECONDS, estimated))

    def match(self, symbol: str, df) -> bool:
        """本策略不走传统单只匹配流程。"""
        return False

    @property
    def required_data(self) -> DataRequirement:
        """本策略按需获取数据。"""
        return DataRequirement(period="1d", bars=5)

    async def prepare_data(self, gateway) -> dict[str, Any] | None:
        """准备数据：解析用户要求并执行选股。"""
        if not self.requirements_text:
            raise RuntimeError("未提供选股要求文本，请使用 /选股 <选股要求>")

        parser = UserRequirementParser(self.llm_provider)
        self.config = await parser.parse(self.requirements_text)

        # 安全边界：正数时强制限制不超过 100；0 表示不限制，保持为 0
        if self.config.max_analyze > 0:
            self.config.max_analyze = max(1, min(self.config.max_analyze, 100))

        execute_timeout = self._compute_timeout(self.config.max_analyze)

        for attempt in range(2):
            executor = StrategyExecutor(gateway, self.config)
            try:
                results = await asyncio.wait_for(
                    asyncio.to_thread(executor.execute),
                    timeout=execute_timeout,
                )
                return {
                    "results": results,
                    "config": self.config,
                    "execution_trace": executor.trace,
                    "data_time": datetime.now().strftime("%Y-%m-%d 15:00:00"),
                    "data_source": "akshare + stock_finance_data（按用户要求执行）",
                }
            except KimiAuthError as exc:
                if attempt == 0 and self.on_auth_error is not None:
                    logger = __import__("outluna.utils.logger", fromlist=["setup_logging"]).setup_logging()
                    logger.warning(f"Kimi 凭证过期，尝试刷新（第 {attempt + 1} 次）：{exc}")
                    await self.on_auth_error(str(exc))
                    await asyncio.sleep(self.AUTH_REFRESH_WAIT_SECONDS)
                    continue
                raise RuntimeError(
                    f"Kimi 凭证刷新后仍然无效：{exc}。请检查 /kimi login 是否成功。"
                ) from exc
            except TimeoutError as exc:
                raise RuntimeError(
                    f"选股执行超时（>{execute_timeout}秒），"
                    "请缩小范围或稍后重试"
                ) from exc

        # 循环正常结束仅可能为第二次 KimiAuthError 已抛出，此处兜底防止 mypy 警告
        raise RuntimeError("选股执行异常：Kimi 凭证刷新失败")

    async def evaluate_batch(self, data: dict[str, Any]) -> list[ScanResult]:
        """返回执行结果并暂存报告元数据。

        选股执行器仅做股票池粗筛、一票否决和硬性入选，不生成量化评分。
        若配置了 LLM，则为通过初筛的候选股票生成推荐理由，供最终报告使用。
        """
        results = cast(list[ScanResult], data.get("results", []))

        if self.llm_provider is not None:
            await self._generate_reasons(results)

        self._last_results = results
        self._last_data_time = data.get("data_time", "")
        self._last_data_source = data.get("data_source", "")
        self._last_execution_trace = data.get("execution_trace", ExecutionTrace())
        return self._last_results

    async def _generate_reasons(
        self,
        results: list[ScanResult],
    ) -> None:
        """为通过初筛的候选股票生成推荐理由。"""
        passed = [r for r in results if not r.vetos]
        if not passed:
            return

        target_stocks = passed[:30]
        tasks = [self._generate_reason_for_stock(r) for r in target_stocks]
        await asyncio.gather(*tasks)

    async def _generate_reason_for_stock(
        self,
        result: ScanResult,
    ) -> None:
        """使用 LLM 为单只股票生成推荐理由。"""
        if self.llm_provider is None:
            return
        try:
            system_prompt = self._build_reason_system_prompt()
            user_prompt = self._build_reason_user_prompt(result)
            output = cast(
                StockReasonOutput,
                await self.llm_provider.generate_structured(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    response_format=StockReasonOutput,
                    temperature=0.3,
                ),
            )
            result.notes = output.reasons or result.notes
        except Exception as exc:
            logger.warning(f"为 {result.symbol} 生成推荐理由失败：{exc}")

    def _build_reason_system_prompt(self) -> str:
        """构建生成推荐理由的系统提示词。"""
        return (
            "你是一位资深量化分析师。请根据用户选股要求以及股票的技术指标数据，"
            "为该股给出 3-5 条简洁的推荐理由。\n\n"
            "推荐理由要求：\n"
            "1. 每条理由必须结合具体技术指标（如均线、RSI、MACD、资金流向、筹码分布等）说明；\n"
            "2. 理由应直接回应用户选股要求，避免空泛描述；\n"
            "3. 若某些指标不支持用户要求，也应在理由中简要说明风险。\n\n"
            "输出必须是合法 JSON，包含 reasons（字符串数组）。"
        )

    def _build_reason_user_prompt(
        self,
        result: ScanResult,
    ) -> str:
        """构建生成推荐理由的用户提示词。"""
        metrics = dict(result.trigger_data or {})
        metrics.update(result.score_details or {})
        metrics_text = "\n".join(f"- {k}: {v}" for k, v in metrics.items() if v is not None)

        return (
            f"用户选股要求：{self.config.original_requirements if self.config else ''}\n\n"
            f"股票代码：{result.symbol}\n"
            f"股票名称：{result.name}\n"
            f"最新价：{result.price}\n"
            f"当日涨跌幅：{result.change_pct}%\n"
            f"当日成交额（亿）：{result.turnover:.2f}\n\n"
            f"主要技术指标：\n{metrics_text}\n\n"
            "请给出推荐理由。"
        )

    def build_scan_report(self, report_id: str) -> ScanReport:
        """构建完整选股报告。"""
        results = getattr(self, "_last_results", [])
        config = self.config
        if config is None:
            raise RuntimeError("策略未执行，无法生成报告")

        vetoed = [r for r in results if r.vetos]
        passed = [r for r in results if not r.vetos]
        # 选股仅做通过性判断，不做量化评分，所有通过初筛的股票均作为候选。
        qualified = passed
        watch_list: list[ScanResult] = []

        task_folder = self._build_task_folder(config)

        return ScanReport(
            report_id=report_id,
            strategy_name=config.name,
            strategy_params={"requirements": config.original_requirements[:500]},
            created_at=datetime.now(),
            matches=qualified,
            total_scanned=len(results),
            data_time=getattr(self, "_last_data_time", ""),
            data_source=getattr(self, "_last_data_source", ""),
            market_summary=self._build_market_summary(results),
            vetoed=vetoed,
            watch_list=watch_list,
            qualified=qualified,
            final_conclusion=self._build_final_conclusion(qualified, watch_list, config),
            execution_trace=getattr(self, "_last_execution_trace", ExecutionTrace()),
            task_folder=task_folder,
        )

    def _build_task_folder(self, config: UserStrategyConfig) -> str:
        """生成任务文件夹名：策略标签-日期。"""
        label = config.task_label or "用户选股"
        date_str = datetime.now().strftime("%Y.%m.%d")
        return f"{label}-{date_str}"

    def _build_market_summary(self, results: list[ScanResult]) -> str:
        """生成市场环境概述。"""
        if not results:
            return "无数据"
        total = len(results)
        vetoed = [r for r in results if r.vetos]
        down_count = sum(1 for r in results if (r.change_pct or 0) < -2)
        up_count = sum(1 for r in results if (r.change_pct or 0) > 2)

        reason_counts: dict[str, int] = {}
        for r in vetoed:
            for reason in r.vetos:
                key = reason.split("：")[0].split("=")[0].strip()
                reason_counts[key] = reason_counts.get(key, 0) + 1

        lines = [
            f"本次按用户要求共分析 {total} 只股票，",
            f"其中下跌超过2%的有 {down_count} 只，上涨超过2%的有 {up_count} 只。",
        ]
        if reason_counts:
            top_reasons = sorted(reason_counts.items(), key=lambda x: x[1], reverse=True)[:3]
            lines.append("主要否决原因：" + "、".join(f"{k}（{v}只）" for k, v in top_reasons) + "。")
        return "".join(lines)

    def _build_final_conclusion(
        self,
        qualified: list[ScanResult],
        watch_list: list[ScanResult],
        config: UserStrategyConfig,
    ) -> str:
        """生成最终结论文本。

        只要存在通过初筛的股票，就作为候选推荐，不再做量化评分。
        """
        if not qualified:
            return (
                "无通过初筛的股票，本次不推荐标的。\n"
                "建议：放宽选股条件或等待市场环境改善。"
            )

        lines = [
            f"按用户要求筛选后，共推荐 {len(qualified)} 只候选股：",
            "",
        ]
        for i, r in enumerate(qualified[:10], 1):
            lines.append(f"{i}. {r.name}（{r.symbol}）")
            if r.notes:
                lines.append(f"   推荐理由：{r.notes[0]}")
        if len(qualified) > 10:
            lines.append(f"...（共 {len(qualified)} 只，此处仅展示前 10）")
        if watch_list:
            lines.append("")
            names = "、".join(f"{r.name}（{r.symbol}）" for r in watch_list[:3])
            lines.append(f"可观察：{names}。")
        return "\n".join(lines)
