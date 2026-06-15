"""报告生成与存储。"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from jinja2 import Environment, FileSystemLoader, PackageLoader

from outluna.config import settings
from outluna.data.models import AnalysisReport, BacktestReport, ScanReport


class ReportStorage:
    """报告存储管理。"""

    def __init__(self, report_dir: Path | None = None):
        self.report_dir = report_dir or settings.report_dir
        self.report_dir.mkdir(parents=True, exist_ok=True)

    def save(self, report: AnalysisReport | ScanReport | BacktestReport) -> Path:
        """保存报告为 JSON 和 Markdown。"""
        data = self._serialize(report)
        json_path = self.report_dir / f"{report.report_id}.json"
        md_path = self.report_dir / f"{report.report_id}.md"

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, default=str, indent=2)

        md_content = self._to_markdown(report)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        return json_path

    def load(self, report_id: str) -> dict[str, Any] | None:
        """加载报告。"""
        json_path = self.report_dir / f"{report_id}.json"
        if not json_path.exists():
            return None
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def list_reports(self, report_type: str | None = None) -> list[dict[str, Any]]:
        """列出所有报告。"""
        reports = []
        for path in self.report_dir.glob("*.json"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if report_type is None or data.get("report_type") == report_type:
                    reports.append(
                        {
                            "report_id": data.get("report_id"),
                            "report_type": data.get("report_type"),
                            "created_at": data.get("created_at"),
                            "title": data.get("title", ""),
                        }
                    )
            except Exception:
                continue
        return sorted(reports, key=lambda x: x["created_at"], reverse=True)

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
