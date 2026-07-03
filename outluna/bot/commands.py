"""AstrBot 命令处理层。

将聊天指令的解析与业务调用解耦，便于在 CLI 与 AstrBot 插件之间复用。
"""

from __future__ import annotations

from outluna.engine import OutLunaEngine
from outluna.utils.logger import setup_logging
from outluna.utils.validation import ValidationError

logger = setup_logging()


class CommandHandler:
    """命令处理器。

    统一处理用户输入的文本命令，并返回待发送的文本结果。
    """

    def __init__(self, engine: OutLunaEngine | None = None):
        """初始化命令处理器。"""
        self.engine = engine or OutLunaEngine()

    async def handle(self, text: str) -> str:
        """解析并执行单条命令文本。

        Args:
            text: 用户输入的原始文本，例如 "/scan 十字星"。

        Returns:
            待回复给用户的消息文本。
        """
        text = text.strip()
        if not text:
            return "请输入命令。可用命令：/scan、/analyze、/backtest、/report、/compare、/strategy"

        if not text.startswith("/"):
            return f"未知输入：{text}\n请以 / 开头输入命令。"

        parts = text[1:].split(maxsplit=2)
        command = parts[0].lower()
        args = parts[1:]

        try:
            if command == "scan":
                return await self._scan(args)
            if command == "选股":
                return await self._select_stocks(args)
            if command == "analyze":
                return await self._analyze(args)
            if command == "backtest":
                return await self._backtest(args)
            if command == "report":
                return await self._report(args)
            if command == "compare":
                return await self._compare(args)
            if command == "strategy":
                return await self.engine.list_strategies()
            if command == "help":
                return self._help()
            return f"未知命令：/{command}\n{self._help()}"
        except ValidationError as exc:
            logger.warning(f"参数校验失败：/{command} {args} - {exc}")
            return f"参数错误：{exc}"
        except Exception as exc:
            logger.exception(f"命令执行失败：/{command} {args}")
            return f"执行失败：{exc}"

    async def _scan(self, args: list[str]) -> str:
        """处理 /scan [策略名] 命令。"""
        strategy = args[0] if args else "十字星"
        return await self.engine.scan(strategy)

    async def _select_stocks(self, args: list[str]) -> str:
        """处理 /选股 [选股要求文本] 命令。"""
        requirements = " ".join(args) if args else ""
        return await self.engine.select_stocks(requirements)

    async def _analyze(self, args: list[str]) -> str:
        """处理 /analyze <代码> 命令。"""
        if not args:
            return "用法：/analyze <股票代码>\n例如：/analyze 600519"
        symbol = args[0].strip()
        return await self.engine.analyze(symbol)

    async def _backtest(self, args: list[str]) -> str:
        """处理 /backtest <策略名> [天数] 命令。"""
        if not args:
            return "用法：/backtest <策略名> [天数]\n例如：/backtest 十字星 90"
        strategy = args[0]
        days = 90
        if len(args) > 1:
            try:
                days = int(args[1])
            except ValueError:
                return "天数必须为整数。"
        return await self.engine.backtest(strategy, days)

    async def _report(self, args: list[str]) -> str:
        """处理 /report [报告ID] 命令。"""
        if not args:
            return await self.engine.list_reports()
        return await self.engine.get_report(args[0])

    async def _compare(self, args: list[str]) -> str:
        """处理 /compare <id1> <id2> 命令。"""
        if len(args) < 2:
            return "用法：/compare <报告ID1> <报告ID2>"
        return await self.engine.compare_reports(args[0], args[1])

    def _help(self) -> str:
        """返回帮助文本。"""
        return (
            "可用命令：\n"
            "/scan [策略名] - 执行策略扫描\n"
            "/选股 <要求> - 根据用户提供的选股要求执行筛选\n"
            "/analyze <代码> - 分析指定股票\n"
            "/backtest <策略名> [天数] - 策略回测\n"
            "/report [报告ID] - 查看报告\n"
            "/compare <id1> <id2> - 对比报告\n"
            "/strategy - 列出可用策略\n"
            "/help - 显示帮助"
        )
