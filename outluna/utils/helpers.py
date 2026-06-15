"""工具函数。"""

from pathlib import Path


def ensure_dirs(*paths: Path) -> None:
    """确保目录存在。"""
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)


def format_number(value: float | int | None, decimals: int = 2) -> str:
    """格式化数字。"""
    if value is None:
        return "N/A"
    return f"{value:,.{decimals}f}"


def safe_divide(a: float, b: float, default: float = 0.0) -> float:
    """安全除法。"""
    if b == 0:
        return default
    return a / b
