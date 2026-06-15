"""AstrBot Star 插件入口。"""

from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, filter

from outluna.engine import OutLunaEngine


class OutLunaPlugin(star.Star):
    """OutLuna 投资助手 AstrBot 插件。"""

    def __init__(self, context: star.Context) -> None:
        self.context = context
        self.engine = OutLunaEngine()

    async def initialize(self) -> None:
        """插件初始化。"""
        await self.engine.initialize()

    @filter.command("scan")
    async def scan_stocks(self, event: AstrMessageEvent, strategy_name: str = "十字星"):
        """执行策略扫描。用法：/scan [策略名]"""
        await event.send("正在执行策略扫描，请稍候...")
        try:
            text = await self.engine.scan(strategy_name)
            await event.send(text)
        except Exception as exc:
            await event.send(f"扫描失败：{exc}")

    @filter.command("analyze")
    async def analyze_stock(self, event: AstrMessageEvent, symbol: str):
        """分析指定股票。用法：/analyze 600519"""
        await event.send(f"正在分析 {symbol}，请稍候...")
        try:
            text = await self.engine.analyze(symbol)
            await event.send(text)
        except Exception as exc:
            await event.send(f"分析失败：{exc}")

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
        await event.send(text)

    @filter.command("compare")
    async def compare_reports(self, event: AstrMessageEvent, id1: str = "", id2: str = ""):
        """对比两份报告。用法：/compare <id1> <id2>"""
        if not id1 or not id2:
            await event.send("用法：/compare <id1> <id2>")
            return
        text = await self.engine.compare_reports(id1, id2)
        await event.send(text)
