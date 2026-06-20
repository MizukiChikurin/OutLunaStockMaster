"""报告生成与存储。"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from outluna.config import settings
from outluna.data.models import AnalysisReport, BacktestReport, ScanReport
from outluna.data.storage import SQLiteStorage


def _get_risk_class(risk_rating: str) -> str:
    """根据风险等级返回 CSS 类名。"""
    if "低" in risk_rating:
        return "risk-low"
    if "高" in risk_rating:
        return "risk-high"
    return "risk-mid"


class ReportStorage:
    """报告存储管理。"""

    def __init__(self, report_dir: Path | None = None, db_storage: SQLiteStorage | None = None):
        self.report_dir = report_dir or settings.report_dir
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.db = db_storage or SQLiteStorage()
        template_dir = Path(__file__).parent / "templates"
        self._env = Environment(loader=FileSystemLoader(str(template_dir)), autoescape=False)

    def save(self, report: AnalysisReport | ScanReport | BacktestReport) -> Path:
        """保存报告为 JSON、Markdown 和 HTML，并写入数据库索引。"""
        data = self._serialize(report)
        json_path = self.report_dir / f"{report.report_id}.json"
        md_path = self.report_dir / f"{report.report_id}.md"
        html_path = self.report_dir / f"{report.report_id}.html"

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, default=str, indent=2)

        md_content = self._to_markdown(report)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        html_content = self._render_html(report, data)
        if html_content:
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html_content)

        self._save_to_db(report, str(json_path))
        return json_path

    def _save_to_db(self, report: AnalysisReport | ScanReport | BacktestReport, report_path: str) -> None:
        """将报告元数据写入数据库。"""
        if isinstance(report, ScanReport):
            self.db.save_scan_record(
                scan_id=report.report_id,
                strategy_name=report.strategy_name,
                strategy_params=report.strategy_params,
                total_scanned=report.total_scanned,
                matched_count=len(report.matches),
                report_path=report_path,
            )
            matches = [
                {
                    "symbol": m.symbol,
                    "match_score": m.match_score,
                    "trigger_data": m.trigger_data,
                }
                for m in report.matches
            ]
            self.db.save_scan_matches(report.report_id, matches)
        elif isinstance(report, AnalysisReport):
            self.db.save_analysis_report(
                report_id=report.report_id,
                symbol=report.symbol,
                strategy_name=report.strategy_name,
                risk_rating=report.risk_rating,
                recommendation=report.recommendation,
                report_path=report_path,
            )
        elif isinstance(report, BacktestReport):
            self.db.save_backtest_record(
                backtest_id=report.report_id,
                strategy_name=report.strategy_name,
                start_date=report.start_date,
                end_date=report.end_date,
                initial_capital=report.initial_capital,
                total_return=report.metrics.total_return,
                annualized_return=report.metrics.annualized_return,
                win_rate=report.metrics.win_rate,
                max_drawdown=report.metrics.max_drawdown,
                sharpe_ratio=report.metrics.sharpe_ratio,
                total_trades=report.metrics.total_trades,
                result_path=report_path,
            )

    def load(self, report_id: str) -> dict[str, Any] | None:
        """加载报告。"""
        json_path = self.report_dir / f"{report_id}.json"
        if not json_path.exists():
            return None
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            return None

    def list_reports(self, report_type: str | None = None) -> list[dict[str, Any]]:
        """列出所有报告。"""
        return self.db.list_reports(report_type)

    def _serialize(self, report: AnalysisReport | ScanReport | BacktestReport) -> dict[str, Any]:
        """序列化报告。"""
        if isinstance(report, AnalysisReport):
            return {
                "report_id": report.report_id,
                "report_type": "analysis",
                "created_at": report.created_at.isoformat(),
                "symbol": report.symbol,
                "strategy_name": report.strategy_name,
                "risk_rating": report.risk_rating,
                "recommendation": report.recommendation,
                "llm_summary": report.llm_summary,
                "results": {
                    dim: {
                        "dimension": r.dimension,
                        "score": r.score,
                        "summary": r.summary,
                        "signals": r.signals,
                    }
                    for dim, r in report.results.items()
                },
                "title": f"{report.symbol} 分析报告",
            }
        elif isinstance(report, ScanReport):
            return {
                "report_id": report.report_id,
                "report_type": "scan",
                "created_at": report.created_at.isoformat(),
                "strategy_name": report.strategy_name,
                "strategy_params": report.strategy_params,
                "total_scanned": report.total_scanned,
                "matches": [
                    {
                        "symbol": m.symbol,
                        "match_score": m.match_score,
                        "matched_at": m.matched_at.isoformat(),
                    }
                    for m in report.matches
                ],
                "title": f"{report.strategy_name} 扫描报告",
            }
        elif isinstance(report, BacktestReport):
            return {
                "report_id": report.report_id,
                "report_type": "backtest",
                "created_at": datetime.now().isoformat(),
                "strategy_name": report.strategy_name,
                "start_date": report.start_date.isoformat(),
                "end_date": report.end_date.isoformat(),
                "initial_capital": report.initial_capital,
                "metrics": {
                    "total_return": report.metrics.total_return,
                    "annualized_return": report.metrics.annualized_return,
                    "win_rate": report.metrics.win_rate,
                    "profit_factor": report.metrics.profit_factor,
                    "max_drawdown": report.metrics.max_drawdown,
                    "sharpe_ratio": report.metrics.sharpe_ratio,
                    "total_trades": report.metrics.total_trades,
                    "alpha": report.metrics.alpha,
                    "benchmark_return": report.metrics.benchmark_return,
                },
                "trade_log": [
                    {
                        "symbol": t.symbol,
                        "action": t.action,
                        "date": t.date.isoformat(),
                        "price": t.price,
                        "shares": t.shares,
                        "reason": t.reason,
                    }
                    for t in report.trade_log
                ],
                "title": f"{report.strategy_name} 回测报告",
            }
        return {}

    def _to_markdown(self, report: AnalysisReport | ScanReport | BacktestReport) -> str:
        """将报告转换为 Markdown 文本。"""
        if isinstance(report, AnalysisReport):
            lines = [
                f"# {report.symbol} 投资分析报告",
                "",
                f"- 报告ID：{report.report_id}",
                f"- 生成时间：{report.created_at.strftime('%Y-%m-%d %H:%M:%S')}",
                f"- 风险等级：{report.risk_rating}",
                "",
                "## 投资建议",
                "",
                report.recommendation,
                "",
                "## 多维度分析",
                "",
            ]
            for dim, result in report.results.items():
                lines.append(f"### {dim}（评分：{result.score:.0f}/100）")
                lines.append("")
                lines.append(result.summary)
                lines.append("")
                if result.signals:
                    lines.append("- " + "\n- ".join(result.signals))
                    lines.append("")
            return "\n".join(lines)

        elif isinstance(report, ScanReport):
            lines = [
                f"# {report.strategy_name} 扫描报告",
                "",
                f"- 报告ID：{report.report_id}",
                f"- 生成时间：{report.created_at.strftime('%Y-%m-%d %H:%M:%S')}",
                f"- 扫描总数：{report.total_scanned}",
                f"- 命中数量：{len(report.matches)}",
                "",
                "## 匹配股票",
                "",
            ]
            for idx, match in enumerate(report.matches, 1):
                lines.append(f"{idx}. {match.symbol}（匹配度：{match.match_score:.2f}）")
            return "\n".join(lines)

        elif isinstance(report, BacktestReport):
            lines = [
                f"# {report.strategy_name} 回测报告",
                "",
                f"- 报告ID：{report.report_id}",
                f"- 回测区间：{report.start_date.date()} ~ {report.end_date.date()}",
                f"- 初始资金：{report.initial_capital:,.2f}",
                "",
                "## 绩效指标",
                "",
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
                "## 交易记录",
                "",
            ]
            for trade in report.trade_log:
                lines.append(
                    f"- {trade.date.date()} {trade.action} {trade.symbol} "
                    f"@{trade.price:.2f} × {trade.shares}（{trade.reason}）"
                )
            return "\n".join(lines)

        return ""

    def _render_html(self, report: AnalysisReport | ScanReport | BacktestReport, data: dict[str, Any]) -> str:
        """使用 Jinja2 模板渲染 HTML 报告。"""
        try:
            template = self._env.get_template("html.j2")
            context = dict(data)
            if isinstance(report, AnalysisReport):
                context["risk_class"] = _get_risk_class(report.risk_rating)
            return template.render(**context)
        except Exception as exc:
            logger = __import__("outluna.utils.logger", fromlist=["setup_logging"]).setup_logging()
            logger.warning(f"HTML 模板渲染失败：{exc}")
            return ""


class ReportGenerator:
    """报告生成器。"""

    def __init__(self, storage: ReportStorage | None = None):
        self.storage = storage or ReportStorage()

    def save(self, report: AnalysisReport | ScanReport | BacktestReport) -> str:
        """保存报告并返回报告 ID。"""
        self.storage.save(report)
        return report.report_id

    def load(self, report_id: str) -> dict[str, Any] | None:
        """加载报告。"""
        return self.storage.load(report_id)

    def list_reports(self, report_type: str | None = None) -> list[dict[str, Any]]:
        """列出报告。"""
        return self.storage.list_reports(report_type)

    def compare(self, id1: str, id2: str) -> dict[str, Any]:
        """对比两份报告。"""
        r1 = self.storage.load(id1)
        r2 = self.storage.load(id2)
        if not r1 or not r2:
            return {"error": "报告不存在"}
        return {
            "report_1": {"id": id1, "type": r1.get("report_type")},
            "report_2": {"id": id2, "type": r2.get("report_type")},
            "fields_in_both": list(set(r1.keys()) & set(r2.keys())),
            "report_1_only": list(set(r1.keys()) - set(r2.keys())),
            "report_2_only": list(set(r2.keys()) - set(r1.keys())),
        }
