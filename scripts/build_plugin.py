#!/usr/bin/env python3
"""AstrBot 插件打包脚本。

将 outluna/bot 目录打包为独立的 AstrBot Star 插件目录，
方便复制或链接到 AstrBot 的 plugins 目录。
"""

import shutil
from pathlib import Path


def build_plugin():
    """构建 AstrBot 插件目录。"""
    root = Path(__file__).parent.parent
    source = root / "outluna" / "bot"
    target = root / "astrbot_plugin_outluna"

    if target.exists():
        shutil.rmtree(target)

    shutil.copytree(source, target)
    print(f"插件已生成：{target}")
    print("请将此目录复制或链接到 AstrBot 的 plugins 目录下")


if __name__ == "__main__":
    build_plugin()
