"""用户自定义选股策略入口。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, cast

from outluna.data.models import DataRequirement, ExecutionTrace, ScanReport, ScanResult
from outluna.llm.base import LLMProvider
from outluna.strategy.base import StrategyBase, registry
from outluna.strategy.user_driven.criteria import UserStrategyConfig
from outluna.strategy.user_driven.executor import StrategyExecutor
from outluna.strategy.user_driven.parser import UserRequirementParser


@registry.register
class UserDrivenStrategy(StrategyBase):
    """用户自定义选股策略。

    接收用户自然语言选股要求，使用 LLM 解析为结构化配置，
    再由执行器严格按配置执行选股。
    """

    name = "用户自定义选股"
    description = "根据用户提供的选股要求，自动解析并严格执行筛选。"
    version = "1.0"

    EXECUTE_TIMEOUT_SECONDS = 55

    def __init__(self, params: dict | None = None):
        self.requirements_text: str = ""
        self.config: UserStrategyConfig | None = None
        self.llm_provider: LLMProvider | None = None
        super().__init__(params)

    def _apply_params(self) -> None:
        """应用策略参数。"""
        super()._apply_params()
        if "requirements_text" in self.params:
            self.requirements_text = str(self.params["requirements_text"])
        if "llm_provider" in self.params:
            self.llm_provider = self.params["llm_provider"]

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

        # 安全边界：即使 LLM 输出较大值也强制限制
        self.config.max_analyze = max(1, min(self.config.max_analyze, 50))

        executor = StrategyExecutor(gateway, self.config)
        try:
            results = await asyncio.wait_for(
                asyncio.to_thread(executor.execute),
                timeout=self.EXECUTE_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            raise RuntimeError(
                f"选股执行超时（>{self.EXECUTE_TIMEOUT_SECONDS}秒），"
                "请缩小范围或稍后重试"
            ) from exc

        return {
            "results": results,
            "config": self.config,
            "execution_trace": executor.trace,
            "data_time": datetime.now().strftime("%Y-%m-%d 15:00:00"),
            "data_source": "akshare + stock_finance_data（按用户要求执行）",
        }

    async def evaluate_batch(self, data: dict[str, Any]) -> list[ScanResult]:
        """返回执行结果并暂存报告元数据。"""
        self._last_results = cast(list[ScanResult], data.get("results", []))
        self._last_data_time = data.get("data_time", "")
        self._last_data_source = data.get("data_source", "")
        self._last_execution_trace = data.get("execution_trace", ExecutionTrace())
        return self._last_results

    def build_scan_report(self, report_id: str) -> ScanReport:
        """构建完整选股报告。"""
        results = getattr(self, "_last_results", [])
        config = self.config
        if config is None:
            raise RuntimeError("策略未执行，无法生成报告")

        vetoed = [r for r in results if r.vetos]
        passed = [r for r in results if not r.vetos]
        passed_sorted = sorted(passed, key=lambda x: x.match_score, reverse=True)
        qualified = [r for r in passed_sorted if r.match_score >= config.min_recommend_score]
        watch_list = [
            r for r in passed_sorted
            if config.min_watch_score <= r.match_score < config.min_recommend_score
        ]

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
        """生成最终结论文本。"""
        if qualified:
            return (
                f"按用户要求筛选后，推荐 {len(qualified)} 只候选股，"
                f"评分均≥{config.min_recommend_score:.0f}分。"
            )
        lines = [
            "### 空仓/无符合条件标的\n",
            f"没有评分≥{config.min_recommend_score:.0f}分的股票可推荐。",
            "",
            "原因分析：",
            "1. 当前股票池中多数标的未通过用户设定的否决或入选条件；",
            "2. 技术指标或资金数据未满足用户要求的阈值；",
            "3. 缺乏符合用户选股标准的标的。",
            "",
            "交易建议：空仓观望，等待更好的入场时机。",
        ]
        if watch_list:
            names = "、".join(f"{r.name}（{r.symbol}）" for r in watch_list[:3])
            lines.append(f"可观察：{names}。")
        return "\n".join(lines)
