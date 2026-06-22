"""输入参数校验工具。

为聊天机器人命令与引擎接口提供白名单校验，防止路径遍历、命令注入与参数滥用。
"""

from __future__ import annotations

import re


class ValidationError(ValueError):
    """参数校验失败异常。"""


class InputValidator:
    """命令参数校验器。

    所有校验方法在校验通过时返回清理后的值，失败时抛出 ``ValidationError``。
    """

    # 报告ID白名单：仅允许字母、数字、下划线、连字符，防止路径遍历
    _REPORT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

    # 策略名白名单：允许中英文、数字、下划线、连字符
    _STRATEGY_NAME_PATTERN = re.compile(r"^[\w\u4e00-\u9fa5-]+$")

    # 股票代码白名单：允许字母、数字、点号（用于交易所后缀）
    _SYMBOL_PATTERN = re.compile(r"^[A-Za-z0-9.]+$")

    @classmethod
    def validate_report_id(cls, report_id: str) -> str:
        """校验报告ID，防止路径遍历。"""
        if not report_id or not isinstance(report_id, str):
            raise ValidationError("报告ID不能为空")
        report_id = report_id.strip()
        if not cls._REPORT_ID_PATTERN.match(report_id):
            raise ValidationError(f"报告ID包含非法字符：{report_id}")
        return report_id

    @classmethod
    def validate_strategy_name(cls, strategy_name: str) -> str:
        """校验策略名，防止命令注入。"""
        if not strategy_name or not isinstance(strategy_name, str):
            raise ValidationError("策略名不能为空")
        strategy_name = strategy_name.strip()
        if not cls._STRATEGY_NAME_PATTERN.match(strategy_name):
            raise ValidationError(f"策略名包含非法字符：{strategy_name}")
        return strategy_name

    @classmethod
    def validate_symbol(cls, symbol: str) -> str:
        """校验股票代码格式。"""
        if not symbol or not isinstance(symbol, str):
            raise ValidationError("股票代码不能为空")
        symbol = symbol.strip().upper()
        if not cls._SYMBOL_PATTERN.match(symbol):
            raise ValidationError(f"股票代码包含非法字符：{symbol}")
        return symbol

    @classmethod
    def validate_symbols(cls, symbols: list[str]) -> list[str]:
        """批量校验股票代码。"""
        if not symbols:
            return []
        return [cls.validate_symbol(s) for s in symbols]

    @classmethod
    def validate_days(cls, days: int, min_days: int = 1, max_days: int = 3650) -> int:
        """校验回测天数范围。"""
        try:
            days = int(days)
        except (TypeError, ValueError) as exc:
            raise ValidationError("回测天数必须为整数") from exc
        if days < min_days or days > max_days:
            raise ValidationError(f"回测天数需在 {min_days}~{max_days} 之间")
        return days

    @classmethod
    def validate_max_candidates(cls, max_candidates: int | None, upper_bound: int = 10000) -> int | None:
        """校验候选股票数量上限。"""
        if max_candidates is None:
            return None
        try:
            value = int(max_candidates)
        except (TypeError, ValueError) as exc:
            raise ValidationError("候选数量上限必须为整数") from exc
        if value < 1 or value > upper_bound:
            raise ValidationError(f"候选数量上限需在 1~{upper_bound} 之间")
        return value


def validate_report_id(report_id: str) -> str:
    """便捷函数：校验报告ID。"""
    return InputValidator.validate_report_id(report_id)


def validate_strategy_name(strategy_name: str) -> str:
    """便捷函数：校验策略名。"""
    return InputValidator.validate_strategy_name(strategy_name)


def validate_symbol(symbol: str) -> str:
    """便捷函数：校验股票代码。"""
    return InputValidator.validate_symbol(symbol)
