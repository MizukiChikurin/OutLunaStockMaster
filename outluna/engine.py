"""核心引擎，整合策略、分析、报告能力。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from outluna.analysis.orchestrator import AnalysisOrchestrator
from outluna.backtest.engine import run_backtest
from outluna.config import settings
from outluna.data.gateway import DataGateway
from outluna.data.providers.kimi_provider import KimiAuthError
from outluna.data.watchlist import WatchlistStorage
from outluna.llm.base import LLMProvider
from outluna.report.generator import ReportGenerator
from outluna.strategy import registry
from outluna.strategy.scanner import StockScanner
from outluna.utils.logger import setup_logging
from outluna.utils.metrics import metrics
from outluna.utils.validation import InputValidator

logger = setup_logging()


class OutLunaEngine:
    """OutLuna 核心引擎。"""

    def __init__(self, llm_provider: LLMProvider | None = None):
        self.gateway = DataGateway()
        self.report_generator = ReportGenerator()
        self.llm_provider = llm_provider
        self.on_kimi_auth_error: Callable[[str], Awaitable[None]] | None = None
        self.watchlist_storage = WatchlistStorage(
            (settings.data_dir or settings.project_dir / "data") / "watchlist.json"
        )

    async def initialize(self) -> None:
        """初始化引擎。"""
        logger.info("OutLuna 引擎初始化完成")

    async def scan(
        self,
        strategy_name: str,
        universe: list[str] | None = None,
        max_candidates: int | None = None,
    ) -> str:
        """执行策略扫描并保存报告，返回报告文本。"""
        strategy_name = InputValidator.validate_strategy_name(strategy_name)
        max_candidates = InputValidator.validate_max_candidates(max_candidates)
        if universe is not None:
            universe = InputValidator.validate_symbols(universe)

        metric = metrics.start_operation("scan", strategy=strategy_name)
        try:
            strategy = registry.build(strategy_name)
            scanner = StockScanner(self.gateway, strategy)
            report = await scanner.scan(universe=universe, max_candidates=max_candidates)
            self.report_generator.save(report)
            metric.finish(success=True, matched=len(report.matches))
            logger.info(f"扫描完成：{strategy_name}，命中 {len(report.matches)} 只")
            return report.format_text()
        except Exception as exc:
            metric.finish(success=False, error=str(exc))
            logger.error(f"扫描失败：{exc}")
            raise

    async def select_stocks(self, requirements_text: str = "") -> str:
        """执行用户自定义选股流程并返回报告文本。

        该方法对应“选股要求以聊天形式输入”的场景。
        若未提供选股要求文本，则提示用户输入。
        """
        metric = metrics.start_operation("select_stocks")
        for attempt in range(2):
            try:
                if not requirements_text or not requirements_text.strip():
                    return (
                        "请提供选股要求。\n"
                        "用法：/选股 <选股要求文本>\n"
                        "例如：/选股 选择近5日涨幅不超过10%、RSI在40-70之间、站上MA5的股票"
                    )

                strategy = registry.build("用户自定义选股", params={
                    "requirements_text": requirements_text,
                    "llm_provider": self.llm_provider,
                    "on_auth_error": self.on_kimi_auth_error,
                })
                # 显式确保 llm_provider 传递到策略实例，避免依赖 _apply_params
                if self.llm_provider is not None and hasattr(strategy, "llm_provider"):
                    strategy.llm_provider = self.llm_provider
                    logger.debug(f"已将 llm_provider 注入策略：{type(self.llm_provider).__name__}")
                if self.on_kimi_auth_error is not None and hasattr(strategy, "on_auth_error"):
                    strategy.on_auth_error = self.on_kimi_auth_error
                    logger.debug("已将 on_auth_error 回调注入策略")

                scanner = StockScanner(self.gateway, strategy)
                report = await scanner.scan()
                txt_path = self.report_generator.save(report)
                formatted = report.format_text()
                return (
                    f"{formatted}\n\n"
                    f"---\n"
                    f"完整选股报告已保存至：{txt_path}"
                )
            except KimiAuthError as exc:
                if attempt == 0 and self.on_kimi_auth_error is not None:
                    logger.warning(f"Kimi 凭证过期，尝试刷新：{exc}")
                    await self.on_kimi_auth_error(str(exc))
                    await asyncio.sleep(8)
                    continue
                metric.finish(success=False, error=str(exc))
                logger.error(f"选股失败：{exc}")
                raise
        # 理论上不会执行到这里，保留类型安全
        return "选股失败：重试后仍无法完成选股"

    async def analyze(self, symbol: str, strategy_name: str = "") -> str:
        """分析指定股票并保存报告，返回报告文本。

        在 AstrBot 模式下，如果已配置 LLM Provider，则启用 LLM 综合研判，
        基于 Kimi Datasource 获取的原始数据生成结构化分析结论。
        """
        symbol = InputValidator.validate_symbol(symbol)
        if strategy_name:
            strategy_name = InputValidator.validate_strategy_name(strategy_name)

        metric = metrics.start_operation("analyze", symbol=symbol)
        try:
            use_llm = self.llm_provider is not None
            orchestrator = AnalysisOrchestrator(
                self.gateway,
                enable_llm=use_llm,
                llm_provider=self.llm_provider,
            )
            report = await orchestrator.analyze(symbol, strategy_name)
            self.report_generator.save(report)
            metric.finish(success=True, risk=report.risk_rating)
            for dim, result in report.results.items():
                signal_preview = result.signals[0] if result.signals else "无信号"
                logger.info(f"分析维度 {dim}：{signal_preview}")
            logger.info(f"分析完成：{symbol}")
            return self._format_analysis_text(report)
        except Exception as exc:
            metric.finish(success=False, error=str(exc))
            logger.error(f"分析失败：{exc}")
            raise

    async def backtest(
        self,
        strategy_name: str,
        days: int = 90,
        universe: list[str] | None = None,
    ) -> str:
        """执行策略回测并保存报告，返回报告文本。"""
        strategy_name = InputValidator.validate_strategy_name(strategy_name)
        days = InputValidator.validate_days(days)
        if universe is not None:
            universe = InputValidator.validate_symbols(universe)

        from datetime import datetime, timedelta

        metric = metrics.start_operation("backtest", strategy=strategy_name, days=days)
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days)
            report = run_backtest(
                strategy_name,
                start_date=start_date,
                end_date=end_date,
                universe=universe,
            )
            self.report_generator.save(report)
            metric.finish(success=True, return_pct=report.metrics.total_return)
            logger.info(f"回测完成：{strategy_name}，总收益 {report.metrics.total_return:.2%}")
            return self._format_backtest_text(report)
        except Exception as exc:
            metric.finish(success=False, error=str(exc))
            logger.error(f"回测失败：{exc}")
            raise

    async def list_strategies(self) -> str:
        """列出可用策略。"""
        strategies = registry.list_strategies()
        lines = ["可用策略：", ""]
        for idx, s in enumerate(strategies, 1):
            lines.append(f"{idx}. {s['name']}（v{s['version']}）")
            lines.append(f"   {s['description']}")
        return "\n".join(lines)

    async def get_report(self, report_id: str) -> str:
        """获取报告内容。"""
        report_id = InputValidator.validate_report_id(report_id)
        data = self.report_generator.load(report_id)
        if not data:
            return f"未找到报告：{report_id}"
        import json
        return json.dumps(data, ensure_ascii=False, indent=2)

    async def list_reports(self, report_type: str | None = None) -> str:
        """列出报告。"""
        reports = self.report_generator.list_reports(report_type)
        if not reports:
            return "暂无报告。"
        lines = ["报告列表：", ""]
        for r in reports[:20]:
            lines.append(
                f"- {r['report_id']} [{r['report_type']}] {r['title']} "
                f"({r['created_at']})"
            )
        return "\n".join(lines)

    async def compare_reports(self, id1: str, id2: str) -> str:
        """对比两份报告。"""
        id1 = InputValidator.validate_report_id(id1)
        id2 = InputValidator.validate_report_id(id2)
        result = self.report_generator.compare(id1, id2)
        import json
        return json.dumps(result, ensure_ascii=False, indent=2)

    async def add_watch(self, symbol: str) -> str:
        """将股票加入自选股池。

        Args:
            symbol: 股票代码，支持任意常用格式，会自动标准化。

        Returns:
            操作结果提示文本。
        """
        symbol = InputValidator.validate_symbol(symbol)
        added = self.watchlist_storage.add(symbol)
        normalized = self.watchlist_storage.list()
        if not added:
            return f"{symbol} 已在自选股池中。\n当前股池：{', '.join(normalized) if normalized else '空'}"
        return f"已将 {symbol} 加入自选股池。\n当前股池：{', '.join(normalized)}"

    async def remove_watch(self, symbol: str) -> str:
        """将股票从自选股池中移除。

        Args:
            symbol: 股票代码，支持任意常用格式，会自动标准化。

        Returns:
            操作结果提示文本。
        """
        symbol = InputValidator.validate_symbol(symbol)
        removed = self.watchlist_storage.remove(symbol)
        normalized = self.watchlist_storage.list()
        if not removed:
            return f"{symbol} 不在自选股池中。\n当前股池：{', '.join(normalized) if normalized else '空'}"
        return f"已将 {symbol} 从自选股池移除。\n当前股池：{', '.join(normalized) if normalized else '空'}"

    async def list_watch(self) -> str:
        """列出自选股池中的所有股票。"""
        symbols = self.watchlist_storage.list()
        info = self.watchlist_storage.info()
        if not symbols:
            return "自选股池为空。"
        lines = [
            f"自选股池（共 {len(symbols)} 只）：",
            ", ".join(symbols),
            f"更新时间：{info['updated_at']}",
        ]
        return "\n".join(lines)

    async def analyze_watchlist(self) -> str:
        """分析自选股池中的所有股票并保存结果到任务目录。

        使用数据网关（以 Kimi Datasource 为核心）对股池中的股票逐一进行
        基本面、企业画像、主力资金、情绪面等多维度分析，并将每个股票的
        详细报告以及汇总报告保存到 ``data/tasks/分析股池-YYYYMMDD`` 任务
        文件夹中，单只股票报告位于 ``分析股票：股票代码`` 子文件夹下。

        Returns:
            汇总分析结果文本，包含任务文件夹路径。
        """
        symbols = self.watchlist_storage.list()
        if not symbols:
            return "自选股池为空，请先使用 /观察 股票代码 添加股票。"

        metric = metrics.start_operation("analyze_watchlist", symbols=len(symbols))
        from datetime import datetime

        task_name = f"分析股池-{datetime.now().strftime('%Y.%m.%d')}"
        task_dir = (settings.data_dir or settings.project_dir / "data") / "tasks" / task_name
        task_dir.mkdir(parents=True, exist_ok=True)

        use_llm = self.llm_provider is not None
        reports: list[dict] = []
        failed_symbols: list[str] = []
        for symbol in symbols:
            try:
                orchestrator = AnalysisOrchestrator(
                    self.gateway,
                    enable_llm=use_llm,
                    llm_provider=self.llm_provider,
                )
                report = await orchestrator.analyze(symbol, strategy_name="")
                self.report_generator.save(report)
                # 将单只股票分析报告保存到股池任务文件夹的独立子目录中
                self._save_single_analysis_to_folder(task_dir, report)
                reports.append({"symbol": symbol, "report": report})
            except Exception as exc:
                logger.warning(f"自选股池分析失败：{symbol} - {exc}")
                failed_symbols.append(symbol)
                continue

        self._save_watchlist_reports(task_dir, reports, failed_symbols)
        metric.finish(success=True, analyzed=len(reports), failed=len(failed_symbols))
        summary = self._build_watchlist_summary(reports, failed_symbols, task_dir)
        return summary

    def _save_single_analysis_to_folder(
        self,
        task_dir: Path,
        report,
    ) -> None:
        """将单只股票分析报告保存到股池任务文件夹的独立子目录中。"""
        sub_folder = task_dir / f"分析股票：{report.symbol}"
        sub_folder.mkdir(parents=True, exist_ok=True)
        summary_path = sub_folder / "总结.md"
        detailed_path = sub_folder / "详细数据.md"
        summary_path.write_text(
            self._format_analysis_markdown(report), encoding="utf-8"
        )
        detailed_path.write_text(
            self._format_analysis_detailed_markdown(report), encoding="utf-8"
        )

    def _save_watchlist_reports(
        self,
        task_dir: Path,
        reports: list[dict],
        failed_symbols: list[str],
    ) -> None:
        """将自选股池分析结果写入任务文件夹。"""
        # 生成汇总报告
        summary_path = task_dir / "总结.md"
        summary_content = self._build_watchlist_summary(
            reports, failed_symbols, task_dir, include_path=False
        )
        summary_path.write_text(summary_content, encoding="utf-8")

    def _build_watchlist_summary(
        self,
        reports: list[dict],
        failed_symbols: list[str],
        task_dir: Path | None = None,
        include_path: bool = True,
    ) -> str:
        """构建自选股池分析汇总文本。"""
        lines = [
            "# 自选股池分析报告",
            "",
            f"- 分析股票数：{len(reports)} 只",
        ]
        if failed_symbols:
            lines.append(f"- 分析失败：{len(failed_symbols)} 只（{', '.join(failed_symbols)}）")
        lines.append("")

        if not reports:
            lines.append("暂无可展示的分析结果。")
            return "\n".join(lines)

        lines.append("## 各股票分析结果")
        lines.append("")
        for item in reports:
            report = item["report"]
            lines.append(f"### {report.symbol}")
            lines.append(f"- 风险等级：{report.risk_rating}")
            lines.append(f"- 投资建议：{report.recommendation}")
            lines.append("")

        if include_path and task_dir is not None:
            lines.append("---")
            lines.append(f"详细报告已保存至：{task_dir}")
        lines.append("")
        lines.append("> AI生成，不构成投资建议。股市有风险，投资需谨慎。")
        return "\n".join(lines)

    def _format_analysis_markdown(self, report) -> str:
        """将单只股票分析报告格式化为 Markdown 总结。"""
        lines = [
            f"# {report.symbol} 投资分析报告",
            "",
            f"- 报告ID：{report.report_id}",
            f"- 生成时间：{report.created_at.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- 风险等级：{report.risk_rating}",
            "",
        ]
        llm_result = report.results.get("llm")
        if llm_result is not None and llm_result.summary:
            lines.append(llm_result.summary)
        else:
            lines.append("## 投资建议")
            lines.append("")
            lines.append(report.recommendation)
            lines.append("")
            lines.append("## 各维度分析")
            lines.append("")
            for dim, result in report.results.items():
                lines.append(f"### {dim}")
                lines.append("")
                lines.append(result.summary)
                if result.signals:
                    lines.append("- " + "\n- ".join(result.signals))
                lines.append("")
        return "\n".join(lines)

    def _format_analysis_detailed_markdown(self, report) -> str:
        """将单只股票分析原始数据格式化为 Markdown 详细数据。"""
        lines = [
            f"# {report.symbol} 投资分析详细数据",
            "",
            f"- 报告ID：{report.report_id}",
            f"- 生成时间：{report.created_at.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- 风险等级：{report.risk_rating}",
            "",
        ]
        for dim, result in report.results.items():
            lines.append(f"## {dim}")
            lines.append("")
            lines.append(result.summary)
            lines.append("")
            if result.signals:
                lines.append("- " + "\n- ".join(result.signals))
                lines.append("")
            data = result.data or {}
            if data:
                lines.append("### 原始数据")
                lines.append("")
                for key, value in data.items():
                    if value is None:
                        continue
                    if hasattr(value, "empty") and value.empty:
                        continue
                    if isinstance(value, dict) and not value:
                        continue
                    if isinstance(value, list) and not value:
                        continue
                    lines.append(f"**{key}**:")
                    if hasattr(value, "head"):
                        lines.append(value.head(10).to_markdown(index=False))
                    elif isinstance(value, dict):
                        import json
                        lines.append(
                            "```json\n"
                            + json.dumps(value, ensure_ascii=False, indent=2, default=str)
                            + "\n```"
                        )
                    elif isinstance(value, list):
                        import json
                        lines.append(
                            "```json\n"
                            + json.dumps(value, ensure_ascii=False, indent=2, default=str)
                            + "\n```"
                        )
                    else:
                        lines.append(str(value))
                    lines.append("")
        return "\n".join(lines)

    def get_metrics(self) -> dict:
        """获取运行指标。"""
        summary = metrics.get_summary()
        summary["data_gateway_stats"] = self.gateway.get_call_stats()
        return summary

    def _format_analysis_text(self, report) -> str:
        """格式化分析报告为聊天文本。

        优先展示 LLM 生成的结构化结论（公司概况、股价表现、财务亮点、
        关键财务数据、估值参考、优势与风险、总体评价）。
        """
        lines = [
            f"股票：{report.symbol}",
            f"风险等级：{report.risk_rating}",
            "",
        ]

        llm_result = report.results.get("llm")
        if llm_result is not None and llm_result.summary:
            lines.append(llm_result.summary)
        else:
            lines.append("投资建议：")
            lines.append(report.recommendation)
            lines.append("")
            lines.append("各维度关键信号：")
            for dim, result in report.results.items():
                lines.append(f"【{dim}】")
                for signal in result.signals[:3]:
                    lines.append(f"  - {signal}")
                if not result.signals:
                    lines.append("  - 暂无明确信号")
                lines.append("")

        return "\n".join(lines)

    def _format_backtest_text(self, report) -> str:
        """格式化回测报告为聊天文本。"""
        lines = [
            f"策略：{report.strategy_name}",
            f"回测区间：{report.start_date.date()} ~ {report.end_date.date()}",
            f"初始资金：{report.initial_capital:,.2f}",
            "",
            "绩效指标：",
            f"- 总收益率：{report.metrics.total_return:.2%}",
            f"- 年化收益率：{report.metrics.annualized_return:.2%}",
            f"- 基准收益率（沪深300）：{report.metrics.benchmark_return:.2f}",
            f"- 超额收益（Alpha）：{report.metrics.alpha:.2f}",
            f"- 胜率：{report.metrics.win_rate:.2%}",
            f"- 盈亏比：{report.metrics.profit_factor:.2f}",
            f"- 最大回撤：{report.metrics.max_drawdown:.2%}",
            f"- 夏普比率：{report.metrics.sharpe_ratio:.2f}",
            f"- 总交易次数：{report.metrics.total_trades}",
            "",
            f"报告ID：{report.report_id}",
        ]
        return "\n".join(lines)
