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
            text: 用户输入的原始文本，例如 "/分析 600519"。

        Returns:
            待回复给用户的消息文本。
        """
        text = text.strip()
        if not text:
            return "请输入命令。可用命令：/选股、/分析、/report、/观察、/放弃观察、/分析股池、/追踪股池、/help"

        if not text.startswith("/"):
            return f"未知输入：{text}\n请以 / 开头输入命令。"

        parts = text[1:].split(maxsplit=2)
        command = parts[0].lower()
        args = parts[1:]

        try:
            if command == "选股":
                return await self._select_stocks(args)
            if command == "固定策略":
                return await self._select_stocks_by_preset(args)
            if command == "分析":
                return await self._analyze(args)
            if command == "report":
                return await self._report(args)
            if command == "观察":
                return await self._watch(args)
            if command == "放弃观察":
                return await self._unwatch(args)
            if command == "分析股池":
                return await self._analyze_watchlist(args)
            if command == "追踪股池":
                return await self._track_watchlist(args)
            if command == "help":
                return self._help()
            return f"未知命令：/{command}\n{self._help()}"
        except ValidationError as exc:
            logger.warning(f"参数校验失败：/{command} {args} - {exc}")
            return f"参数错误：{exc}"
        except Exception as exc:
            logger.exception(f"命令执行失败：/{command} {args}")
            return f"执行失败：{exc}"

    async def _select_stocks(self, args: list[str]) -> str:
        """处理 /选股 [选股要求文本] 命令。"""
        requirements = " ".join(args) if args else ""
        result = await self.engine.select_stocks(requirements)
        return result.text

    async def _select_stocks_by_preset(self, args: list[str]) -> str:
        """处理 /固定策略 <策略名称> 命令。"""
        preset_name = args[0] if args else ""
        result = await self.engine.select_stocks_by_preset(preset_name)
        return result.text

    async def _analyze(self, args: list[str]) -> str:
        """处理 /分析 <代码> 命令。"""
        if not args:
            return "用法：/分析 <股票代码>\n例如：/分析 600519"
        symbol = args[0].strip()
        return await self.engine.analyze(symbol)

    async def _report(self, args: list[str]) -> str:
        """处理 /report [报告ID] 命令。"""
        if not args:
            return await self.engine.list_reports()
        return await self.engine.get_report(args[0])

    async def _watch(self, args: list[str]) -> str:
        """处理 /观察 <股票代码> 命令。"""
        if not args:
            return "用法：/观察 <股票代码>\n例如：/观察 600519"
        return await self.engine.add_watch(args[0])

    async def _unwatch(self, args: list[str]) -> str:
        """处理 /放弃观察 <股票代码> 命令。"""
        if not args:
            return "用法：/放弃观察 <股票代码>\n例如：/放弃观察 600519"
        return await self.engine.remove_watch(args[0])

    async def _analyze_watchlist(self, args: list[str]) -> str:
        """处理 /分析股池 命令。"""
        return await self.engine.analyze_watchlist()

    async def _track_watchlist(self, args: list[str]) -> str:
        """处理 /追踪股池 命令。"""
        result = await self.engine.track_watchlist()
        return result.text

    def _help(self) -> str:
        """返回帮助文本。"""
        return (
            "可用命令：\n"
            "/选股 <要求> - 根据用户提供的选股要求执行筛选\n"
            "/固定策略 <策略名称> - 根据预设策略文件执行选股\n"
            "/分析 <代码> - 分析指定股票\n"
            "/report [报告ID] - 查看报告\n"
            "/观察 <代码> - 将股票加入自选股池\n"
            "/放弃观察 <代码> - 将股票从自选股池移除\n"
            "/分析股池 - 分析自选股池中的所有股票\n"
            "/追踪股池 - 查看自选股池最近开盘价与收盘价\n"
            "/help - 显示帮助"
        )
