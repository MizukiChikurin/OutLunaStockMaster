"""命令行入口。"""

import asyncio

from outluna.engine import OutLunaEngine
from outluna.utils.logger import setup_logging


def main():
    """CLI 主入口。"""
    setup_logging()
    print("OutLunaStockMaster CLI")
    print("=" * 40)

    engine = OutLunaEngine()

    async def run():
        await engine.initialize()
        print("\n可用命令：")
        print("  list    - 列出策略")
        print("  scan    - 策略扫描")
        print("  select  - 超短线风控选股")
        print("  fixed   - 固定策略选股")
        print("  analyze - 分析股票")
        print("  reports - 列出报告")
        print("  track   - 追踪自选股池")
        print("  quit    - 退出")

        while True:
            cmd = input("\noutluna> ").strip()
            if not cmd:
                continue
            parts = cmd.split()
            action = parts[0].lower()

            try:
                if action == "quit":
                    break
                elif action == "list":
                    print(await engine.list_strategies())
                elif action == "scan":
                    strategy = parts[1] if len(parts) > 1 else "十字星"
                    print(await engine.scan(strategy))
                elif action == "select":
                    requirements = " ".join(parts[1:]) if len(parts) > 1 else ""
                    result = await engine.select_stocks(requirements)
                    print(result.text)
                elif action == "fixed":
                    preset_name = parts[1] if len(parts) > 1 else ""
                    result = await engine.select_stocks_by_preset(preset_name)
                    print(result.text)
                elif action == "analyze":
                    symbol = parts[1] if len(parts) > 1 else "600519"
                    print(await engine.analyze(symbol))
                elif action == "reports":
                    print(await engine.list_reports())
                elif action == "track":
                    result = await engine.track_watchlist()
                    print(result.text)
                else:
                    print(f"未知命令：{action}")
            except Exception as exc:
                print(f"执行失败：{exc}")

    asyncio.run(run())


if __name__ == "__main__":
    main()
