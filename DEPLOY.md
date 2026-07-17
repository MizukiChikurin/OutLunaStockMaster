# OutLuna 部署文档

## 环境要求

- Python 3.11+
- Windows / Linux / macOS
- Kimi Code 账号并完成 OAuth 登录（用于 Kimi Datasource）

## 安装步骤

### 1. 克隆项目

```bash
cd E:\OutLunaStockMaster
```

### 2. 创建虚拟环境（可选但推荐）

```bash
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # Linux/macOS
```

### 3. 安装依赖

```bash
pip install -e ".[all]"
```

如果只需要核心功能：

```bash
pip install -e ".[analysis]"
```

### 4. 配置环境变量

复制示例配置文件：

```bash
cp .env.example .env
```

编辑 `.env`，至少配置以下项：

```env
# Kimi Datasource 路径
OUTLUNA_KIMI_DATASOURCE_HOME=E:\kimi-datasource

# LLM 配置（可选，用于综合研判）
OUTLUNA_LLM_API_KEY=your_openai_key_here
OUTLUNA_LLM_MODEL=gpt-4o
```

### 5. 验证 Kimi 登录

确保已运行：

```bash
kimi login
```

并确认凭证文件存在：

```bash
ls %USERPROFILE%\.kimi\credentials\kimi-code.json
```

### 6. 运行测试

```bash
pytest
```

## AstrBot 插件部署（推荐方式：整体项目打包）

本项目已将完整源码打包为 AstrBot 插件目录，部署时无需复制源码、无需创建软链接、无需在 AstrBot 环境中安装 `outluna` 包。

### 方式一：本地构建后复制（推荐）

```bash
# 1. 生成插件目录
python scripts/build_plugin.py

# 2. 将生成的 astrbot_plugin_outluna 目录复制到 AstrBot 的 plugins 目录
# Windows PowerShell 示例：
Copy-Item -Recurse -Force "E:\OutLunaStockMaster\astrbot_plugin_outluna" "C:\Users\admin\AppData\Roaming\uv\tools\astrbot\data\plugins\"

# Linux/macOS 示例：
# cp -r E:/OutLunaStockMaster/astrbot_plugin_outluna /path/to/astrbot/data/plugins/

# 3. 重启 AstrBot
```

### 方式二：直接下载插件包

如果已获取打包好的 `astrbot_plugin_outluna.zip`：

```bash
# Windows PowerShell
Expand-Archive -Path astrbot_plugin_outluna.zip -DestinationPath "C:\Users\admin\AppData\Roaming\uv\tools\astrbot\data\plugins\"

# Linux/macOS
# unzip astrbot_plugin_outluna.zip -d /path/to/astrbot/data/plugins/
```

### 验证插件加载

启动 AstrBot 后，在聊天窗口发送：

```
/strategy
```

如果返回可用策略列表（包含“超短线风控选股”），说明插件加载成功。

### 常用命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `/scan [策略名]` | 执行策略扫描 | `/scan 十字星` |
| `/选股 <要求>` | 根据用户选股要求执行筛选（默认发图片，带 `--txt` 发送文本） | `/选股 选择站上MA5的股票` |
| `/analyze <代码>` | 分析指定股票 | `/analyze 600519` |
| `/backtest <策略名> [天数]` | 执行策略回测 | `/backtest 十字星 90` |
| `/report [报告ID]` | 查看报告 | `/report` |
| `/追踪股池` | 查看自选股池最近开盘价与收盘价（默认发图片，带 `--txt` 发送文本） | `/追踪股池` |

> 图片表格说明：`/选股` 与 `/追踪股池` 默认将结果渲染为图片表格发送；
> 在命令末尾加上 `--txt` 可强制发送文本。
> 图片渲染依赖 AstrBot 内置 T2I 服务，需确保 AstrBot 能访问默认 T2I 端点
> `https://t2i.soulter.top/text2img`，或已在 AstrBot 配置中指定自定义端点。

`超短线风控选股` 是内置示例策略，会按照 ``plans/选股要求.txt`` 中的规则执行。

自定义选股要求：

```
/选股 选择近5日涨幅不超过10%、RSI在40-70之间、站上MA5的股票
```

自定义选股会自动复用 AstrBot 中已配置的 LLM 来解析要求，无需单独设置 `OUTLUNA_LLM_API_KEY`。
如需在 CLI 中使用自定义选股，则需在 `.env` 中配置 `OUTLUNA_LLM_API_KEY`。

### 插件目录结构说明

打包后的插件目录已包含完整项目源码：

```
astrbot_plugin_outluna/
├── main.py              # AstrBot 插件入口
├── metadata.yaml        # 插件元数据
├── requirements.txt     # 外部依赖（仅含 PyPI 可安装包）
├── outluna/             # 完整项目源码
├── data/                # 运行时数据目录
└── logs/                # 运行时日志目录
```

`main.py` 会自动调整 `sys.path`，使 AstrBot 能够直接导入插件内部的 `outluna` 包，因此无需额外安装 `outluna`。

## 数据源说明

| 数据源 | 用途 | 是否必需 |
|--------|------|----------|
| Kimi Datasource | 选股、行情、财务、公司信息、企业风险 | 是 |
| akshare | A股特色数据（主力、龙虎榜、融资融券、新闻） | 否 |
| yfinance | 分钟级历史、美股期权、分析师评级 | 否 |

## 常见问题

### Q1: Kimi Datasource 调用失败

- 确认已执行 `kimi login`
- 确认 `OUTLUNA_KIMI_DATASOURCE_HOME` 指向正确的 kimi-datasource 目录
- 查看日志：`logs/outluna_YYYY-MM-DD.log`

### Q2: akshare / yfinance 数据为空

- 确认已安装：`pip install akshare yfinance`
- 部分接口可能需要网络访问或特定版本
- 查看网关调用统计排查

### Q3: 回测结果异常

- 回测使用历史日 K 数据，不含分红、拆股等细节
- 默认交易成本为万5，可在 `Portfolio` 中调整
- 策略信号基于收盘价，实际滑点未完全模拟

### Q4: AstrBot 插件加载失败

- 确认使用的是 `scripts/build_plugin.py` 生成的完整插件目录
- 确认插件目录名为 `astrbot_plugin_outluna`
- 检查 AstrBot 日志中是否有依赖安装失败信息
- 若使用 uv 打包版 AstrBot，请确保 requirements.txt 中的依赖可被正常安装

## 免责声明

本项目仅供学习和研究使用，不构成任何投资建议。所有分析结果均由 AI 和外部数据生成，可能存在错误、延迟或偏差。投资有风险，决策需谨慎。使用者应自行核实数据并承担全部投资风险。
