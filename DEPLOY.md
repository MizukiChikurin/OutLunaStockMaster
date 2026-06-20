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

## AstrBot 插件部署

### 方式一：本地开发部署

```bash
# 生成插件目录
python scripts/build_plugin.py

# 复制到 AstrBot 的 plugins 目录
cp -r astrbot_plugin_outluna <AstrBot安装目录>/plugins/

# 重启 AstrBot
```

### 方式二：软链接开发（推荐开发时使用）

```bash
# Windows PowerShell（管理员）
New-Item -ItemType SymbolicLink -Path "<AstrBot安装目录>\plugins\astrbot_plugin_outluna" -Target "E:\OutLunaStockMaster\astrbot_plugin_outluna"

# Linux/macOS
ln -s E:/OutLunaStockMaster/astrbot_plugin_outluna <AstrBot安装目录>/plugins/astrbot_plugin_outluna
```

### 验证插件加载

启动 AstrBot 后，在聊天窗口发送：

```
/strategy
```

如果返回可用策略列表，说明插件加载成功。

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

## 免责声明

本项目仅供学习和研究使用，不构成任何投资建议。所有分析结果均由 AI 和外部数据生成，可能存在错误、延迟或偏差。投资有风险，决策需谨慎。使用者应自行核实数据并承担全部投资风险。
