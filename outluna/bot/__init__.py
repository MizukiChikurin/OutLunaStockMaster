"""AstrBot 插件包入口。

此文件在作为 Python 包导入时使用，提供 OutLunaPlugin 类。
AstrBot 实际加载插件时，优先读取 main.py 或 <插件目录名>.py，
因此入口逻辑主要在 star_plugin.py / main.py 中实现。
"""

from outluna.bot.star_plugin import OutLunaPlugin

__all__ = ["OutLunaPlugin"]
