"""核心引擎，整合策略、分析、报告能力。"""

from outluna.analysis.orchestrator import AnalysisOrchestrator
from outluna.backtest.engine import run_backtest
from outluna.data.gateway import DataGateway
from outluna.report.generator import ReportGenerator
from outluna.strategy import registry
from outluna.strategy.scanner import StockScanner
from outluna.utils.logger import setup_logging
from outluna.utils.metrics import metrics

logger = setup_logging()


class OutLunaEngine:
    """OutLuna 核心引擎。"""

    def __init__(self):
        self.gateway = DataGateway()
        self.report_generator = ReportGenerator()

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
        metric = metrics.start_operation("scan", strategy=strategy_name)
        try:
            strategy = registry.build(strategy_name)
            scanner = StockScanner(self.gateway, strategy)
            report = scanner.scan(universe=universe, max_candidates=max_candidates)
            self.report_generator.save(report)
            metric.finish(success=True, matched=len(report.matches))
            logger.info(f"扫描完成：{strategy_name}，命中 {len(report.matches)} 只")
            return report.format_text()
        except Exception as exc:
            metric.finish(success=False, error=str(exc))
            logger.error(f"扫描失败：{exc}")
            raise

    async def analyze(self, symbol: str, strategy_name: str = "") -> str:
        """分析指定股票并保存报告，返回报告文本。"""
        metric = metrics.start_operation("analyze", symbol=symbol)
        try:
            orchestrator = AnalysisOrchestrator(self.gateway, enable_llm=False)
            report = await orchestrator.analyze(symbol, strategy_name)
            self.report_generator.save(report)
            metric.finish(success=True, risk=report.risk_rating)
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
        result = self.report_generator.compare(id1, id2)
        import json
        return json.dumps(result, ensure_ascii=False, indent=2)

    def get_metrics(self) -> dict:
        """获取运行指标。"""
        summary = metrics.get_summary()
        summary["data_gateway_stats"] = self.gateway.get_call_stats()
        return summary

    def _format_analysis_text(self, report) -> str:
        """格式化分析报告为聊天文本。"""
        lines = [
            f"股票：{report.symbol}",
            f"风险等级：{report.risk_rating}",
            "",
            "投资建议：",
            report.recommendation,
            "",
            "各维度评分：",
        ]
        for dim, result in report.results.items():
            lines.append(f"- {dim}: {result.score:.0f}/100")
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
            f"- 基准收益率（沪深300）：{report.metrics.benchmark_return:.2%}",
            f"- 超额收益（Alpha）：{report.metrics.alpha:.2%}",
            f"- 胜率：{report.metrics.win_rate:.2%}",
            f"- 盈亏比：{report.metrics.profit_factor:.2f}",
            f"- 最大回撤：{report.metrics.max_drawdown:.2%}",
            f"- 夏普比率：{report.metrics.sharpe_ratio:.2f}",
            f"- 总交易次数：{report.metrics.total_trades}",
            "",
            f"报告ID：{report.report_id}",
        ]
        return "\n".join(lines)

