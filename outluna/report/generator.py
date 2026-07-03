import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from outluna.config import settings
from outluna.data.models import AnalysisReport, BacktestReport, DataCall, ScanReport
from outluna.data.storage import SQLiteStorage


@dataclass
class ReportPaths:
    """报告保存后的路径集合。"""

    json: Path
    md: Path
    html: Path
    txt: Path
    task_folder: Path | None = None
    summary_md: Path | None = None
    detailed_md: Path | None = None


def _get_risk_class(risk_rating: str) -> str:
    """根据风险等级返回 CSS 类名。"""
    if "低" in risk_rating:
        return "risk-low"
    if "高" in risk_rating:
        return "risk-high"
    return "risk-mid"


def _sanitize_folder_name(name: str) -> str:
    """清理文件夹名称中的非法字符。"""
    name = re.sub(r'[\\/:*?"<>|]', "-", name)
    return name.strip("-.")


class ReportStorage:
    """报告存储管理。"""

    def __init__(self, report_dir: Path | None = None, db_storage: SQLiteStorage | None = None):
        self.report_dir = report_dir or settings.report_dir
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.db = db_storage or SQLiteStorage()
        template_dir = Path(__file__).parent / "templates"
        self._env = Environment(loader=FileSystemLoader(str(template_dir)), autoescape=False)

    def save(self, report: AnalysisReport | ScanReport | BacktestReport) -> ReportPaths:
        """保存报告为 JSON、Markdown、HTML 和 TXT，并写入数据库索引。"""
        data = self._serialize(report)
        paths = ReportPaths(
            json=self.report_dir / f"{report.report_id}.json",
            md=self.report_dir / f"{report.report_id}.md",
            html=self.report_dir / f"{report.report_id}.html",
            txt=self.report_dir / f"{report.report_id}.txt",
        )

        with open(paths.json, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, default=str, indent=2)

        md_content = self._to_markdown(report)
        with open(paths.md, "w", encoding="utf-8") as f:
            f.write(md_content)

        html_content = self._render_html(report, data)
        if html_content:
            with open(paths.html, "w", encoding="utf-8") as f:
                f.write(html_content)

        txt_content = self._to_text(report)
        with open(paths.txt, "w", encoding="utf-8") as f:
            f.write(txt_content)

        self._save_to_db(report, str(paths.json))

        # 选股报告额外生成任务文件夹：总结 + 详细流程
        if isinstance(report, ScanReport) and report.task_folder:
            paths.task_folder, paths.summary_md, paths.detailed_md = self._save_task_folder(report)

        return paths

    def _save_task_folder(self, report: ScanReport) -> tuple[Path, Path, Path]:
        """生成任务文件夹，包含总结.md 和 详细流程.md。"""
        folder_name = _sanitize_folder_name(report.task_folder)
        task_folder = self.report_dir.parent / "tasks" / folder_name
        task_folder.mkdir(parents=True, exist_ok=True)

        summary_path = task_folder / "总结.md"
        detailed_path = task_folder / "详细流程.md"

        summary_content = self._build_summary(report)
        detailed_content = self._build_detailed_process(report)

        with open(summary_path, "w", encoding="utf-8") as f:
            f.write(summary_content)
        with open(detailed_path, "w", encoding="utf-8") as f:
            f.write(detailed_content)

        return task_folder, summary_path, detailed_path

    def _build_summary(self, report: ScanReport) -> str:
        """构建总结报告：只显示结果。"""
        lines = [
            f"# {report.strategy_name} - 总结报告",
            "",
            f"- 分析日期：{report.created_at.strftime('%Y年%m月%d日')}",
            f"- 数据时间：{report.data_time}",
            f"- 数据来源：{report.data_source}",
            f"- 扫描总数：{report.total_scanned} 只",
            "",
        ]

        if report.market_summary:
            lines.append("## 市场环境")
            lines.append(report.market_summary)
            lines.append("")

        if report.qualified:
            lines.append(f"## 推荐候选（评分≥{self._min_score(report):.0f}分，共 {len(report.qualified)} 只）")
            for idx, item in enumerate(report.qualified, 1):
                lines.append(
                    f"{idx}. {item.symbol} {item.name} | "
                    f"总分：{item.match_score:.0f} | "
                    f"最新价：{item.price:.2f} 涨跌：{item.change_pct:+.2f}% "
                    f"成交额：{item.turnover:.2f}亿"
                )
                if item.notes:
                    lines.append(f"   亮点：{' | '.join(item.notes[:3])}")
            lines.append("")
        else:
            lines.append("## 推荐候选")
            lines.append("无符合推荐条件的股票。")
            lines.append("")

        if report.watch_list:
            lines.append(f"## 观察股（共 {len(report.watch_list)} 只）")
            for idx, item in enumerate(report.watch_list, 1):
                lines.append(
                    f"{idx}. {item.symbol} {item.name} | "
                    f"总分：{item.match_score:.0f} | "
                    f"最新价：{item.price:.2f} 涨跌：{item.change_pct:+.2f}% "
                    f"成交额：{item.turnover:.2f}亿"
                )
            lines.append("")

        if report.vetoed:
            lines.append(f"## 一票否决（共 {len(report.vetoed)} 只）")
            for idx, item in enumerate(report.vetoed[:20], 1):
                lines.append(f"{idx}. {item.symbol} {item.name} | 原因：{'；'.join(item.vetos[:3])}")
            if len(report.vetoed) > 20:
                lines.append(f"... 还有 {len(report.vetoed) - 20} 只")
            lines.append("")

        if report.final_conclusion:
            lines.append("## 最终结论")
            lines.append(report.final_conclusion)
            lines.append("")

        lines.append(
            "> ⚠️ AI生成，不构成投资建议。股市有风险，投资需谨慎。"
        )
        return "\n".join(lines)

    def _build_detailed_process(self, report: ScanReport) -> str:
        """构建详细流程报告：记录每一步调用的接口和股票。"""
        trace = report.execution_trace
        lines = [
            f"# {report.strategy_name} - 详细流程",
            "",
            f"- 报告ID：{report.report_id}",
            f"- 生成时间：{report.created_at.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- 数据时间：{report.data_time}",
            f"- 数据来源：{report.data_source}",
            "",
            "## 一、执行流程概述",
            "",
            "本次选股任务按以下阶段执行：",
            "",
        ]

        for idx, phase in enumerate(trace.phases, 1):
            lines.append(f"{idx}. **{phase['name']}**：{phase['description']}")
            details = phase.get("details") or {}
            if details:
                for k, v in details.items():
                    lines.append(f"   - {k}：{v}")
        lines.append("")

        if trace.stock_counts:
            lines.append("## 二、股票池变化")
            lines.append("")
            lines.append("| 步骤 | 股票数量 | 说明 |")
            lines.append("|------|----------|------|")
            for count in trace.stock_counts:
                lines.append(f"| {count['step']} | {count['count']} | {count['note']} |")
            lines.append("")

        if trace.data_calls:
            lines.append("## 三、数据源调用详情")
            lines.append("")

            grouped: dict[str, list[DataCall]] = {}
            for call in trace.data_calls:
                grouped.setdefault(call.phase, []).append(call)

            for phase_name, calls in grouped.items():
                lines.append(f"### {phase_name}")
                lines.append("")
                total = len(calls)
                success = sum(1 for c in calls if c.status == "成功")
                elapsed = sum(c.elapsed_seconds for c in calls)
                lines.append(f"- 调用次数：{total} 次（成功 {success} 次）")
                lines.append(f"- 总耗时：{elapsed:.2f} 秒")
                lines.append("")
                lines.append("| 序号 | 数据源 | 接口 | 股票 | 参数 | 状态 | 结果摘要 | 耗时(秒) |")
                lines.append("|------|--------|------|------|------|------|----------|----------|")
                for idx, call in enumerate(calls, 1):
                    symbols = ", ".join(call.symbols[:10]) or "-"
                    if len(call.symbols) > 10:
                        symbols += f" 等{len(call.symbols)}只"
                    params = json.dumps(call.params, ensure_ascii=False) if call.params else "-"
                    lines.append(
                        f"| {idx} | {call.provider} | {call.method} | {symbols} | {params} | "
                        f"{call.status} | {call.result_summary} | {call.elapsed_seconds:.2f} |"
                    )
                lines.append("")
        else:
            lines.append("## 三、数据源调用详情")
            lines.append("")
            lines.append("未记录到数据源调用。")
            lines.append("")

        if trace.notes:
            lines.append("## 四、执行备注")
            lines.append("")
            for note in trace.notes:
                lines.append(f"- {note}")
            lines.append("")

        lines.append("## 五、最终筛选结果")
        lines.append("")
        lines.append(f"- 进入详细分析：{report.total_scanned} 只")
        lines.append(f"- 推荐候选：{len(report.qualified)} 只")
        lines.append(f"- 观察股：{len(report.watch_list)} 只")
        lines.append(f"- 一票否决：{len(report.vetoed)} 只")
        lines.append("")
        lines.append(
            "> ⚠️ AI生成，不构成投资建议。股市有风险，投资需谨慎。"
        )
        return "\n".join(lines)

    def _min_score(self, report: ScanReport) -> float:
        """获取推荐最低分。"""
        if report.qualified:
            return min(r.match_score for r in report.qualified)
        return 85.0

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
                "data_time": report.data_time,
                "data_source": report.data_source,
                "market_summary": report.market_summary,
                "final_conclusion": report.final_conclusion,
                "matches": [
                    {
                        "symbol": m.symbol,
                        "name": m.name,
                        "price": m.price,
                        "change_pct": m.change_pct,
                        "turnover": m.turnover,
                        "match_score": m.match_score,
                        "recommendation": m.recommendation,
                        "score_details": m.score_details,
                        "notes": m.notes,
                        "vetos": m.vetos,
                        "matched_at": m.matched_at.isoformat(),
                    }
                    for m in report.matches
                ],
                "qualified": [
                    {"symbol": m.symbol, "name": m.name, "match_score": m.match_score}
                    for m in report.qualified
                ],
                "watch_list": [
                    {"symbol": m.symbol, "name": m.name, "match_score": m.match_score}
                    for m in report.watch_list
                ],
                "vetoed": [
                    {"symbol": m.symbol, "name": m.name, "vetos": m.vetos}
                    for m in report.vetoed
                ],
                "title": f"{report.strategy_name}报告",
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
                f"- 年化收益率：{report.metrics.annualized_return:.2f}",
                f"- 基准收益率（沪深300）：{report.metrics.benchmark_return:.2f}",
                f"- 超额收益（Alpha）：{report.metrics.alpha:.2f}",
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

    def _to_text(self, report: AnalysisReport | ScanReport | BacktestReport) -> str:
        """生成面向聊天场景的纯文本报告，便于用户直接阅读。"""
        if isinstance(report, ScanReport):
            return report.format_text()
        if isinstance(report, AnalysisReport):
            return self._to_markdown(report)
        if isinstance(report, BacktestReport):
            return self._to_markdown(report)
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
        """保存报告并返回 txt 报告路径。"""
        paths = self.storage.save(report)
        if paths.detailed_md:
            return str(paths.detailed_md)
        return str(paths.txt)

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
