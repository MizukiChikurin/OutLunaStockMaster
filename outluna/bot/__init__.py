"""AstrBot 插件包入口。

AstrBot 实际加载插件时，优先读取打包后的 ``main.py``，
因此入口逻辑在 ``star_plugin.py`` / ``main.py`` 中实现。
本文件不再主动导入 ``star_plugin``，避免在非 AstrBot 环境（如测试、CLI）中
因缺少 astrbot 依赖而导致导入失败。
"""

__all__: list[str] = []
