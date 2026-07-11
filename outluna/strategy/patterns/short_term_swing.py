"""A股超短线风控选股策略。

本策略复现了 ``plans/API调用流程复盘.md`` 中的手动选股流程：
1. 通过 akshare 获取 A 股全市场实时行情快照；
2. 清洗股票池，剔除 ST/退市/北交所/新股/停牌等；
3. 按成交额取前 N 只作为重点分析对象；
4. 通过 Kimi Datasource 的 realtime_tech 分批获取技术指标；
5. 执行一票否决与硬性入选；
6. 输出结构化的超短线选股报告。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from outluna.data.models import DataRequirement, ScanReport, ScanResult
from outluna.data.providers.akshare_provider import AkShareProvider
from outluna.data.providers.kimi_provider import KimiDataSourceProvider
from outluna.strategy.base import StrategyBase, registry
from outluna.utils.symbol import SymbolNormalizer


@registry.register
class ShortTermSwingStrategy(StrategyBase):
    """A股超短线风控选股策略。

    严格按照“先否决、后筛选”的顺序工作，只输出通过所有风控条件的候选标的，
    不再做量化评分，将原始数据与信号统一交给后续 LLM 或用户自行判断。
    """

    name = "超短线风控选股"
    description = "A股超短线风控选股：先否决、后筛选，输出低位启动候选股。"
    version = "1.0"

    def __init__(self, params: dict | None = None):
        self.top_n: int = 100
        self.min_turnover: float = 150_000_000.0
        super().__init__(params)

    def match(self, symbol: str, df: pd.DataFrame) -> bool:
        """本策略不走传统单只 match 流程，返回 False。"""
        return False

    @property
    def required_data(self) -> DataRequirement:
        """本策略主要使用快照与实时技术指标，无需大量历史 K 线。"""
        return DataRequirement(period="1d", bars=5)

    async def prepare_data(self, gateway) -> dict[str, Any] | None:
        """准备选股所需数据：A股快照 + 技术指标。

        直接从网关的 provider 实例中选取 akshare 与 Kimi Datasource，
        复现手动流程中的数据源组合。
        """
        ak_provider = self._get_provider(gateway, AkShareProvider)
        kimi_provider = self._get_provider(gateway, KimiDataSourceProvider)

        if ak_provider is None:
            raise RuntimeError("超短线选股依赖 akshare 获取 A 股快照，但未启用或初始化失败")

        # 1. 获取全市场实时行情
        spot_df = ak_provider.get_a_share_spot()
        if spot_df.empty:
            raise RuntimeError("未能获取 A 股实时行情快照")

        # 2. 清洗与筛选
        filtered = self._filter_spot(spot_df)

        # 3. 取成交额前 N
        top_df = filtered.nlargest(self.top_n, "成交额").reset_index(drop=True)

        # 4. 获取技术指标
        tech_df = pd.DataFrame()
        if kimi_provider is not None and not top_df.empty:
            tech_df = self._fetch_tech_indicators(kimi_provider, top_df)

        return {
            "spot": top_df,
            "tech": tech_df,
            "data_time": self._extract_data_time(spot_df, tech_df),
            "data_source": "akshare实时行情 + stock_finance_data实时技术指标",
        }

    async def evaluate_batch(self, data: dict[str, Any]) -> list[ScanResult]:
        """批量执行一票否决，返回所有结果（含被否决与候选）。"""
        spot_df: pd.DataFrame = data.get("spot", pd.DataFrame())
        tech_df: pd.DataFrame = data.get("tech", pd.DataFrame())

        if spot_df.empty:
            return []

        # 构建代码到技术指标行的映射
        tech_map = self._build_tech_map(tech_df)

        results: list[ScanResult] = []
        for _, row in spot_df.iterrows():
            result = self._evaluate_single(row, tech_map)
            results.append(result)

        # 分类并生成报告
        self._build_report(results, data)
        return results

    def _get_provider(self, gateway, provider_cls: type) -> Any:
        """从网关的 provider 集合中获取指定类型的实例。"""
        for provider in gateway.providers.values():
            if isinstance(provider, provider_cls):
                return provider
        return None

    def _filter_spot(self, df: pd.DataFrame) -> pd.DataFrame:
        """清洗 A 股快照数据。

        剔除 ST/*ST/退市/北交所/新股/停牌，并保留成交额达标股票。
        """
        df = df.copy()

        # 确保关键列存在
        for col in ["名称", "代码", "成交额", "最新价"]:
            if col not in df.columns:
                raise RuntimeError(f"行情快照缺少必要列：{col}")

        # 1. 排除 ST/*ST/退市/摘牌
        df = df[~df["名称"].astype(str).str.contains(r"ST|退|摘", na=False, regex=True)]
        # 2. 排除北交所（bj 开头）
        df = df[~df["代码"].astype(str).str.lower().str.startswith("bj")]
        # 3. 排除新股 N 开头
        df = df[~df["名称"].astype(str).str.startswith("N")]
        # 4. 排除停牌（最新价<=0 或为空）
        df = df[pd.to_numeric(df["最新价"], errors="coerce").fillna(0) > 0]
        # 5. 成交额>=1.5亿
        df = df[pd.to_numeric(df["成交额"], errors="coerce").fillna(0) >= self.min_turnover]

        return df

    def _fetch_tech_indicators(
        self,
        kimi_provider: KimiDataSourceProvider,
        spot_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """分批获取实时技术指标，每次最多 3 只。"""
        symbols = []
        for _, row in spot_df.iterrows():
            code = str(row["代码"]).strip()
            normalized = SymbolNormalizer.normalize(code)
            if normalized:
                symbols.append(SymbolNormalizer.to_kimi(normalized))

        if not symbols:
            return pd.DataFrame()

        frames: list[pd.DataFrame] = []
        batch_size = 3
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i : i + batch_size]
            try:
                df = kimi_provider.get_realtime_tech(batch)
                if not df.empty:
                    frames.append(df)
            except Exception as exc:
                logger = __import__(
                    "outluna.utils.logger", fromlist=["setup_logging"]
                ).setup_logging()
                logger.warning(f"获取技术指标失败（批次 {i // batch_size + 1}）：{exc}")

        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def _build_tech_map(self, tech_df: pd.DataFrame) -> dict[str, pd.Series]:
        """将技术指标 DataFrame 转为以标准化代码为键的字典。"""
        tech_map: dict[str, pd.Series] = {}
        if tech_df.empty or "code" not in tech_df.columns:
            return tech_map

        for _, row in tech_df.iterrows():
            code = str(row["code"]).strip().upper()
            normalized = SymbolNormalizer.normalize(code)
            if normalized:
                tech_map[normalized] = row
        return tech_map

    def _evaluate_single(
        self,
        row: pd.Series,
        tech_map: dict[str, pd.Series],
    ) -> ScanResult:
        """评估单只股票：一票否决 + 生成关键信号。"""
        code = str(row["代码"]).strip()
        symbol = SymbolNormalizer.normalize(code)
        name = str(row.get("名称", ""))
        price = float(row.get("最新价", 0) or 0)
        change_pct = float(row.get("涨跌幅", 0) or 0)
        turnover = float(row.get("成交额", 0) or 0) / 1e8

        tech_row = tech_map.get(symbol)
        vetos, notes = self._detailed_analysis(row, tech_row)

        recommendation = "剔除" if vetos else "候选"

        return ScanResult(
            symbol=symbol or code,
            strategy_name=self.name,
            matched_at=datetime.now(),
            match_score=-1.0,
            trigger_data={
                "price": price,
                "change_pct": change_pct,
                "turnover": turnover,
            },
            name=name,
            price=price,
            change_pct=change_pct,
            turnover=turnover,
            score_details={},
            notes=notes,
            vetos=vetos,
            recommendation=recommendation,
        )

    def _detailed_analysis(
        self,
        row: pd.Series,
        tech_row: pd.Series | None,
    ) -> tuple[list[str], list[str]]:
        """执行一票否决并提取关键信号，返回 (否决原因, 备注)。"""
        vetos: list[str] = []
        notes: list[str] = []

        price = float(row.get("最新价", 0) or 0)
        change_pct = float(row.get("涨跌幅", 0) or 0)
        turnover = float(row.get("成交额", 0) or 0)
        high = float(row.get("最高", 0) or 0)
        low = float(row.get("最低", 0) or 0)

        if price <= 0:
            vetos.append("数据无效/停牌")
            return vetos, notes

        # ===== 一票否决条件 =====
        if abs(change_pct) >= 19.9:
            vetos.append(f"当日涨跌停：{change_pct:.2f}%")

        if turnover < self.min_turnover:
            vetos.append(f"成交额不足：{turnover / 1e8:.2f}亿")

        if tech_row is not None:
            rsi = self._safe_float(tech_row.get("rsi12"))
            j = self._safe_float(tech_row.get("j"))
            ma60 = self._safe_float(tech_row.get("ma60"))
            ma5 = self._safe_float(tech_row.get("ma5"))
            ma10 = self._safe_float(tech_row.get("ma10"))
            ma20 = self._safe_float(tech_row.get("ma20"))
            macd = self._safe_float(tech_row.get("macd"))
            dif = self._safe_float(tech_row.get("dif"))
            dea = self._safe_float(tech_row.get("dea"))
            atr14 = self._safe_float(tech_row.get("atr14"))

            if rsi is not None and rsi > 80:
                vetos.append(f"RSI极度超买：{rsi:.2f}")

            if j is not None and j > 110:
                vetos.append(f"KDJ极度高位：J={j:.2f}")

            if ma60 is not None and ma60 > 0 and price > ma60 * 1.3:
                vetos.append(f"价格极高：{price:.2f} > MA60*1.3={ma60 * 1.3:.2f}")

            if ma60 is not None and ma60 > 0 and price <= ma60 * 1.05:
                notes.append("价格处于MA60附近或下方，位置相对安全")
            elif ma60 is not None and ma60 > 0:
                notes.append("价格高于MA60，注意追高风险")

            if ma5 is not None and price > ma5:
                notes.append("站上MA5")
            if ma10 is not None and price > ma10:
                notes.append("站上MA10")
            if ma20 is not None and abs(price - ma20) / price < 0.05:
                notes.append("价格接近MA20")

            if rsi is not None and 40 <= rsi <= 70:
                notes.append(f"RSI健康({rsi:.1f})")

            if macd is not None and dif is not None and dea is not None:
                if macd > 0 and dif > dea:
                    notes.append("MACD金叉且红柱")
                elif dif > dea:
                    notes.append("MACD DIF>DEA")

            if atr14 is not None and price > 0:
                atr_ratio = atr14 / price * 100
                if atr_ratio >= 3:
                    notes.append(f"ATR充足({atr_ratio:.2f}%)")
                elif atr_ratio >= 2:
                    notes.append(f"ATR一般({atr_ratio:.2f}%)")
                else:
                    notes.append(f"ATR不足({atr_ratio:.2f}%)")

        # 收盘在日内振幅下半区
        if high > low:
            upper_half = low + (high - low) * 0.5
            if price < upper_half:
                vetos.append("收盘价位于日内振幅下半区")

            if 0 < change_pct <= 3:
                notes.append(f"当日温和上涨({change_pct:.2f}%)")
            elif -2 <= change_pct <= 0:
                notes.append(f"当日微跌/平盘({change_pct:.2f}%)")

        # 收盘在日内振幅下半区
        if high > low:
            upper_half = low + (high - low) * 0.5
            if price < upper_half:
                vetos.append("收盘价位于日内振幅下半区")

        notes.append("板块/资金数据待补充")

        return vetos, notes


    def _safe_float(self, value: Any) -> float | None:
        """安全地将值转为浮点数，失败返回 None。"""
        try:
            if value is None or (isinstance(value, float) and pd.isna(value)):
                return None
            return float(value)
        except (ValueError, TypeError):
            return None

    def _extract_data_time(self, spot_df: pd.DataFrame, tech_df: pd.DataFrame) -> str:
        """提取数据时间戳。"""
        time_candidates: list[str] = []
        if not tech_df.empty and "time" in tech_df.columns:
            first_time = str(tech_df["time"].iloc[0])
            if first_time and first_time.lower() != "nan":
                time_candidates.append(first_time)
        if not spot_df.empty and "时间戳" in spot_df.columns:
            first_time = str(spot_df["时间戳"].iloc[0])
            if first_time and first_time.lower() != "nan":
                time_candidates.append(first_time)
        if time_candidates:
            return time_candidates[0]
        return datetime.now().strftime("%Y-%m-%d 15:00:00")

    def _build_report(self, results: list[ScanResult], data: dict[str, Any]) -> None:
        """将评估结果组织为报告结构，附加到策略实例上供扫描器使用。"""
        # 由于 evaluate_batch 返回后才构造 ScanReport，这里先把中间结果暂存
        self._last_results = results
        self._last_data_time = data.get("data_time", "")
        self._last_data_source = data.get("data_source", "")

    def build_scan_report(self, report_id: str) -> ScanReport:
        """根据最近一次批量评估结果构建完整的 ScanReport。

        扫描器在 evaluate_batch 后调用此方法，将分类结果填入报告。
        """
        results = getattr(self, "_last_results", [])
        vetoed = [r for r in results if r.vetos]
        qualified = [r for r in results if not r.vetos]
        watch_list: list[ScanResult] = []

        # 构建市场环境概述
        market_summary = self._build_market_summary(results)
        final_conclusion = self._build_final_conclusion(qualified, watch_list, results)

        return ScanReport(
            report_id=report_id,
            strategy_name=self.name,
            strategy_params=self.params,
            created_at=datetime.now(),
            matches=qualified,
            total_scanned=len(results),
            data_time=self._last_data_time,
            data_source=self._last_data_source,
            market_summary=market_summary,
            vetoed=vetoed,
            watch_list=watch_list,
            qualified=qualified,
            final_conclusion=final_conclusion,
        )

    def _build_market_summary(self, results: list[ScanResult]) -> str:
        """根据被否决原因生成市场环境概述。"""
        if not results:
            return "无数据"

        total = len(results)
        vetoed = [r for r in results if r.vetos]
        down_count = sum(1 for r in results if (r.change_pct or 0) < -2)
        up_count = sum(1 for r in results if (r.change_pct or 0) > 2)

        reason_counts: dict[str, int] = {}
        for r in vetoed:
            for reason in r.vetos:
                # 只统计第一个原因，避免过细
                key = reason.split("：")[0].split("=")[0].strip()
                reason_counts[key] = reason_counts.get(key, 0) + 1

        lines = [
            f"本次分析共 {total} 只重点股票，",
            f"其中下跌超过2%的有 {down_count} 只，上涨超过2%的有 {up_count} 只。",
        ]
        if reason_counts:
            top_reasons = sorted(reason_counts.items(), key=lambda x: x[1], reverse=True)[:3]
            lines.append("主要否决原因：" + "、".join(f"{k}（{v}只）" for k, v in top_reasons) + "。")
        return "".join(lines)

    def _build_final_conclusion(
        self,
        qualified: list[ScanResult],
        watch_list: list[ScanResult],
        results: list[ScanResult],
    ) -> str:
        """生成最终结论文本。"""
        if qualified:
            lines = [
                f"推荐 {len(qualified)} 只候选股。",
                "建议结合次日开盘情况、板块强度与资金流向进一步确认买点。",
            ]
            return "\n".join(lines)

        lines = [
            "### 空仓/无符合条件标的",
            "",
            "没有通过一票否决的股票可推荐。",
            "",
            "原因分析：",
            "1. 当前重点股票池中多数标的未通过一票否决；",
            "2. 技术指标显示多数股票处于调整期或波动空间不足；",
            "3. 缺乏明确的低位启动、资金承接强的标的。",
            "",
            "交易建议：空仓观望，等待更好的入场时机。",
        ]
        if watch_list:
            names = "、".join(f"{r.name}（{r.symbol}）" for r in watch_list[:3])
            lines.append(f"可观察：{names}。")
        return "\n".join(lines)
