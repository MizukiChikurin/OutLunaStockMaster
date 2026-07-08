"""AstrBot Star 插件入口。

本文件有两种使用场景：
1. 作为项目源码中的 ``outluna/bot/star_plugin.py`` 被其他模块导入。
2. 被 ``scripts/build_plugin.py`` 复制为插件根目录的 ``main.py``，由 AstrBot 直接加载。

因此 ``sys.path`` 注入逻辑需要兼容两种路径布局：
- 源码布局：本文件位于 ``outluna/bot/``，项目根目录是 ``Path(__file__).parent.parent.parent``。
- 插件布局：本文件位于插件根目录，``outluna`` 包是本目录的子目录。
"""

import asyncio
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
from outluna.bot.plugin_config import apply_plugin_config
from outluna.config import settings
from outluna.data.providers.kimi_api.models import OAuthUnauthorizedError
from outluna.data.providers.kimi_api.oauth import KimiOAuthClient
from outluna.data.providers.kimi_api.storage import KimiCredentialStore
from outluna.engine import OutLunaEngine
from outluna.report.generator import ReportGenerator, ReportStorage
from outluna.utils.logger import setup_logging

logger = setup_logging()


def _get_kimi_oauth_client() -> KimiOAuthClient:
    """获取内置的 Kimi OAuth 客户端。"""
    store = KimiCredentialStore()
    return KimiOAuthClient(store)


def _plugin_data_dir() -> Path:
    """获取插件数据目录。

    源码布局下为项目根目录 data/，插件布局下为插件根目录 data/。
    """
    return _project_root / "data"


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
        # AstrBot 场景下将报告保存到插件自己的 data/tasks 目录
        data_dir = _plugin_data_dir()
        tasks_dir = data_dir / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        # 先加载插件私有配置，覆盖全局 settings（如 prefer_kimi_api）
        apply_plugin_config(data_dir, settings)
        report_storage = ReportStorage(base_dir=tasks_dir)
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

    @filter.command("test_kimi")
    async def test_kimi(self, event: AstrMessageEvent) -> None:
        """测试 Kimi API 数据源。用法：/test_kimi"""
        provider = self.engine.gateway.providers.get("kimi_api")
        if provider is None:
            await event.send(_message("kimi_api 提供商未启用，请检查 data/outluna_config.json 中 prefer_kimi_api 是否为 true。"))
            return
        method = getattr(provider, "test_realtime_price", None)
        if not callable(method):
            await event.send(_message("当前 kim_api 提供商不支持诊断接口。"))
            return
        await event.send(_message("正在测试 Kimi API（000001.SZ realtime_price），请稍候..."))
        try:
            text, df = method(["000001.SZ"])
            rows, cols = len(df), len(df.columns)
            preview = df.head(3).to_markdown(index=False) if rows else "无数据"
            msg = (
                "Kimi API 诊断结果（000001.SZ realtime_price）：\n\n"
                f"[原始响应]\n{text[:1200]}{'...' if len(text) > 1200 else ''}\n\n"
                f"[解析结果] {rows} 行 × {cols} 列\n"
                f"列名：{list(df.columns)}\n\n"
                f"[前 3 行]\n{preview}"
            )
            await event.send(_message(msg))
        except Exception as exc:
            await event.send(_message(f"Kimi API 测试失败：{exc}"))

    async def _refresh_kimi_credentials(self, event: AstrMessageEvent, error_message: str) -> None:
        """检测到 Kimi 401 凭证错误时，使用内置 OAuth 客户端自动恢复。

        1. 先尝试刷新已有 token；
        2. 刷新失败则发起设备流登录，向用户发送授权链接并轮询等待；
        3. 对用户表现为："正在等待 LLM 生成回复"，期间显示授权链接。
        """
        logger.warning(f"Kimi 凭证过期：{error_message}")
        await event.send(
            _message("检测到 Kimi 数据源凭证已过期（401），正在自动恢复凭证，请稍候...")
        )

        oauth = _get_kimi_oauth_client()

        # 第一步：尝试刷新已有 token
        try:
            await asyncio.to_thread(oauth.ensure_fresh)
            await event.send(_message("Kimi 凭证已自动刷新，正在继续处理..."))
            return
        except OAuthUnauthorizedError:
            logger.info("Kimi token 已失效，进入设备登录流程")
        except Exception as exc:
            logger.warning(f"Kimi token 自动刷新失败：{exc}")

        # 第二步：发起设备流登录（群聊仅限管理员，私聊或 webchat 平台直接允许）
        is_private_chat = getattr(event, "is_private_chat", lambda: False)()
        is_admin = getattr(event, "is_admin", lambda: False)()
        platform_id = ""
        try:
            platform_id = getattr(event, "get_platform_id", lambda: "")()
        except Exception:
            platform_id = ""
        is_webchat = platform_id == "webchat"
        if not is_admin and not is_private_chat and not is_webchat:
            await event.send(
                _message(
                    "Kimi 数据源凭证已过期，请让管理员发送 /选股 触发授权登录后重试。"
                )
            )
            return

        await event.send(
            _message("自动刷新 token 失败，正在发起 Kimi 登录流程，请查看授权链接...")
        )
        try:
            auth = await asyncio.to_thread(oauth.request_device_authorization)
            link = auth.verification_uri_complete or auth.verification_uri
            expires_in = auth.expires_in or 900
            await event.send(
                _message(
                    f"请点击下方链接授权 Kimi 数据源：\n{link}\n"
                    f"授权码：{auth.user_code}\n"
                    f"请在 {expires_in} 秒内完成授权。"
                )
            )

            # 最多等待 120 秒，避免长期占用聊天命令协程
            max_wait = min(expires_in, 120)
            deadline = asyncio.get_event_loop().time() + max_wait
            while asyncio.get_event_loop().time() < deadline:
                result = await asyncio.to_thread(oauth.poll_device_token, auth.device_code)
                if result.kind == "success" and result.token is not None:
                    device_id = await asyncio.to_thread(oauth.store.get_device_id)
                    await asyncio.to_thread(
                        oauth.store.save_login_token,
                        result.token,
                        account_id="default",
                        device_id=device_id,
                        session_id=auth.device_code,
                    )
                    await event.send(_message("Kimi 授权成功，正在继续处理..."))
                    return
                if result.kind == "expired":
                    await event.send(_message("授权链接已过期，请重新发送 /选股 触发登录。"))
                    return
                if result.kind == "denied":
                    await event.send(_message("授权被拒绝，请重新发送 /选股 触发登录。"))
                    return
                await asyncio.sleep(auth.interval)

            await event.send(_message("等待授权超时，请重新发送 /选股 触发登录。"))
        except Exception as exc:
            logger.warning(f"自动发起 Kimi 登录失败：{exc}")
            await event.send(
                _message(
                    "自动恢复 Kimi 凭证失败。请检查网络或让管理员手动配置 Kimi 凭证后重试。"
                )
            )

    @filter.command("选股")
    async def select_stocks(self, event: AstrMessageEvent, requirements: str = ""):
        """执行用户自定义选股。用法：/选股 <选股要求>"""
        await event.send(_message("正在根据您的要求执行选股，请稍候..."))
        try:
            llm_provider = self._get_llm_provider(event)
            logger.info(f"/选股 使用 AstrBot LLM Provider，可用：{llm_provider.available}")
            self.engine.llm_provider = llm_provider
            self.engine.on_kimi_auth_error = lambda msg: self._refresh_kimi_credentials(event, msg)
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
