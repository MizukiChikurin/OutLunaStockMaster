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

    # 插件运行时依赖 outluna 包，创建依赖说明
    deps_file = target / "requirements.txt"
    deps_file.write_text("outluna\n", encoding="utf-8")

    print(f"插件已生成：{target}")
    print("部署步骤：")
    print("1. 确保 AstrBot 环境已安装 outluna 包：pip install -e E:\\OutLunaStockMaster")
    print("2. 将 astrbot_plugin_outluna 复制到 AstrBot 的 plugins 目录")
    print("3. 重启 AstrBot 并启用 outluna 插件")


if __name__ == "__main__":
    build_plugin()
