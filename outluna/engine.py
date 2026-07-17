"""核心引擎，整合策略、分析、报告能力。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from outluna.analysis.orchestrator import AnalysisOrchestrator
from outluna.config import settings
from outluna.data.gateway import DataGateway
from outluna.data.providers.kimi_provider import KimiAuthError
from outluna.data.watchlist import SOURCE_AUTO, SOURCE_MANUAL, WatchlistStorage
from outluna.llm.base import LLMProvider
from outluna.report.generator import ReportGenerator
from outluna.strategy import registry
from outluna.strategy.fixed import FixedStrategy
from outluna.strategy.scanner import StockScanner
from outluna.utils.logger import setup_logging
from outluna.utils.metrics import metrics
from outluna.utils.symbol import SymbolNormalizer
from outluna.utils.validation import InputValidator

logger = setup_logging()


@dataclass
class ReportOutput:
    """引擎命令输出结构：同时包含展示文本与结构化数据。"""

    text: str
    data: Any | None = None


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

    async def select_stocks(self, requirements_text: str = "") -> ReportOutput:
        """执行用户自定义选股流程并返回报告文本与结构化数据。

        该方法对应“选股要求以聊天形式输入”的场景。
        若未提供选股要求文本，则提示用户输入。

        Returns:
            ReportOutput: ``text`` 为发送给用户的文本，``data`` 为 ``ScanReport`` 对象，
            供 AstrBot 入口渲染图片表格使用。
        """
        metric = metrics.start_operation("select_stocks")
        for attempt in range(2):
            try:
                if not requirements_text or not requirements_text.strip():
                    return ReportOutput(
                        text=(
                            "请提供选股要求。\n"
                            "用法：/选股 <选股要求文本>\n"
                            "例如：/选股 选择近5日涨幅不超过10%、RSI在40-70之间、站上MA5的股票"
                        )
                    )

                strategy = registry.build(
                    "用户自定义选股",
                    params={
                        "requirements_text": requirements_text,
                        "llm_provider": self.llm_provider,
                        "on_auth_error": self.on_kimi_auth_error,
                    },
                )
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
                return ReportOutput(
                    text=(f"{formatted}\n\n---\n完整选股报告已保存至：{txt_path}"),
                    data=report,
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
        return ReportOutput(text="选股失败：重试后仍无法完成选股")

    async def select_stocks_by_preset(self, preset_name: str = "") -> ReportOutput:
        """执行固定策略选股流程并返回报告文本与结构化数据。

        该方法对应“使用预设策略文件”的场景。若未提供策略名称，则列出可用策略。

        Returns:
            ReportOutput: ``text`` 为发送给用户的文本，``data`` 为 ``ScanReport`` 对象。
        """
        metric = metrics.start_operation("select_stocks_by_preset", preset_name=preset_name)
        for attempt in range(2):
            try:
                if not preset_name or not preset_name.strip():
                    presets = FixedStrategy.list_presets()
                    return ReportOutput(
                        text=(
                            "请提供固定策略名称。\n"
                            "用法：/固定策略 <策略名称>\n"
                            f"可用策略：{', '.join(presets) if presets else '无'}"
                        )
                    )

                strategy = registry.build(
                    "固定策略选股",
                    params={
                        "preset_name": preset_name,
                        "llm_provider": self.llm_provider,
                        "on_auth_error": self.on_kimi_auth_error,
                    },
                )
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
                return ReportOutput(
                    text=(f"{formatted}\n\n---\n完整选股报告已保存至：{txt_path}"),
                    data=report,
                )
            except KimiAuthError as exc:
                if attempt == 0 and self.on_kimi_auth_error is not None:
                    logger.warning(f"Kimi 凭证过期，尝试刷新：{exc}")
                    await self.on_kimi_auth_error(str(exc))
                    await asyncio.sleep(8)
                    continue
                metric.finish(success=False, error=str(exc))
                logger.error(f"固定策略选股失败：{exc}")
                raise
        # 理论上不会执行到这里，保留类型安全
        return ReportOutput(text="固定策略选股失败：重试后仍无法完成选股")

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
                f"- {r['report_id']} [{r['report_type']}] {r['title']} ({r['created_at']})"
            )
        return "\n".join(lines)

    async def add_watch(self, symbol: str) -> str:
        """将股票加入自选股池。

        若股票已存在且来源为自动入库（auto），手动添加会将其升级为
        手动持有（manual），不再受自动移除机制影响。

        Args:
            symbol: 股票代码，支持任意常用格式，会自动标准化。

        Returns:
            操作结果提示文本。
        """
        symbol = InputValidator.validate_symbol(symbol)
        added = self.watchlist_storage.add(symbol)
        normalized = self.watchlist_storage.list()
        if not added:
            item = self.watchlist_storage.get_item(symbol)
            if item is not None and item.source == SOURCE_AUTO:
                self.watchlist_storage.set_source(symbol, SOURCE_MANUAL)
                return (
                    f"{symbol} 已在自选股池中（自动入库），已转为手动持有，不再自动移除。\n"
                    f"当前股池：{', '.join(normalized)}"
                )
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

    async def track_watchlist(self, days: int = 5) -> ReportOutput:
        """追踪自选股池，返回最近 N 个交易日 + 今日行情的价格宽表。

        每只股票占用一行，行首为"股票名称+股票代码"；列为 日期1...日期N、今日，
        每个单元格展示"开盘价/收盘价（实时价）"。

        Args:
            days: 展示最近多少个交易日，默认 5 天。

        Returns:
            ReportOutput: ``text`` 为 Markdown 表格文本，``data`` 为生成图片用的 DataFrame。
        """
        symbols = self.watchlist_storage.list()
        if not symbols:
            return ReportOutput(text="自选股池为空，请先使用 /观察 股票代码 添加股票。")

        from datetime import datetime, timedelta

        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=max(days * 2, 30))).strftime("%Y-%m-%d")

        metric = metrics.start_operation("track_watchlist", symbols=len(symbols))
        # 网关数据访问为同步阻塞网络调用，放入线程执行避免阻塞事件循环
        ohlcv_data = await asyncio.to_thread(
            self.gateway.get_ohlcv_multi,
            symbols,
            start_date=start_date,
            end_date=end_date,
            bars=days,
        )
        realtime_data = await asyncio.to_thread(self._get_realtime_data, symbols)

        date_columns = [f"日期{i}" for i in range(1, days + 1)]
        wide_rows: list[dict[str, str]] = []
        failed_symbols: list[str] = []
        for symbol in symbols:
            df = ohlcv_data.get(symbol)
            if df is None or df.empty:
                try:
                    df = await asyncio.to_thread(
                        self.gateway.get_ohlcv,
                        symbol,
                        start_date=start_date,
                        end_date=end_date,
                        bars=days,
                    )
                except Exception as exc:
                    logger.warning(f"获取 {symbol} K 线失败：{exc}")
                    failed_symbols.append(symbol)
                    continue
            if df is None or df.empty:
                failed_symbols.append(symbol)
                continue

            name = await asyncio.to_thread(self._get_stock_name, symbol)
            stock_label = f"{name}{symbol}" if name != symbol else symbol
            realtime = realtime_data.get(symbol, {})
            row: dict[str, str] = {"股票": stock_label}

            recent_df = df.tail(days).copy()
            for idx, (_, r) in enumerate(recent_df.iterrows()):
                open_price = self._format_price(r.get("open"))
                close_price = self._format_price(r.get("close"))
                row[date_columns[idx]] = f"{open_price}/{close_price}"

            # 如果 K 线不足 days 根，剩余历史列补 "-/-"
            for col in date_columns:
                if col not in row:
                    row[col] = "-/-"

            row["今日"] = f"{realtime.get('open', '-')}/{realtime.get('price', '-')}"
            wide_rows.append(row)

        metric.finish(success=True, rows=len(wide_rows), failed=len(failed_symbols))
        if not wide_rows:
            return ReportOutput(text="未能获取到自选股池的行情数据，请稍后重试。")

        result_df = pd.DataFrame(wide_rows, columns=["股票"] + date_columns + ["今日"])
        table = self._format_markdown_table(result_df)

        lines = [f"自选股池追踪（最近 {days} 个交易日 + 今日）：", "", table]
        if failed_symbols:
            lines.append("")
            lines.append(f"以下股票获取数据失败：{', '.join(failed_symbols)}")
        return ReportOutput(text="\n".join(lines), data=result_df)

    def _get_realtime_data(self, symbols: list[str]) -> dict[str, dict[str, str]]:
        """批量获取每只股票的最新实时价格与今日开盘价。

        实时价格从 get_realtime_price 获取，今日开盘价从 get_close_summary 获取。
        返回以内部标准代码为键、包含 price/open 的字典。
        """
        if not symbols:
            return {}

        result: dict[str, dict[str, str]] = {}
        for symbol in symbols:
            result[symbol] = {"price": "-", "open": "-"}

        # 1. 获取实时价格
        try:
            price_df = self.gateway.get_realtime_price(symbols)
            if price_df is not None and not price_df.empty:
                price_col = self._find_realtime_price_column(price_df)
                symbol_col = self._find_symbol_column(price_df)
                if price_col:
                    for idx, symbol in enumerate(symbols):
                        row = self._find_realtime_row(price_df, symbol_col, symbol, idx)
                        if row is not None and price_col in row:
                            result[symbol]["price"] = self._format_price(row[price_col])
        except Exception as exc:
            logger.warning(f"获取实时价格失败：{exc}")

        # 2. 获取今日开盘价（close_summary）
        try:
            summary_df = self.gateway.get_close_summary(symbols)
            if summary_df is not None and not summary_df.empty:
                open_col = self._find_open_column(summary_df)
                symbol_col = self._find_symbol_column(summary_df)
                if open_col:
                    for idx, symbol in enumerate(symbols):
                        row = self._find_realtime_row(summary_df, symbol_col, symbol, idx)
                        if row is not None and open_col in row:
                            result[symbol]["open"] = self._format_price(row[open_col])
        except Exception as exc:
            logger.warning(f"获取今日开盘价失败：{exc}")

        return result

    def _find_realtime_price_column(self, df: pd.DataFrame) -> str:
        """从实时行情 DataFrame 中查找价格列。"""
        candidates = [
            "close",
            "price",
            "latest",
            "最新价",
            "现价",
            "收盘价",
            "last_price",
        ]
        for col in candidates:
            if col in df.columns:
                return col
        # 兜底：查找包含 price/价的列
        for col in df.columns:
            if "价" in col or "price" in col.lower():
                return col
        return ""

    def _find_open_column(self, df: pd.DataFrame) -> str:
        """从 DataFrame 中查找今日开盘价列。"""
        candidates = ["open", "今开", "开盘", "开盘价", "open_price"]
        for col in candidates:
            if col in df.columns:
                return col
        # 兜底：查找包含 open/开的列
        for col in df.columns:
            if "开" in col or "open" in col.lower():
                return col
        return ""

    def _find_symbol_column(self, df: pd.DataFrame) -> str:
        """从实时行情 DataFrame 中查找股票代码列。"""
        candidates = ["symbol", "ticker", "thscode", "code", "代码", "股票代码"]
        for col in candidates:
            if col in df.columns:
                return col
        return ""

    def _find_realtime_row(
        self,
        df: pd.DataFrame,
        symbol_col: str,
        symbol: str,
        index: int,
    ) -> pd.Series | None:
        """在实时行情 DataFrame 中定位某只股票对应的行。

        优先按 symbol 列匹配，失败时回退到输入顺序的第 index 行。
        """
        if not symbol_col or symbol_col not in df.columns:
            return df.iloc[index] if index < len(df) else None

        normalized = SymbolNormalizer.normalize(symbol)
        for _, row in df.iterrows():
            row_symbol = str(row.get(symbol_col, "")).strip()
            if not row_symbol:
                continue
            if row_symbol == symbol:
                return row
            if SymbolNormalizer.normalize(row_symbol) == normalized:
                return row
            # 兼容无后缀 6 位代码
            if row_symbol.replace(".", "") == symbol.split(".")[0]:
                return row

        return df.iloc[index] if index < len(df) else None

    def _get_stock_name(self, symbol: str) -> str:
        """获取股票简称，失败时返回代码本身。"""
        try:
            company_info = self.gateway.get_company_info(symbol)
            name = company_info.get("ths_stock_short_name_stock", "")
            if name and str(name).strip():
                return str(name).strip()
        except Exception as exc:
            logger.debug(f"获取 {symbol} 公司名称失败：{exc}")
        return symbol

    def _format_price(self, value: Any) -> str:
        """将价格格式化为两位小数，失败时返回原字符串。"""
        try:
            return f"{float(value):.2f}"
        except (ValueError, TypeError):
            return str(value)

    def _format_markdown_table(self, df: pd.DataFrame) -> str:
        """将 DataFrame 格式化为 Markdown 表格。"""
        if df.empty:
            return ""
        headers = df.columns.tolist()
        lines = ["| " + " | ".join(headers) + " |"]
        lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
        for _, row in df.iterrows():
            lines.append("| " + " | ".join(str(row[col]) for col in headers) + " |")
        return "\n".join(lines)

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
        summary_path.write_text(self._format_analysis_markdown(report), encoding="utf-8")
        detailed_path.write_text(self._format_analysis_detailed_markdown(report), encoding="utf-8")

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
                    lines.append(f"**{key}**：")
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
