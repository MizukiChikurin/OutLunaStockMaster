"""消息格式化工具。

统一 AstrBot 与 CLI 场景下的消息排版，支持截断、加粗、表格等简单格式。
"""

from __future__ import annotations

from typing import Any


class MessageFormatter:
    """消息格式化器。"""

    @staticmethod
    def section(title: str) -> str:
        """生成章节标题。"""
        return f"**{title}**"

    @staticmethod
    def key_value(key: str, value: Any) -> str:
        """生成键值对文本。"""
        return f"{key}：{value}"

    @staticmethod
    def truncate(text: str, max_length: int = 2000) -> str:
        """按最大长度截断文本并附加提示。

        Args:
            text: 原始文本。
            max_length: 最大保留字符数。

        Returns:
            截断后的文本。
        """
        if len(text) <= max_length:
            return text
        return text[:max_length] + f"\n...（内容已截断，共 {len(text)} 字符）"

    @staticmethod
    def format_list(items: list[str], ordered: bool = True) -> str:
        """将列表格式化为有序或无序列表。"""
        if not items:
            return "无"
        lines = []
        for idx, item in enumerate(items, 1):
            prefix = f"{idx}. " if ordered else "- "
            lines.append(f"{prefix}{item}")
        return "\n".join(lines)
