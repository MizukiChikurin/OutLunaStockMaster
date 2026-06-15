"""核心引擎，整合策略、分析、报告能力。"""

from outluna.analysis.orchestrator import AnalysisOrchestrator
from outluna.data.gateway import DataGateway
from outluna.report.generator import ReportGenerator
from outluna.strategy import registry
from outluna.strategy.scanner import StockScanner


class OutLunaEngine:
    """OutLuna 核心引擎。"""

    def __init__(self):
        self.gateway = DataGateway()
        self.report_generator = ReportGenerator()

    async def initialize(self) -> None:
        """初始化引擎。"""
        # 可在此加载模型、预热缓存等
        pass

    async def scan(
        self,
        strategy_name: str,
        universe: list[str] | None = None,
        max_candidates: int | None = None,
    ) -> str:
        """执行策略扫描并保存报告，返回报告文本。"""
        strategy = registry.build(strategy_name)
        scanner = StockScanner(self.gateway, strategy)
        report = scanner.scan(universe=universe, max_candidates=max_candidates)
        self.report_generator.save(report)
        return report.format_text()

    async def analyze(self, symbol: str, strategy_name: str = "") -> str:
        """分析指定股票并保存报告，返回报告文本。"""
        orchestrator = AnalysisOrchestrator(self.gateway, enable_llm=False)
        report = await orchestrator.analyze(symbol, strategy_name)
        self.report_generator.save(report)
        return self._format_analysis_text(report)

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
