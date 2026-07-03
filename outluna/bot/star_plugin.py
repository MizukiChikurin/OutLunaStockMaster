"""AstrBot Star 插件入口。

本文件有两种使用场景：
1. 作为项目源码中的 ``outluna/bot/star_plugin.py`` 被其他模块导入。
2. 被 ``scripts/build_plugin.py`` 复制为插件根目录的 ``main.py``，由 AstrBot 直接加载。

因此 ``sys.path`` 注入逻辑需要兼容两种路径布局：
- 源码布局：本文件位于 ``outluna/bot/``，项目根目录是 ``Path(__file__).parent.parent.parent``。
- 插件布局：本文件位于插件根目录，``outluna`` 包是本目录的子目录。
"""

import sys
from pathlib import Path

_plugin_dir = Path(__file__).parent

# 自动判断当前路径布局并注入正确的项目根目录。
#
# 源码布局：本文件位于 outluna/bot/，项目根目录是 _plugin_dir.parent.parent。
#   此时需要把项目根目录加入 sys.path，才能从项目根目录导入 outluna。
#
# 插件布局：本文件被复制为插件根目录的 main.py，outluna 包是插件根目录的子目录。
#   此时需要把插件根目录（即 _plugin_dir）加入 sys.path，才能导入 outluna。
if (_plugin_dir / "outluna").exists():
    # 插件布局
    _project_root = _plugin_dir
else:
    # 源码布局
    _project_root = _plugin_dir.parent.parent

if _project_root.exists() and str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# 强制清除可能已缓存的 outluna 模块，确保 AstrBot 加载/重载插件时使用插件自带的最新代码。
# 否则 Python 可能复用旧的 sys.modules 条目，导致代码更新不生效。
for _module_name in list(sys.modules.keys()):
    if _module_name == "outluna" or _module_name.startswith("outluna."):
        del sys.modules[_module_name]

from astrbot.api import star  # type: ignore[import-not-found]
from astrbot.api.event import (  # type: ignore[import-not-found]
    AstrMessageEvent,
    MessageChain,  # type: ignore[import-not-found]
    filter,
)

import outluna
from outluna.bot.astrbot_llm_provider import AstrBotLLMProvider
from outluna.bot.commands import CommandHandler
from outluna.bot.formatter import MessageFormatter
from outluna.engine import OutLunaEngine
from outluna.report.generator import ReportGenerator, ReportStorage
from outluna.utils.logger import setup_logging

logger = setup_logging()


def _plugin_data_dir() -> Path:
    """获取插件数据目录（AstrBot 场景下为插件根目录下的 data/）。"""
    plugin_root = Path(__file__).parent
    return plugin_root / "data"


def _message(text: str) -> MessageChain:

    """把普通文本包装为 AstrBot 兼容的 MessageChain 消息链。

    同时清理 Kimi 数据源返回中无法解码的替换字符（\ufffd），
    避免部分消息平台/前端因非法字节而显示为空。
    """
    cleaned = text.replace("\ufffd", "")
    return MessageChain().message(cleaned)


class OutLunaPlugin(star.Star):
    """OutLuna 投资助手 AstrBot 插件。"""

    def __init__(self, context: star.Context) -> None:
        super().__init__(context)
        self.context = context
        # AstrBot 场景下将报告保存到插件自己的 data/reports 目录
        data_dir = _plugin_data_dir()
        report_dir = data_dir / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_storage = ReportStorage(report_dir=report_dir)
        # AstrBot 场景下复用其已配置的 LLM
        self.engine = OutLunaEngine(
            llm_provider=None,
        )
        self.engine.report_generator = ReportGenerator(storage=report_storage)
        self.handler = CommandHandler(self.engine)
        self.formatter = MessageFormatter()

    async def initialize(self) -> None:
        """插件初始化。"""
        logger.info(
            f"OutLuna 插件初始化，版本：{outluna.__version__}，路径：{Path(__file__).parent}"
        )
        await self.engine.initialize()

    def _get_llm_provider(self, event: AstrMessageEvent) -> AstrBotLLMProvider:
        """为当前事件创建 AstrBot LLM Provider。"""
        return AstrBotLLMProvider(self.context, event)

    @filter.command("scan")
    async def scan_stocks(self, event: AstrMessageEvent, strategy_name: str = "十字星"):
        """执行策略扫描。用法：/scan [策略名]"""
        await event.send(_message("正在执行策略扫描，请稍候..."))
        try:
            text = await self.engine.scan(strategy_name)
            await event.send(_message(self.formatter.truncate(text)))
        except Exception as exc:
            await event.send(_message(f"扫描失败：{exc}"))

    @filter.command("选股")
    async def select_stocks(self, event: AstrMessageEvent, requirements: str = ""):
        """执行用户自定义选股。用法：/选股 <选股要求>"""
        await event.send(_message("正在根据您的要求执行选股，请稍候..."))
        try:
            llm_provider = self._get_llm_provider(event)
            logger.info(f"/选股 使用 AstrBot LLM Provider，可用：{llm_provider.available}")
            self.engine.llm_provider = llm_provider
            text = await self.engine.select_stocks(requirements)
            await event.send(_message(self.formatter.truncate(text)))
        except Exception as exc:
            await event.send(_message(f"选股失败：{exc}"))

    @filter.command("analyze")
    async def analyze_stock(self, event: AstrMessageEvent, symbol: str):
        """分析指定股票。用法：/analyze 600519"""
        await event.send(_message(f"正在分析 {symbol}，请稍候..."))
        try:
            text = await self.engine.analyze(symbol)
            await event.send(_message(self.formatter.truncate(text)))
        except Exception as exc:
            await event.send(_message(f"分析失败：{exc}"))

    @filter.command("backtest")
    async def run_backtest(self, event: AstrMessageEvent, strategy_name: str, days: int = 90):
        """执行策略回测。用法：/backtest 十字星 90"""
        await event.send(_message(f"正在执行 {strategy_name} 策略回测（近 {days} 天），请稍候..."))
        try:
            text = await self.engine.backtest(strategy_name, days)
            await event.send(_message(self.formatter.truncate(text)))
        except Exception as exc:
            await event.send(_message(f"回测失败：{exc}"))

    @filter.command("strategy")
    async def list_strategies(self, event: AstrMessageEvent):
        """列出可用策略"""
        text = await self.engine.list_strategies()
        await event.send(_message(text))

    @filter.command("report")
    async def get_report(self, event: AstrMessageEvent, report_id: str = ""):
        """查看报告。用法：/report [报告ID]"""
        if not report_id:
            text = await self.engine.list_reports()
        else:
            text = await self.engine.get_report(report_id)
        await event.send(_message(self.formatter.truncate(text)))

    @filter.command("compare")
    async def compare_reports(self, event: AstrMessageEvent, id1: str = "", id2: str = ""):
        """对比两份报告。用法：/compare <id1> <id2>"""
        if not id1 or not id2:
            await event.send(_message("用法：/compare <id1> <id2>"))
            return
        text = await self.engine.compare_reports(id1, id2)
        await event.send(_message(self.formatter.truncate(text)))
