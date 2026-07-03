"""数据模型定义。"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd


@dataclass
class DataCall:
    """单次数据源调用记录。"""

    step: int
    phase: str
    provider: str
    method: str
    symbols: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    status: str = "成功"  # 成功 / 失败 / 部分成功
    result_summary: str = ""
    elapsed_seconds: float = 0.0


@dataclass
class ExecutionTrace:
    """选股执行流程追踪。"""

    phases: list[dict[str, Any]] = field(default_factory=list)
    data_calls: list[DataCall] = field(default_factory=list)
    stock_counts: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add_phase(self, name: str, description: str, details: dict[str, Any] | None = None) -> None:
        """添加一个执行阶段。"""
        self.phases.append({
            "name": name,
            "description": description,
            "details": details or {},
        })

    def add_call(self, call: DataCall) -> None:
        """添加一次数据源调用。"""
        self.data_calls.append(call)

    def add_count(self, step: str, count: int, note: str = "") -> None:
        """添加股票数量变化节点。"""
        self.stock_counts.append({"step": step, "count": count, "note": note})

    def add_note(self, note: str) -> None:
        """添加流程备注。"""
        self.notes.append(note)


@dataclass
class OHLCV:
    """单根K线数据。"""

    date: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class DataRequirement:
    """策略所需数据规格。"""

    period: str = "1d"
    bars: int = 5
    adjust: str = "qfq"


@dataclass
class ScanResult:
    """扫描结果。"""

    symbol: str
    strategy_name: str
    matched_at: datetime
    match_score: float = 1.0
    trigger_data: dict[str, Any] = field(default_factory=dict)
    # 以下为超短线选股报告扩展字段
    name: str = ""
    price: float | None = None
    change_pct: float | None = None
    turnover: float | None = None
    score_details: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    vetos: list[str] = field(default_factory=list)
    recommendation: str = ""


@dataclass
class CompanyInfo:
    """企业基本信息。"""

    symbol: str
    name: str = ""
    industry: str = ""
    market_cap: float | None = None
    credit_risk: str | None = None
    legal_cases: int = 0


@dataclass
class CapitalFlow:
    """资金流向数据。"""

    date: datetime
    main_inflow: float = 0.0
    retail_inflow: float = 0.0
    net_inflow: float = 0.0


@dataclass
class NewsItem:
    """新闻条目。"""

    title: str
    source: str
    published_at: datetime
    sentiment: float | None = None
    url: str | None = None


@dataclass
class TradeRecord:
    """回测交易记录。"""

    symbol: str
    action: str  # "buy" | "sell"
    date: datetime
    price: float
    shares: int
    reason: str
    pnl: float = 0.0  # 卖出时记录实际盈亏


@dataclass
class BacktestMetrics:
    """回测绩效指标。"""

    total_return: float = 0.0
    annualized_return: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    total_trades: int = 0
    alpha: float = 0.0  # 相对基准超额收益
    benchmark_return: float = 0.0  # 基准收益率


@dataclass
class AnalyzerResult:
    """单个分析器的结果。"""

    dimension: str
    data: dict[str, Any] = field(default_factory=dict)
    signals: list[str] = field(default_factory=list)
    score: float | None = None
    summary: str = ""


@dataclass
class AnalysisReport:
    """股票分析报告。"""

    report_id: str
    symbol: str
    created_at: datetime
    strategy_name: str = ""
    results: dict[str, AnalyzerResult] = field(default_factory=dict)
    llm_summary: str = ""
    risk_rating: str = ""  # 低风险/中风险/高风险
    recommendation: str = ""


@dataclass
class BacktestReport:
    """回测报告。"""

    report_id: str
    strategy_name: str
    start_date: datetime
    end_date: datetime
    initial_capital: float
    metrics: BacktestMetrics
    trade_log: list[TradeRecord] = field(default_factory=list)
    equity_curve: pd.DataFrame = field(default_factory=pd.DataFrame)


@dataclass
class ScanReport:
    """扫描报告。"""

    report_id: str
    strategy_name: str
    strategy_params: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    matches: list[ScanResult] = field(default_factory=list)
    total_scanned: int = 0
    # 以下为超短线选股报告扩展字段
    data_time: str = ""
    data_source: str = ""
    market_summary: str = ""
    vetoed: list[ScanResult] = field(default_factory=list)
    watch_list: list[ScanResult] = field(default_factory=list)
    qualified: list[ScanResult] = field(default_factory=list)
    final_conclusion: str = ""
    execution_trace: ExecutionTrace = field(default_factory=ExecutionTrace)
    task_folder: str = ""  # 选股任务-日期 形式

    def format_text(self, max_items: int = 20) -> str:
        """格式化为聊天文本。"""
        # 超短线选股报告使用专用排版
        if self.qualified or self.watch_list or self.vetoed:
            return self._format_selection_text(max_items)

        lines = [
            f"策略：{self.strategy_name}",
            f"扫描总数：{self.total_scanned}",
            f"命中数量：{len(self.matches)}",
            "",
        ]
        if not self.matches:
            lines.append("未找到匹配股票。")
            return "\n".join(lines)

        lines.append("匹配股票：")
        for idx, match in enumerate(self.matches[:max_items], 1):
            lines.append(f"{idx}. {match.symbol} (匹配度：{match.match_score:.2f})")
        if len(self.matches) > max_items:
            lines.append(f"... 还有 {len(self.matches) - max_items} 只")
        return "\n".join(lines)

    def _format_selection_text(self, max_items: int = 20) -> str:
        """格式化超短线选股报告。"""
        lines = [
            f"# {self.strategy_name}报告",
            "",
            f"- 分析日期：{self.created_at.strftime('%Y年%m月%d日')}",
            f"- 数据时间：{self.data_time}",
            f"- 数据来源：{self.data_source}",
            "",
        ]

        if self.market_summary:
            lines.append("## 市场环境")
            lines.append(self.market_summary)
            lines.append("")

        if self.vetoed:
            lines.append(f"## 一票否决（共 {len(self.vetoed)} 只）")
            for idx, item in enumerate(self.vetoed[:max_items], 1):
                lines.append(
                    f"{idx}. {item.symbol} {item.name} | "
                    f"最新价：{item.price:.2f} 涨跌：{item.change_pct:+.2f}% "
                    f"成交额：{item.turnover:.2f}亿"
                )
                lines.append(f"   原因：{'；'.join(item.vetos)}")
            if len(self.vetoed) > max_items:
                lines.append(f"... 还有 {len(self.vetoed) - max_items} 只")
            lines.append("")

        if self.qualified:
            lines.append(f"## 推荐候选（评分≥85分，共 {len(self.qualified)} 只）")
            for idx, item in enumerate(self.qualified[:max_items], 1):
                lines.append(
                    f"{idx}. {item.symbol} {item.name} | 总分：{item.match_score:.0f} | "
                    f"最新价：{item.price:.2f} 涨跌：{item.change_pct:+.2f}% "
                    f"成交额：{item.turnover:.2f}亿"
                )
                lines.append(f"   分项：{item.score_details}")
                lines.append(f"   亮点：{' | '.join(item.notes[:3])}")
            lines.append("")
        else:
            lines.append("## 推荐候选")
            lines.append("无评分≥85分的股票。")
            lines.append("")

        if self.watch_list:
            lines.append(f"## 观察股（60-84分，共 {len(self.watch_list)} 只）")
            for idx, item in enumerate(self.watch_list[:max_items], 1):
                lines.append(
                    f"{idx}. {item.symbol} {item.name} | 总分：{item.match_score:.0f} | "
                    f"最新价：{item.price:.2f} 涨跌：{item.change_pct:+.2f}% "
                    f"成交额：{item.turnover:.2f}亿"
                )
            if len(self.watch_list) > max_items:
                lines.append(f"... 还有 {len(self.watch_list) - max_items} 只")
            lines.append("")

        if self.final_conclusion:
            lines.append("## 最终结论")
            lines.append(self.final_conclusion)
            lines.append("")

        lines.append(
            "> ⚠️ AI生成，不构成投资建议。股市有风险，投资需谨慎。"
        )
        return "\n".join(lines)
