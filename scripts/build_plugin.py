#!/usr/bin/env python3
"""AstrBot 插件打包脚本（整体项目作为插件）。

本脚本将项目根目录整体打包为一个可直接放入 AstrBot plugins 目录的插件目录。
打包后的目录结构示例：

    astrbot_plugin_outluna/
        metadata.yaml          # AstrBot 插件元数据
        main.py                # 插件入口，自动调整 sys.path 后导入 outluna
        requirements.txt       # 外部依赖
        outluna/               # 项目源码（复制）
        data/                  # 数据目录占位（运行时创建）
        logs/                  # 日志目录占位（运行时创建）

这样用户无需复制/软链接，只需把打包后的目录一次性复制到 AstrBot 的 plugins 目录即可。
"""

import shutil
from pathlib import Path

# 需要排除的文件/目录模式，避免把无用或敏感文件打包进插件。
# 注意：这里 "data" / "logs" 仅排除项目根目录下的运行时数据，
# 子包（如 outluna/data）不受影响。
EXCLUDE_PATTERNS: tuple[str, ...] = (
    ".git",
    ".gitignore",
    ".github",
    ".venv",
    "__pycache__",
    "*.pyc",
    "*.pyo",
    ".pytest_cache",
    ".coverage",
    "htmlcov",
    "tests",
    "plans",
    ".rcoder",
    ".vscode",
    ".idea",
    "*.egg-info",
    "build",
    "dist",
    "develop-eggs",
    "astrbot_plugin_outluna",
    ".ruff_cache",
    ".mypy_cache",
    ".env",
    ".env.local",
    ".env.example",
)

# 仅在项目根目录下排除的目录名称，不应在子包中误排除
ROOT_ONLY_EXCLUDES: set[str] = {"data", "logs"}


def _should_exclude(path: Path, root: Path) -> bool:
    """根据排除模式判断是否跳过该路径。"""
    rel = path.relative_to(root).as_posix()
    parts = rel.split("/")
    for idx, part in enumerate(parts):
        for pattern in EXCLUDE_PATTERNS:
            clean_pattern = pattern.lstrip("*")
            if part == pattern or part.endswith(clean_pattern):
                return True
        # 根目录下的 data/logs 排除，子包中的 data/logs 不排除
        if idx == 0 and part in ROOT_ONLY_EXCLUDES:
            return True
    return False


def _copy_project(source: Path, target: Path) -> None:
    """将项目源码复制到插件目录内，同时排除无用文件。"""
    for item in source.iterdir():
        if _should_exclude(item, source):
            continue
        destination = target / item.name
        if item.is_dir():
            shutil.copytree(item, destination, ignore=_shutil_ignore)
        else:
            shutil.copy2(item, destination)


def _shutil_ignore(_src: str, names: list[str]) -> set[str]:
    """shutil.copytree 的 ignore 回调。"""
    ignored: set[str] = set()
    for name in names:
        for pattern in EXCLUDE_PATTERNS:
            clean_pattern = pattern.lstrip("*")
            if name == pattern or name.endswith(clean_pattern):
                ignored.add(name)
    return ignored


def _generate_main_entry(plugin_dir: Path) -> str:
    """生成插件 main.py 内容。

    关键点：
    1. 先把插件目录本身加入 sys.path，确保能导入插件目录内的 outluna 包。
    2. 再从 outluna.bot.star_plugin 导入插件类。
    """
    return '''\
"""AstrBot Star 插件入口。

本文件由 scripts/build_plugin.py 自动生成，请勿手动修改。
项目源码已整体打包到本插件目录内，运行时会自动调整 sys.path。
"""

import sys
from pathlib import Path

_plugin_dir = Path(__file__).parent

# 确保插件目录本身在 sys.path 中，使 AstrBot 能导入插件内的 outluna 包
if str(_plugin_dir) not in sys.path:
    sys.path.insert(0, str(_plugin_dir))

from outluna.bot.star_plugin import OutLunaPlugin

__all__ = ["OutLunaPlugin"]
'''


def _generate_requirements() -> str:
    """生成插件 requirements.txt，声明可通过 PyPI 安装的外部依赖。"""
    return (
        "pandas>=2.0.0\n"
        "numpy>=1.24.0\n"
        "pydantic>=2.0.0\n"
        "pydantic-settings>=2.0.0\n"
        "loguru>=0.7.0\n"
        "jinja2>=3.1.0\n"
        "httpx>=0.27.0\n"
        "python-dotenv>=1.0.0\n"
        "PyYAML>=6.0\n"
        "pyarrow>=16.0.0\n"
        "akshare>=1.15.0\n"
        "yfinance>=0.2.54\n"
        "pandas-ta>=0.3.14b\n"
        "ta-lib>=0.5.0\n"
        "openai>=1.35.0\n"
    )


def _generate_metadata() -> str:
    """生成插件 metadata.yaml。"""
    return (
        "name: outluna\n"
        "desc: OutLuna 投资助手：策略寻股、智能分析、回测验证\n"
        "author: OutLuna Team\n"
        "version: 0.1.0\n"
        'display_name: "OutLuna 投资助手"\n'
        'astrbot_version: ">=4.16,<5"\n'
        "support_platforms: []\n"
    )


def build_plugin():
    """构建 AstrBot 插件目录（整体项目打包）。"""
    root = Path(__file__).parent.parent
    target = root / "astrbot_plugin_outluna"

    # 清理并重新生成插件目录
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)

    # 1. 复制项目源码到插件目录内
    _copy_project(root, target)

    # 2. 创建数据与日志目录占位
    (target / "data").mkdir(exist_ok=True)
    (target / "logs").mkdir(exist_ok=True)

    # 3. 生成 AstrBot 插件入口 main.py
    main_path = target / "main.py"
    main_path.write_text(_generate_main_entry(target), encoding="utf-8")

    # 4. 生成 metadata.yaml
    metadata_path = target / "metadata.yaml"
    metadata_path.write_text(_generate_metadata(), encoding="utf-8")

    # 5. 生成 requirements.txt
    deps_file = target / "requirements.txt"
    deps_file.write_text(_generate_requirements(), encoding="utf-8")

    print(f"插件已生成：{target}")
    print("部署步骤：")
    print("1. 将整个 astrbot_plugin_outluna 目录复制到 AstrBot 的 plugins 目录")
    print("2. 重启 AstrBot 或重载插件")
    print("3. 在聊天窗口发送 /strategy 验证加载成功")
    print("")
    print("注意：")
    print("  - 本项目源码已整体打包到插件目录内，无需额外安装 outluna 包。")
    print("  - 首次加载时 AstrBot 会自动安装 requirements.txt 中的外部依赖。")
    print("  - 插件运行时的数据将保存在插件目录下的 data/ 和 logs/ 中。")


if __name__ == "__main__":
    build_plugin()
