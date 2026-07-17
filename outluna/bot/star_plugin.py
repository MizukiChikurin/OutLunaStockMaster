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
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path

import pandas as pd

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
from outluna.bot.auto_config import AutoWorkflowStorage, GroupAutoConfig, is_valid_time
from outluna.bot.auto_scheduler import AutoScheduler
from outluna.bot.commands import CommandHandler
from outluna.bot.formatter import MessageFormatter
from outluna.bot.plugin_config import apply_plugin_config, relocate_settings_paths
from outluna.config import settings
from outluna.data.models import ScanReport
from outluna.data.providers.kimi_api.models import OAuthUnauthorizedError
from outluna.data.providers.kimi_api.oauth import KimiOAuthClient
from outluna.data.providers.kimi_api.storage import KimiCredentialStore
from outluna.data.watchlist import AUTO_KEEP_TRADE_DAYS, WatchlistStorage
from outluna.engine import OutLunaEngine, ReportOutput
from outluna.report.generator import ReportGenerator, ReportStorage
from outluna.strategy.fixed import FixedStrategy
from outluna.utils.logger import setup_logging
from outluna.utils.table_image import (
    build_stock_selection_data,
    build_watchlist_data,
    get_template_string,
)
from outluna.utils.trade_calendar import TradeCalendar

logger = setup_logging()

#: 追踪股池展示的历史交易日数量（与 engine.track_watchlist 默认值保持一致）
WATCHLIST_TRACK_DAYS = 5


def _get_kimi_oauth_client() -> KimiOAuthClient:
    """获取内置的 Kimi OAuth 客户端。"""
    store = KimiCredentialStore()
    return KimiOAuthClient(store)


def _plugin_data_dir() -> Path:
    """获取插件数据目录。

    AstrBot 运行时使用 ``StarTools.get_data_dir('outluna')`` 获取
    ``data/plugin_data/outluna`` 目录，避免插件更新导致数据丢失；
    源码布局或无法获取 AstrBot 目录时回退到本地 ``data/``。
    """
    if (_plugin_dir / "outluna").exists():
        # 插件布局：优先使用 AstrBot 提供的插件数据目录
        try:
            from astrbot.core.star.star_tools import StarTools  # type: ignore[import-not-found]

            return Path(str(StarTools.get_data_dir("outluna")))
        except Exception as exc:
            logger.warning(f"无法获取 AstrBot 插件数据目录，回退到本地 data：{exc}")
            return _plugin_dir / "data"
    # 源码布局
    return _project_root / "data"


def _message(text: str) -> MessageChain:
    """把普通文本包装为 AstrBot 兼容的 MessageChain 消息链。

    同时清理 Kimi 数据源返回中无法解码的替换字符（\ufffd），
    避免部分消息平台/前端因非法字节而显示为空。
    """
    cleaned = text.replace("\ufffd", "")
    return MessageChain().message(cleaned)


def _is_txt_flag(text: str) -> tuple[bool, str]:
    """检测并移除文本中的 --txt 标志。

    Args:
        text: 用户输入的命令参数字符串。

    Returns:
        (是否包含 --txt 标志, 移除标志并压缩空白后的文本)。
    """
    parts = text.split()
    has_flag = False
    cleaned: list[str] = []
    for part in parts:
        if part == "--txt":
            has_flag = True
        else:
            cleaned.append(part)
    return has_flag, " ".join(cleaned)


class OutLunaPlugin(star.Star):
    """OutLuna 投资助手 AstrBot 插件。"""

    def __init__(self, context: star.Context) -> None:
        super().__init__(context)
        self.context = context
        # AstrBot 场景下将所有持久化数据重定向到 AstrBot 插件数据目录
        data_dir = _plugin_data_dir()
        tasks_dir = data_dir / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        # 旧数据目录（插件根目录 data/），用于迁移已有凭证
        old_data_dir = _plugin_dir / "data" if (_plugin_dir / "outluna").exists() else None
        # 先重定位文件路径，再加载插件私有配置覆盖全局 settings
        relocate_settings_paths(data_dir, settings, old_data_dir=old_data_dir)
        apply_plugin_config(data_dir, settings)
        logger.info(f"OutLuna 插件数据目录：{data_dir}")
        logger.info(f"Kimi 凭证文件路径：{settings.db_path.parent / 'kimi_api_credentials.json'}")
        logger.info(f"旧数据目录：{old_data_dir}")
        self.image_tables_enabled = getattr(settings, "image_tables_enabled", True)
        report_storage = ReportStorage(base_dir=tasks_dir)
        # AstrBot 场景下复用其已配置的 LLM
        self.engine = OutLunaEngine(
            llm_provider=None,
        )
        self.engine.report_generator = ReportGenerator(storage=report_storage)
        self.engine.watchlist_storage = WatchlistStorage(data_dir / "watchlist.json")
        self.handler = CommandHandler(self.engine)
        self.formatter = MessageFormatter()
        # 自动化工作流：按群配置存储 + 交易日历 + 定时调度器
        self.auto_storage = AutoWorkflowStorage(data_dir / "auto_workflow.json")
        self.trade_calendar = TradeCalendar(settings.cache_dir / "trade_calendar.json")
        self.scheduler = AutoScheduler(
            storage=self.auto_storage,
            trade_calendar=self.trade_calendar,
            on_track=self._push_track_watchlist,
            on_scan=self._push_auto_scan,
            on_cleanup=self._cleanup_expired_auto,
            state_path=data_dir / "auto_scheduler_state.json",
        )

    async def initialize(self) -> None:
        """插件初始化。"""
        logger.info(
            f"OutLuna 插件初始化，版本：{outluna.__version__}，路径：{Path(__file__).parent}"
        )
        await self.engine.initialize()
        await self.scheduler.start()

    async def terminate(self) -> None:
        """插件禁用或重载时停止自动化调度器。"""
        await self.scheduler.stop()

    def _get_llm_provider(self, event: AstrMessageEvent) -> AstrBotLLMProvider:
        """为当前事件创建 AstrBot LLM Provider。"""
        return AstrBotLLMProvider(self.context, event)

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
                _message("Kimi 数据源凭证已过期，请让管理员发送 /选股 触发授权登录后重试。")
            )
            return

        await event.send(_message("自动刷新 token 失败，正在发起 Kimi 登录流程，请查看授权链接..."))
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
                _message("自动恢复 Kimi 凭证失败。请检查网络或让管理员手动配置 Kimi 凭证后重试。")
            )

    async def _build_table_image(self, template: str, data: dict) -> str | None:
        """调用 AstrBot 内置 T2I 服务将模板与数据渲染为图片。

        Args:
            template: Jinja2 HTML 模板字符串。
            data: 模板渲染所需数据字典。

        Returns:
            本地图片文件路径；渲染不可用或失败时返回 None。
        """
        html_render: Callable[..., Awaitable[str]] | None = getattr(self, "html_render", None)
        if not callable(html_render):
            logger.warning("当前 AstrBot 版本未提供 self.html_render，已回退到文本")
            return None
        try:
            return await html_render(
                template,
                data,
                return_url=False,
                options={"full_page": True, "type": "jpeg", "quality": 90},
            )
        except Exception as exc:
            logger.warning(f"图片表格渲染失败：{exc}")
            return None

    async def _send_message_to(self, umo: str, chain: MessageChain) -> None:
        """向指定会话主动发送消息链，失败时记录日志。"""
        try:
            sent = await self.context.send_message(umo, chain)
            if not sent:
                logger.warning(f"主动消息未送达，未找到匹配平台：{umo}")
        except Exception as exc:
            logger.error(f"主动消息发送失败[{umo}]：{exc}")

    def _make_task_engine(self, umo: str) -> OutLunaEngine:
        """为定时任务构造独立的引擎实例。

        与聊天命令使用的 ``self.engine`` 隔离，避免定时任务与交互命令
        互相覆盖共享的 ``llm_provider`` / ``on_kimi_auth_error`` 状态
        （跨会话串用 provider、授权链接发错群聊）。
        股池存储与报告生成器共享同一持久化后端，数据天然一致。
        """
        engine = OutLunaEngine(
            llm_provider=AstrBotLLMProvider(self.context, unified_msg_origin=umo)
        )
        engine.report_generator = self.engine.report_generator
        engine.watchlist_storage = self.engine.watchlist_storage
        # 定时任务场景无法交互式授权，Kimi 凭证失效时直接失败并推送提示
        engine.on_kimi_auth_error = None
        return engine

    async def _render_watchlist_chain(self, result: ReportOutput, allow_image: bool) -> MessageChain:
        """将追踪股池结果渲染为消息链（图片优先，失败回退文本）。

        命令入口与自动化推送共用的渲染管线；``allow_image`` 为 False 时
        （命令带 --txt 标志）强制文本输出。
        """
        if (
            allow_image
            and self.image_tables_enabled
            and isinstance(result.data, pd.DataFrame)
            and not result.data.empty
        ):
            template = get_template_string()
            data = build_watchlist_data(result.data, WATCHLIST_TRACK_DAYS)
            image_path = await self._build_table_image(template, data)
            if image_path:
                return MessageChain().file_image(image_path)
            return _message("图片生成失败，已发送文本。\n" + self.formatter.truncate(result.text))
        return _message(self.formatter.truncate(result.text))

    async def _render_scan_chain(self, result: ReportOutput, allow_image: bool) -> MessageChain:
        """将选股报告结果渲染为消息链（图片优先，失败回退文本）。

        命令入口与自动化推送共用的渲染管线；``allow_image`` 为 False 时
        （命令带 --txt 标志）强制文本输出。
        """
        if (
            allow_image
            and self.image_tables_enabled
            and isinstance(result.data, ScanReport)
            and result.data.qualified
        ):
            template = get_template_string()
            data = build_stock_selection_data(result.data)
            image_path = await self._build_table_image(template, data)
            if image_path:
                return MessageChain().file_image(image_path)
            return _message("图片生成失败，已发送文本。\n" + self.formatter.truncate(result.text))
        return _message(self.formatter.truncate(result.text))

    async def _push_track_watchlist(self, umo: str) -> None:
        """自动化任务：执行追踪股池并将结果推送到指定群聊。"""
        try:
            result = await self._make_task_engine(umo).track_watchlist()
        except Exception as exc:
            await self._send_message_to(umo, _message(f"自动化追踪股池失败：{exc}"))
            return
        chain = await self._render_watchlist_chain(result, allow_image=True)
        await self._send_message_to(umo, chain)

    async def _push_auto_scan(self, umo: str) -> None:
        """自动化任务：执行固定策略选股，达标股票自动入库并推送结果。

        入库规则：LLM 评分大于等于群配置阈值的股票加入股池（来源标记 auto）；
        已在池中的 auto 股票刷新加入日期（重新计算保留期）；
        手动添加的股票保持原有元数据不变。
        """
        config = self.auto_storage.get_group(umo)
        if not config.strategy:
            await self._send_message_to(
                umo,
                _message("自动化选股未配置策略，已跳过。\n请使用 /自动化 设置 策略 <策略名> 进行配置。"),
            )
            return
        engine = self._make_task_engine(umo)
        try:
            result = await engine.select_stocks_by_preset(config.strategy)
        except Exception as exc:
            await self._send_message_to(umo, _message(f"自动化选股失败（策略：{config.strategy}）：{exc}"))
            return

        # 发送选股结果（图片优先，与 /固定策略 命令共用渲染管线）
        chain = await self._render_scan_chain(result, allow_image=True)
        await self._send_message_to(umo, chain)

        # 达标股票入库并推送入库摘要
        summary = self._collect_qualified_to_watchlist(result.data, config, umo)
        await self._send_message_to(umo, _message(summary))

    def _collect_qualified_to_watchlist(
        self,
        report: ScanReport | None,
        config: GroupAutoConfig,
        umo: str,
    ) -> str:
        """将评分达标的选股结果批量写入自选股池，返回入库摘要文本。"""
        labels: dict[str, str] = {}
        symbols: list[str] = []
        if isinstance(report, ScanReport):
            for item in report.qualified:
                score = item.match_score if item.match_score is not None else 0.0
                if score < config.score_threshold:
                    continue
                symbols.append(item.symbol)
                labels[item.symbol] = f"{item.name or item.symbol}({item.symbol}) {score:.0f}分"
        today = datetime.now().date().isoformat()
        outcome = self.engine.watchlist_storage.upsert_auto_items(symbols, added_date=today)
        added = [labels.get(s, s) for s in outcome["added"]]
        refreshed = [labels.get(s, s) for s in outcome["refreshed"]]
        kept_manual = [labels.get(s, s) for s in outcome["kept_manual"]]
        lines = [
            f"自动选股入库结果（策略：{config.strategy}，阈值：{config.score_threshold:g}分）："
        ]
        if added:
            lines.append(f"新加入股池（保留{AUTO_KEEP_TRADE_DAYS}个交易日）：{'、'.join(added)}")
        if refreshed:
            lines.append(f"重复推荐，已刷新保留期：{'、'.join(refreshed)}")
        if kept_manual:
            lines.append(f"手动添加股票保持不变：{'、'.join(kept_manual)}")
        if not added and not refreshed and not kept_manual:
            lines.append("无评分达标股票，股池未变更。")
        logger.info(f"自动选股入库[{umo}]：新增{len(added)}，刷新{len(refreshed)}，手动保留{len(kept_manual)}")
        return "\n".join(lines)

    async def _cleanup_expired_auto(self) -> None:
        """自动化任务：移除保留期满的自动入库股票，并通知已开启的群聊。"""
        trade_dates = await asyncio.to_thread(self.trade_calendar.get_trade_dates)
        removed = self.engine.watchlist_storage.remove_expired_auto(
            trade_dates, keep_days=AUTO_KEEP_TRADE_DAYS
        )
        if not removed:
            return
        text = (
            f"以下自动入库股票已保留满 {AUTO_KEEP_TRADE_DAYS} 个交易日，"
            f"自动移出股池：{', '.join(removed)}"
        )
        for umo, config in self.auto_storage.list_groups().items():
            if config.enabled:
                await self._send_message_to(umo, _message(text))

    def _format_auto_config(self, umo: str) -> str:
        """格式化当前群聊的自动化配置状态文本。"""
        config = self.auto_storage.get_group(umo)
        presets = FixedStrategy.list_presets()
        lines = [
            "本群自动化工作流配置：",
            f"- 状态：{'已开启' if config.enabled else '已关闭'}",
            f"- 追踪股池推送时间：{'、'.join(config.track_times) if config.track_times else '无（已关闭盘中推送）'}",
            f"- 自动选股时间：{config.scan_time}",
            f"- 选股策略：{config.strategy or '未配置'}",
            f"- 入库评分阈值：{config.score_threshold:g} 分",
            f"- 自动入库股票保留期：{AUTO_KEEP_TRADE_DAYS} 个交易日",
            "",
            "配置命令：",
            "/自动化 开 | /自动化 关",
            "/自动化 设置 推送时间 09:30 13:30（设为 无 可关闭盘中推送）",
            "/自动化 设置 选股时间 16:00",
            f"/自动化 设置 策略 <策略名>（可用：{', '.join(presets) if presets else '无'}）",
            "/自动化 设置 阈值 70",
        ]
        return "\n".join(lines)

    async def _handle_auto_set(self, event: AstrMessageEvent, umo: str, args: list[str]) -> None:
        """处理 /自动化 设置 子命令。"""
        if not args:
            await event.send(_message("用法：/自动化 设置 推送时间|选股时间|策略|阈值 <值>"))
            return
        key = args[0]
        values = args[1:]
        config = self.auto_storage.get_group(umo)

        if key == "推送时间":
            if values == ["无"]:
                config.track_times = []
                self.auto_storage.save_group(umo, config)
                await event.send(_message("追踪股池推送时间已清空，盘中推送已关闭（封盘后选股不受影响）。"))
                return
            if not values or any(not is_valid_time(v) for v in values):
                await event.send(_message("时间格式错误，应为 HH:MM，例如：/自动化 设置 推送时间 09:30 13:30"))
                return
            config.track_times = sorted(set(values))
            self.auto_storage.save_group(umo, config)
            await event.send(_message(f"追踪股池推送时间已更新：{'、'.join(config.track_times)}"))
        elif key == "选股时间":
            if len(values) != 1 or not is_valid_time(values[0]):
                await event.send(_message("时间格式错误，应为 HH:MM，例如：/自动化 设置 选股时间 16:00"))
                return
            config.scan_time = values[0]
            self.auto_storage.save_group(umo, config)
            await event.send(_message(f"自动选股时间已更新：{config.scan_time}"))
        elif key == "策略":
            if len(values) != 1:
                await event.send(_message("用法：/自动化 设置 策略 <策略名>"))
                return
            presets = FixedStrategy.list_presets()
            if values[0] not in presets:
                await event.send(
                    _message(f"策略不存在：{values[0]}\n可用策略：{', '.join(presets) if presets else '无'}")
                )
                return
            config.strategy = values[0]
            self.auto_storage.save_group(umo, config)
            await event.send(_message(f"自动选股策略已更新：{config.strategy}"))
        elif key == "阈值":
            if len(values) != 1:
                await event.send(_message("用法：/自动化 设置 阈值 <0-100的分数>"))
                return
            try:
                threshold = float(values[0])
            except ValueError:
                await event.send(_message("阈值格式错误，应为 0-100 的数字，例如：/自动化 设置 阈值 70"))
                return
            if not 0 <= threshold <= 100:
                await event.send(_message("阈值需在 0-100 之间。"))
                return
            config.score_threshold = threshold
            self.auto_storage.save_group(umo, config)
            await event.send(_message(f"入库评分阈值已更新：{config.score_threshold:g} 分"))
        else:
            await event.send(_message(f"未知设置项：{key}\n支持：推送时间、选股时间、策略、阈值"))

    @filter.command("自动化")
    async def auto_workflow(self, event: AstrMessageEvent, args: str = ""):
        """配置自动化工作流。用法：/自动化 [开|关|设置 ...]"""
        umo = event.unified_msg_origin
        try:
            await self._dispatch_auto_command(event, umo, args)
        except Exception as exc:
            logger.error(f"/自动化 命令执行失败：{exc}")
            await event.send(
                _message(f"自动化配置保存失败：{exc}\n配置文件可能已损坏，请检查数据目录中的备份文件。")
            )

    async def _dispatch_auto_command(
        self, event: AstrMessageEvent, umo: str, args: str
    ) -> None:
        """解析并分发 /自动化 子命令。"""
        parts = args.split()
        if not parts:
            await event.send(_message(self._format_auto_config(umo)))
            return
        sub = parts[0]
        if sub == "开":
            config = self.auto_storage.get_group(umo)
            config.enabled = True
            self.auto_storage.save_group(umo, config)
            await event.send(_message("已开启本群自动化工作流。\n" + self._format_auto_config(umo)))
        elif sub == "关":
            config = self.auto_storage.get_group(umo)
            config.enabled = False
            self.auto_storage.save_group(umo, config)
            await event.send(_message("已关闭本群自动化工作流。"))
        elif sub == "设置":
            await self._handle_auto_set(event, umo, parts[1:])
        else:
            await event.send(_message(f"未知子命令：{sub}\n" + self._format_auto_config(umo)))

    @filter.command("选股")
    async def select_stocks(self, event: AstrMessageEvent, requirements: str = ""):
        """执行用户自定义选股。用法：/选股 <选股要求>"""
        txt_flag, requirements = _is_txt_flag(requirements)
        await event.send(_message("正在根据您的要求执行选股，请稍候..."))
        try:
            llm_provider = self._get_llm_provider(event)
            logger.info(f"/选股 使用 AstrBot LLM Provider，可用：{llm_provider.available}")
            self.engine.llm_provider = llm_provider
            self.engine.on_kimi_auth_error = lambda msg: self._refresh_kimi_credentials(event, msg)
            result = await self.engine.select_stocks(requirements)
            chain = await self._render_scan_chain(result, allow_image=not txt_flag)
            await event.send(chain)
        except Exception as exc:
            await event.send(_message(f"选股失败：{exc}"))

    @filter.command("固定策略")
    async def select_stocks_by_preset(self, event: AstrMessageEvent, preset_name: str = ""):
        """执行固定策略选股。用法：/固定策略 <策略名称>"""
        txt_flag, preset_name = _is_txt_flag(preset_name)
        await event.send(_message("正在执行固定策略选股，请稍候..."))
        try:
            llm_provider = self._get_llm_provider(event)
            logger.info(f"/固定策略 使用 AstrBot LLM Provider，可用：{llm_provider.available}")
            self.engine.llm_provider = llm_provider
            self.engine.on_kimi_auth_error = lambda msg: self._refresh_kimi_credentials(event, msg)
            result = await self.engine.select_stocks_by_preset(preset_name)
            chain = await self._render_scan_chain(result, allow_image=not txt_flag)
            await event.send(chain)
        except Exception as exc:
            await event.send(_message(f"固定策略选股失败：{exc}"))

    @filter.command("分析")
    async def analyze_stock(self, event: AstrMessageEvent, symbol: str):
        """分析指定股票。用法：/分析 600519"""
        await event.send(_message(f"正在分析 {symbol}，请稍候..."))
        try:
            llm_provider = self._get_llm_provider(event)
            logger.info(f"/分析 使用 AstrBot LLM Provider，可用：{llm_provider.available}")
            self.engine.llm_provider = llm_provider
            text = await self.engine.analyze(symbol)
            await event.send(_message(self.formatter.truncate(text)))
        except Exception as exc:
            await event.send(_message(f"分析失败：{exc}"))

    @filter.command("观察")
    async def add_watch(self, event: AstrMessageEvent, symbol: str):
        """将股票加入自选股池。用法：/观察 600519"""
        if not symbol:
            await event.send(_message("用法：/观察 股票代码\n例如：/观察 600519"))
            return
        try:
            text = await self.engine.add_watch(symbol)
            await event.send(_message(text))
        except Exception as exc:
            await event.send(_message(f"加入自选股池失败：{exc}"))

    @filter.command("放弃观察")
    async def remove_watch(self, event: AstrMessageEvent, symbol: str):
        """将股票从自选股池移除。用法：/放弃观察 600519"""
        if not symbol:
            await event.send(_message("用法：/放弃观察 股票代码\n例如：/放弃观察 600519"))
            return
        try:
            text = await self.engine.remove_watch(symbol)
            await event.send(_message(text))
        except Exception as exc:
            await event.send(_message(f"移除自选股池失败：{exc}"))

    @filter.command("分析股池")
    async def analyze_watchlist(self, event: AstrMessageEvent):
        """分析自选股池中的所有股票。用法：/分析股池"""
        await event.send(_message("正在分析自选股池，请稍候..."))
        try:
            llm_provider = self._get_llm_provider(event)
            logger.info(f"/分析股池 使用 AstrBot LLM Provider，可用：{llm_provider.available}")
            self.engine.llm_provider = llm_provider
            text = await self.engine.analyze_watchlist()
            await event.send(_message(self.formatter.truncate(text)))
        except Exception as exc:
            await event.send(_message(f"分析股池失败：{exc}"))

    @filter.command("追踪股池")
    async def track_watchlist(self, event: AstrMessageEvent, args: str = ""):
        """追踪自选股池行情。用法：/追踪股池"""
        txt_flag, _ = _is_txt_flag(args)
        await event.send(_message("正在拉取自选股池行情，请稍候..."))
        try:
            result = await self.engine.track_watchlist()
            chain = await self._render_watchlist_chain(result, allow_image=not txt_flag)
            await event.send(chain)
        except Exception as exc:
            await event.send(_message(f"追踪股池失败：{exc}"))

    @filter.command("report")
    async def get_report(self, event: AstrMessageEvent, report_id: str = ""):
        """查看报告。用法：/report [报告ID]"""
        if not report_id:
            text = await self.engine.list_reports()
        else:
            text = await self.engine.get_report(report_id)
        await event.send(_message(self.formatter.truncate(text)))
