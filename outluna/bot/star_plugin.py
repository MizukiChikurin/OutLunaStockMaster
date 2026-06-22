"""AstrBot Star 插件入口。"""

import sys
from pathlib import Path

# 当插件被 AstrBot 以独立目录加载时，将项目根目录加入 sys.path，
# 确保可以从 outluna 包导入引擎与命令处理逻辑。
_plugin_dir = Path(__file__).parent
_project_root = _plugin_dir.parent.parent
if _project_root.exists() and str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from astrbot.api import star  # type: ignore[import-not-found]
from astrbot.api.event import AstrMessageEvent, filter  # type: ignore[import-not-found]

from outluna.bot.commands import CommandHandler
from outluna.bot.formatter import MessageFormatter
from outluna.engine import OutLunaEngine


class OutLunaPlugin(star.Star):
    """OutLuna 投资助手 AstrBot 插件。"""

    def __init__(self, context: star.Context) -> None:
        self.context = context
        self.engine = OutLunaEngine()
        self.handler = CommandHandler(self.engine)
        self.formatter = MessageFormatter()

    async def initialize(self) -> None:
        """插件初始化。"""
        await self.engine.initialize()

    @filter.command("scan")
    async def scan_stocks(self, event: AstrMessageEvent, strategy_name: str = "十字星"):
        """执行策略扫描。用法：/scan [策略名]"""
        await event.send("正在执行策略扫描，请稍候...")
        try:
            text = await self.engine.scan(strategy_name)
            await event.send(self.formatter.truncate(text))
        except Exception as exc:
            await event.send(f"扫描失败：{exc}")

    @filter.command("analyze")
    async def analyze_stock(self, event: AstrMessageEvent, symbol: str):
        """分析指定股票。用法：/analyze 600519"""
        await event.send(f"正在分析 {symbol}，请稍候...")
        try:
            text = await self.engine.analyze(symbol)
            await event.send(self.formatter.truncate(text))
        except Exception as exc:
            await event.send(f"分析失败：{exc}")

    @filter.command("backtest")
    async def run_backtest(self, event: AstrMessageEvent, strategy_name: str, days: int = 90):
        """执行策略回测。用法：/backtest 十字星 90"""
        await event.send(f"正在执行 {strategy_name} 策略回测（近 {days} 天），请稍候...")
        try:
            text = await self.engine.backtest(strategy_name, days)
            await event.send(self.formatter.truncate(text))
        except Exception as exc:
            await event.send(f"回测失败：{exc}")

    @filter.command("strategy")
    async def list_strategies(self, event: AstrMessageEvent):
        """列出可用策略"""
        text = await self.engine.list_strategies()
        await event.send(text)

    @filter.command("report")
    async def get_report(self, event: AstrMessageEvent, report_id: str = ""):
        """查看报告。用法：/report [报告ID]"""
        if not report_id:
            text = await self.engine.list_reports()
        else:
            text = await self.engine.get_report(report_id)
        await event.send(self.formatter.truncate(text))

    @filter.command("compare")
    async def compare_reports(self, event: AstrMessageEvent, id1: str = "", id2: str = ""):
        """对比两份报告。用法：/compare <id1> <id2>"""
        if not id1 or not id2:
            await event.send("用法：/compare <id1> <id2>")
            return
        text = await self.engine.compare_reports(id1, id2)
        await event.send(self.formatter.truncate(text))
