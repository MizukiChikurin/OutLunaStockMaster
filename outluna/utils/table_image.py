"""将表格数据渲染为 HTML 图片的辅助模块。

本模块提供 Jinja2 模板加载、数据构造函数以及本地 HTML 渲染能力，
供 AstrBot 入口调用 AstrBot 内置 T2I 服务生成图片表格。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from outluna.data.models import ScanReport


def _template_dir() -> Path:
    """返回模板文件所在目录。"""
    return Path(__file__).with_name("templates")


def get_template_string() -> str:
    """加载并返回 table.html 模板原始字符串。"""
    template_path = _template_dir() / "table.html"
    return template_path.read_text(encoding="utf-8")


def render_table_html(data: dict[str, Any]) -> str:
    """使用本地 Jinja2 环境将模板数据渲染为 HTML 字符串。

    Args:
        data: 包含 title、headers、rows、footer 的字典。

    Returns:
        渲染后的 HTML 字符串。
    """
    env = Environment(
        loader=FileSystemLoader(_template_dir()),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("table.html")
    return template.render(data)


def _format_price(value: Any) -> str:
    """将价格格式化为两位小数，失败时返回原字符串或 '-'。"""
    try:
        return f"{float(value):.2f}"
    except (ValueError, TypeError):
        return str(value) if value is not None else "-"


def _change_class(open_price: Any, close_price: Any) -> str:
    """根据收盘价与开盘价比较返回涨跌 CSS 类。

    A 股习惯：上涨为红色（up 类），下跌为绿色（down 类），
    相等或数据异常时使用默认颜色。
    """
    try:
        open_val = float(open_price)
        close_val = float(close_price)
        if close_val > open_val:
            return "up"
        if close_val < open_val:
            return "down"
    except (ValueError, TypeError):
        pass
    return ""


def _pct_class(value: float | None) -> str:
    """根据涨跌幅数值返回 CSS 类。"""
    if value is None:
        return ""
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return ""


def _format_turnover(value: Any) -> str:
    """将成交额格式化为 '亿' 单位，失败时返回原字符串。"""
    try:
        return f"{float(value):.2f}亿"
    except (ValueError, TypeError):
        return str(value) if value is not None else "-"


def build_watchlist_data(df: pd.DataFrame, days: int) -> dict[str, Any]:
    """构造自选股池追踪表格的模板数据。

    输入 DataFrame 为宽表格：每行一只股票，首列为"股票名称+股票代码"，
    后续列为 日期1...日期N、今日，每个单元格为"开盘价/收盘价（实时价）"格式。

    Args:
        df: 宽格式 DataFrame，列如 股票、日期1、...、今日。
        days: 展示最近交易日数量，用于生成标题。

    Returns:
        可直接传给 Jinja2 模板的数据字典。
    """
    headers = df.columns.tolist()
    rows: list[list[dict[str, Any]]] = []
    for _, row in df.iterrows():
        cells: list[dict[str, Any]] = []
        for header in headers:
            value = str(row.get(header, ""))
            if header == "股票" or "/" not in value:
                cells.append({"value": value, "class": "", "safe": False})
                continue
            parts = value.split("/")
            if len(parts) != 2:
                cells.append({"value": value, "class": "", "safe": False})
                continue
            open_str, close_str = parts
            cls = ""
            try:
                open_val = float(open_str)
                close_val = float(close_str)
                if close_val > open_val:
                    cls = "up"
                elif close_val < open_val:
                    cls = "down"
            except (ValueError, TypeError):
                pass
            # 开盘价保持默认色，收盘价/实时价按相对开盘价涨跌标红绿
            if cls:
                display = f'{open_str}/<span class="{cls}">{close_str}</span>'
            else:
                display = value
            cells.append({"value": display, "class": "", "safe": True})
        rows.append(cells)
    return {
        "title": f"自选股池追踪（最近 {days} 个交易日 + 今日）",
        "headers": headers,
        "rows": rows,
        "footer": "AI生成，不构成投资建议。股市有风险，投资需谨慎。",
    }


def build_stock_selection_data(report: ScanReport, max_items: int = 20) -> dict[str, Any]:
    """构造选股结果推荐候选表格的模板数据。

    Args:
        report: 选股扫描报告，数据取自 qualified 列表。
        max_items: 最多展示条数，防止图片过长。

    Returns:
        可直接传给 Jinja2 模板的数据字典。
    """
    headers = ["序号", "代码", "名称", "最新价", "涨跌幅", "成交额", "推荐理由"]
    rows: list[list[dict[str, str]]] = []
    for idx, item in enumerate(report.qualified[:max_items], 1):
        reasons: list[str] = []
        if item.vetos:
            reasons.extend(item.vetos)
        if item.notes:
            reasons.extend(item.notes)
        reason_text = " | ".join(reasons[:3]) if reasons else "—"
        change_text = f"{item.change_pct:+.2f}%" if item.change_pct is not None else "—"
        rows.append(
            [
                {"value": str(idx), "class": ""},
                {"value": str(item.symbol), "class": ""},
                {"value": str(item.name or item.symbol), "class": ""},
                {"value": _format_price(item.price), "class": ""},
                {"value": change_text, "class": _pct_class(item.change_pct)},
                {"value": _format_turnover(item.turnover), "class": ""},
                {"value": reason_text, "class": ""},
            ]
        )
    return {
        "title": f"{report.strategy_name} 推荐候选",
        "headers": headers,
        "rows": rows,
        "footer": "AI生成，不构成投资建议。股市有风险，投资需谨慎。",
    }
