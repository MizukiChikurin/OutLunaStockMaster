# OutLunaStockMaster

投资辅助聊天机器人，基于 AstrBot 框架接入，支持策略寻股、智能分析、回测验证。

## 主要功能

- **策略寻股**：将投资经验转化为 K 线特征策略，自动扫描市场匹配股票
- **智能分析**：基本面、企业信息、主力动向、新闻情绪多维分析
- **报告生成**：结构化投研报告，支持历史回溯对比
- **回测验证**：基于历史行情模拟运行，评估策略有效性
- **聊天交互**：通过 AstrBot 以自然语言对话方式使用

## 快速开始

```bash
# 1. 安装依赖
pip install -e ".[all]"

# 2. 配置环境变量（复制 .env.example 为 .env 并填写）
cp .env.example .env

# 3. 运行测试
pytest

# 4. 启动命令行
python -m outluna.cli
```

## AstrBot 插件部署

```bash
# 1. 生成插件目录
python scripts/build_plugin.py

# 2. 复制到 AstrBot 的 plugins 目录
cp -r astrbot_plugin_outluna <AstrBot安装目录>/plugins/

# 3. 重启 AstrBot
```

安装完成后，在聊天窗口使用以下命令：

| 命令 | 说明 | 示例 |
|------|------|------|
| `/scan [策略名]` | 执行策略扫描 | `/scan 十字星` |
| `/analyze <代码>` | 分析指定股票 | `/analyze 600519` |
| `/backtest <策略名> [天数]` | 执行策略回测 | `/backtest 十字星 90` |
| `/report [报告ID]` | 查看报告列表或详情 | `/report` |
| `/compare <id1> <id2>` | 对比两份报告 | `/compare abc123 def456` |
| `/strategy` | 列出可用策略 | `/strategy` |

## 项目结构

```
outluna/
├── bot/              # AstrBot Star 插件
├── strategy/         # 策略引擎
├── analysis/         # 分析引擎
├── data/             # 数据服务层
├── backtest/         # 回测引擎
├── report/           # 报告系统
└── utils/            # 工具函数
```

## 数据源配置

本项目以 **Kimi Datasource** 为核心数据底座：

- **Kimi Datasource**：选股、行情、财务、公司信息、企业风险
- **akshare**：A 股特色数据（主力动向、龙虎榜、融资融券、新闻）
- **yfinance**：分钟级历史、美股期权、分析师评级

请在 `.env` 中配置 `OUTLUNA_KIMI_DATASOURCE_HOME` 指向 kimi-datasource 目录。

## 文档

- [架构设计文档](plans/架构设计文档.md)
- [Kimi Datasource 能力调研报告](plans/KimiDatasource能力调研报告.md)

## 免责声明

本项目仅供学习和研究使用，不构成任何投资建议。投资有风险，决策需谨慎。AI 生成的分析结果可能存在错误，请以官方数据和自身判断为准。
