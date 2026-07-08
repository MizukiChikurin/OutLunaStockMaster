"""用户自定义选股策略执行器。

根据 ``UserStrategyConfig`` 从数据源获取数据，
严格执行一票否决、硬性入选与多维度评分，返回结构化结果。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd

from outluna.config import settings
from outluna.data.gateway import DataGateway
from outluna.data.models import DataCall, ExecutionTrace, ScanResult
from outluna.data.providers.akshare_provider import AkShareProvider
from outluna.data.providers.kimi_api_provider import KimiApiDataSourceProvider
from outluna.data.providers.kimi_provider import KimiAuthError, KimiDataSourceProvider
from outluna.strategy.user_driven.criteria import (
    FilterRule,
    ScoreDimension,
    UserStrategyConfig,
)
from outluna.utils.logger import setup_logging
from outluna.utils.symbol import SymbolNormalizer

logger = setup_logging()


class StockData:
    """单只股票聚合数据。"""

    def __init__(self, symbol: str, name: str = ""):
        self.symbol = symbol
        self.name = name
        self.fields: dict[str, Any] = {"symbol": symbol, "name": name}

    def set(self, field: str, value: Any) -> None:
        """设置字段值。"""
        self.fields[field] = value

    def get(self, field: str, default: Any = None) -> Any:
        """获取字段值。"""
        return self.fields.get(field, default)


class StrategyExecutor:
    """用户策略执行器。"""

    def __init__(
        self,
        gateway: DataGateway,
        config: UserStrategyConfig,
        trace: ExecutionTrace | None = None,
    ):
        self.gateway = gateway
        self.config = config
        self.ak_provider = self._get_provider(AkShareProvider)
        self.kimi_provider = self._get_provider(
            (KimiDataSourceProvider, KimiApiDataSourceProvider)
        )
        self.trace = trace or ExecutionTrace()
        self._step_counter = 0

    def _next_step(self) -> int:
        """获取下一个步骤编号。"""
        self._step_counter += 1
        return self._step_counter

    def _record_call(
        self,
        phase: str,
        provider: str,
        method: str,
        symbols: list[str],
        params: dict[str, Any] | None = None,
        status: str = "成功",
        result_summary: str = "",
        elapsed_seconds: float = 0.0,
    ) -> None:
        """记录一次数据源调用。"""
        self.trace.add_call(
            DataCall(
                step=self._next_step(),
                phase=phase,
                provider=provider,
                method=method,
                symbols=symbols[:50],
                params=params or {},
                status=status,
                result_summary=result_summary,
                elapsed_seconds=elapsed_seconds,
            )
        )

    def _get_provider(self, provider_cls: type | tuple[type, ...]) -> Any:
        """从网关获取指定类型的数据提供商。"""
        if isinstance(provider_cls, type):
            provider_cls = (provider_cls,)
        for provider in self.gateway.providers.values():
            if isinstance(provider, provider_cls):
                return provider
        return None

    def execute(self) -> list[ScanResult]:
        """执行完整选股流程。"""
        self.trace.add_phase("初始化", "准备执行用户自定义选股策略")
        self.trace.add_note(f"策略名称：{self.config.name}")
        self.trace.add_note(f"分析上限：{self.config.max_analyze} 只")

        # 1. 获取 A 股快照
        self.trace.add_phase("阶段1", "获取 A 股全市场实时行情快照", {"数据源": "akshare"})
        spot_df = self._get_spot()
        if spot_df.empty:
            raise RuntimeError("未能获取 A 股实时行情快照")
        self.trace.add_count("原始股票池", len(spot_df), "A股全市场快照")

        # 2. 构建 StockData 列表并填充基础字段
        stocks = self._build_stock_data(spot_df)
        self.trace.add_count("构建 StockData", len(stocks))

        # 3. 应用股票池粗筛：优先使用 LLM 生成的快照初筛代码，失败则回退到 pool_filters
        if self.config.screening_code.strip():
            stocks, method = self._apply_screening_code(stocks, spot_df)
        else:
            stocks = self._apply_pool_filters(stocks)
            method = "pool_filters"

        # 托底：粗筛后股票池为空时，回退到全部股票，避免分析中断
        if not stocks:
            logger.warning("初筛后股票池为空，回退到全部股票")
            stocks = self._build_stock_data(spot_df)
            method = "all_fallback"

        self._save_screening_result(stocks, method=method)
        self.trace.add_count("股票池粗筛后", len(stocks), "应用初筛条件")

        # 4. 按成交额排序并限制分析数量
        stocks = self._limit_analyze_scope(stocks)
        max_analyze = self.config.max_analyze
        scope_note = "分析全部粗筛后股票" if max_analyze <= 0 else f"按成交额排序，取前 {max_analyze} 只"
        self.trace.add_count("进入详细分析", len(stocks), scope_note)

        # 5. 获取并填充技术指标
        self.trace.add_phase("阶段2", "获取实时技术指标", {"数据源": "stock_finance_data / Kimi Datasource", "批次": "每次最多3只"})
        self._fill_technical_data(stocks)

        # 6. 按需获取历史数据
        if self._needs_historical_data():
            self.trace.add_phase("阶段3", "获取历史 K 线数据", {"数据源": "akshare / stock_finance_data", "周期": "1d, 60根"})
            self._fill_historical_data(stocks)
        else:
            self.trace.add_phase("阶段3", "跳过历史 K 线数据", {"原因": "规则中未使用历史字段"})

        # 7. 按需获取资金流数据
        if self._needs_flow_data():
            self.trace.add_phase("阶段4", "获取资金流向数据", {"数据源": "akshare"})
            self._fill_capital_flow(stocks)
        else:
            self.trace.add_phase("阶段4", "跳过资金流向数据", {"原因": "规则中未使用资金流字段"})

        # 8. 一票否决、硬性入选、评分
        self.trace.add_phase("阶段5", "执行一票否决、硬性入选与多维度评分")
        results: list[ScanResult] = []
        for stock in stocks:
            result = self._evaluate_stock(stock)
            results.append(result)

        self.trace.add_count("最终候选", len([r for r in results if r.recommendation != "剔除"]))
        self.trace.add_phase("完成", "选股执行完成，生成报告")

        return results

    def _needs_historical_data(self) -> bool:
        """判断是否需要获取历史 K 线数据。"""
        history_fields = {
            "change_pct_5d", "change_pct_10d", "change_pct_20d",
            "turnover_5d_avg", "turnover_20d_avg", "price_position_60d",
            "price_position_20d", "volume_ratio",
            "dist_to_20d_low_pct", "dist_to_20d_high_pct",
            "dist_to_60d_low_pct", "dist_to_60d_high_pct",
        }
        return self._any_rule_uses_field(history_fields)

    def _needs_flow_data(self) -> bool:
        """判断是否需要获取资金流向数据。"""
        flow_fields = {"main_inflow_3d", "main_inflow_5d"}
        return self._any_rule_uses_field(flow_fields)

    def _any_rule_uses_field(self, fields: set[str]) -> bool:
        """检查是否有任何规则使用了指定字段。"""
        all_rules: list[FilterRule] = []
        all_rules.extend(self.config.pool_filters)
        all_rules.extend(self.config.veto_rules)
        all_rules.extend(self.config.entry_rules)
        for dim in self.config.score_dimensions:
            all_rules.extend(dim.rules)
        return any(rule.field in fields for rule in all_rules)

    def _get_spot(self) -> pd.DataFrame:
        """获取 A 股快照。"""
        start = time.perf_counter()
        if self.ak_provider:
            try:
                df = cast(pd.DataFrame, self.ak_provider.get_a_share_spot())
                self._record_call(
                    phase="阶段1",
                    provider="akshare",
                    method="get_a_share_spot",
                    symbols=[],
                    result_summary=f"获取 {len(df)} 只 A 股快照",
                    elapsed_seconds=time.perf_counter() - start,
                )
                return df
            except Exception as exc:
                elapsed = time.perf_counter() - start
                self._record_call(
                    phase="阶段1",
                    provider="akshare",
                    method="get_a_share_spot",
                    symbols=[],
                    status="失败",
                    result_summary=str(exc),
                    elapsed_seconds=elapsed,
                )
                logger.warning(f"akshare 获取 A 股快照失败，回退到 gateway：{exc}")

        # akshare 不存在或失败时回退到 gateway
        start = time.perf_counter()
        try:
            df = self.gateway.get_a_share_spot()
            self._record_call(
                phase="阶段1",
                provider="gateway",
                method="get_a_share_spot",
                symbols=[],
                result_summary=f"获取 {len(df)} 只 A 股快照",
                elapsed_seconds=time.perf_counter() - start,
            )
            return df
        except Exception as exc:
            self._record_call(
                phase="阶段1",
                provider="gateway",
                method="get_a_share_spot",
                symbols=[],
                status="失败",
                result_summary=str(exc),
                elapsed_seconds=time.perf_counter() - start,
            )
            raise

    def _build_stock_data(self, spot_df: pd.DataFrame) -> list[StockData]:
        """从快照构建 StockData 列表。"""
        stocks: list[StockData] = []
        for _, row in spot_df.iterrows():
            code = str(row.get("代码", "")).strip()
            symbol = SymbolNormalizer.normalize(code)
            if not symbol:
                continue
            name = str(row.get("名称", ""))
            stock = StockData(symbol, name)
            stock.set("price", self._to_float(row.get("最新价")))
            stock.set("change_pct", self._to_float(row.get("涨跌幅")))
            stock.set("open", self._to_float(row.get("今开")))
            stock.set("high", self._to_float(row.get("最高")))
            stock.set("low", self._to_float(row.get("最低")))
            stock.set("turnover", self._to_float(row.get("成交额")))
            stock.set("volume", self._to_float(row.get("成交量")))
            stocks.append(stock)
        return stocks

    def _apply_pool_filters(self, stocks: list[StockData]) -> list[StockData]:
        """应用声明式股票池粗筛条件。"""
        if not self.config.pool_filters:
            return stocks
        return [s for s in stocks if self._matches_all_rules(s, self.config.pool_filters)]

    def _apply_screening_code(self, stocks: list[StockData], spot_df: pd.DataFrame) -> tuple[list[StockData], str]:
        """执行 LLM 生成的快照初筛 Python 代码，失败或结果为空时回退到 pool_filters。

        返回 (筛选后的股票列表, 使用的方法标识)。
        """
        start = time.perf_counter()
        try:
            codes = self._run_screening_code(spot_df, self.config.screening_code)
            code_set = {str(c).strip() for c in codes if c}
            elapsed = time.perf_counter() - start
            self._record_call(
                phase="阶段1",
                provider="llm_generated_code",
                method="screening_code",
                symbols=[],
                params={"code": self.config.screening_code[:200]},
                status="成功",
                result_summary=f"LLM 初筛代码执行成功，保留 {len(code_set)} 只股票",
                elapsed_seconds=elapsed,
            )
            self.trace.add_note(f"使用 LLM 生成的快照初筛代码，保留 {len(code_set)} 只股票")

            if not code_set:
                logger.warning("LLM 初筛代码返回空结果，使用默认快照粗筛")
                codes = self._default_snapshot_screening(spot_df)
                code_set = {c for c in codes if c}
                self.trace.add_note(f"LLM 初筛代码返回空，使用默认快照粗筛，保留 {len(code_set)} 只股票")
                if not code_set:
                    return self._apply_pool_filters(stocks), "pool_filters_fallback"
                return [s for s in stocks if self._stock_code_in_set(s, code_set)], "default_screening"

            filtered = [s for s in stocks if self._stock_code_in_set(s, code_set)]
            return filtered, "screening_code"
        except Exception as exc:
            elapsed = time.perf_counter() - start
            self._record_call(
                phase="阶段1",
                provider="llm_generated_code",
                method="screening_code",
                symbols=[],
                params={"code": self.config.screening_code[:200]},
                status="失败",
                result_summary=str(exc),
                elapsed_seconds=elapsed,
            )
            logger.warning(f"LLM 初筛代码执行失败，使用默认快照粗筛：{exc}")
            self.trace.add_note(f"LLM 初筛代码执行失败，使用默认快照粗筛：{exc}")
            codes = self._default_snapshot_screening(spot_df)
            code_set = {c for c in codes if c}
            if not code_set:
                return self._apply_pool_filters(stocks), "pool_filters_fallback"
            return [s for s in stocks if self._stock_code_in_set(s, code_set)], "default_screening"

    def _save_screening_result(self, stocks: list[StockData], *, method: str) -> None:
        """将初筛阶段结果保存到 data/tasks/{task_label}/初筛结果.json。

        无论通过 screening_code 还是 pool_filters 进行初筛，都会保存，便于回溯。
        """
        try:
            label = self.config.task_label or "用户选股"
            date_str = datetime.now().strftime("%Y.%m.%d")
            project_dir = Path(str(settings.project_dir))
            task_folder = project_dir / "data" / "tasks" / f"{label}-{date_str}"
            task_folder.mkdir(parents=True, exist_ok=True)
            result_path = task_folder / "初筛结果.json"

            codes = []
            for s in stocks:
                raw = s.symbol.split(".")[0] if s.symbol else ""
                codes.append(raw)

            data = {
                "task_label": label,
                "timestamp": datetime.now().isoformat(),
                "method": method,
                "code_count": len(codes),
                "codes": sorted(codes),
            }
            if self.config.screening_code.strip():
                data["screening_code"] = self.config.screening_code
            else:
                data["pool_filters"] = [
                    {"field": r.field, "op": r.op, "value": r.value, "description": r.description}
                    for r in self.config.pool_filters
                ]

            result_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"初筛结果已保存：{result_path}，共 {len(codes)} 只股票")
            self.trace.add_note(f"初筛结果已保存：{result_path}，共 {len(codes)} 只股票")
        except Exception as exc:
            logger.error(f"保存初筛结果失败：{exc}", exc_info=True)
            self.trace.add_note(f"保存初筛结果失败：{exc}")

    def _run_screening_code(self, spot_df: pd.DataFrame, code: str) -> list[str]:
        """在受限命名空间中执行 LLM 生成的 Python 代码，返回标准化后的股票代码列表。

        允许访问 pandas/numpy 和传入的 df；内置函数使用白名单，避免危险操作。
        返回的代码统一使用 SymbolNormalizer 标准化为内部格式（如 600519.SH），
        避免列类型为整数时前导零丢失导致匹配失败。
        """
        safe_builtins = {
            "len": len,
            "range": range,
            "enumerate": enumerate,
            "zip": zip,
            "int": int,
            "float": float,
            "str": str,
            "bool": bool,
            "list": list,
            "dict": dict,
            "set": set,
            "tuple": tuple,
            "abs": abs,
            "min": min,
            "max": max,
            "sum": sum,
            "round": round,
            "sorted": sorted,
            "isinstance": isinstance,
            "hasattr": hasattr,
            "getattr": getattr,
            "__import__": __import__,
        }
        namespace = {
            "__builtins__": safe_builtins,
            "pd": pd,
            "np": np,
            "df": spot_df.copy(),
        }
        exec(code, namespace)  # noqa: S102
        result = namespace.get("result", [])

        if isinstance(result, pd.DataFrame):
            if "代码" not in result.columns:
                raise ValueError("screening_code 返回的 DataFrame 缺少 '代码' 列")
            codes = [SymbolNormalizer.normalize(str(c)) for c in result["代码"].tolist() if c]
        elif isinstance(result, pd.Series):
            codes = [SymbolNormalizer.normalize(str(c)) for c in result.tolist() if c]
        elif isinstance(result, (list, tuple, set)):
            codes = [SymbolNormalizer.normalize(str(c)) for c in result if c]
        else:
            raise ValueError(f"screening_code 返回的 result 类型不支持：{type(result)}")

        logger.info(f"LLM 初筛代码返回 {len(codes)} 只股票，示例：{codes[:5]}")
        return codes

    def _default_snapshot_screening(self, spot_df: pd.DataFrame) -> list[str]:
        """LLM 初筛代码失效时的默认快照粗筛。

        仅使用快照字段做最基础过滤：价格有效、成交额大于 1 亿、排除极端涨跌幅、
        按成交额排序取前 200 只。
        """
        df = spot_df.copy()
        df = df[df["最新价"].notna() & (df["最新价"] > 0)]
        df = df[df["成交额"].notna() & (df["成交额"] > 1e8)]
        df = df[(df["涨跌幅"] >= -9) & (df["涨跌幅"] <= 9)]
        df = df[(df["最新价"] >= 2) & (df["最新价"] <= 200)]
        df = df.sort_values("成交额", ascending=False).head(200)
        codes = [SymbolNormalizer.normalize(str(c)) for c in df["代码"].tolist() if c]
        logger.info(f"默认快照粗筛返回 {len(codes)} 只股票")
        return codes

    def _stock_code_in_set(self, stock: StockData, code_set: set[str]) -> bool:
        """判断股票标准化代码是否在初筛结果集合中。"""
        return stock.symbol in code_set

    def _matches_all_rules(self, stock: StockData, rules: list[FilterRule]) -> bool:
        """判断股票是否满足所有规则（规则满足为通过）。"""
        for rule in rules:
            if not self._evaluate_rule(stock, rule):
                return False
        return True

    def _limit_analyze_scope(self, stocks: list[StockData]) -> list[StockData]:
        """按成交额排序并限制分析数量。

        当 ``max_analyze <= 0`` 时分析全部股票，否则取前 ``max_analyze`` 只。
        """
        stocks = sorted(stocks, key=lambda s: s.get("turnover", 0) or 0, reverse=True)
        if self.config.max_analyze <= 0:
            return stocks
        max_n = max(1, self.config.max_analyze)
        return stocks[:max_n]

    def _fill_technical_data(self, stocks: list[StockData]) -> None:
        """分批获取并填充技术指标。"""
        if not self.kimi_provider or not stocks:
            return

        symbols = [s.symbol for s in stocks]
        batch_size = 3
        tech_map: dict[str, pd.Series] = {}

        for i in range(0, len(symbols), batch_size):
            batch = symbols[i : i + batch_size]
            start = time.perf_counter()
            try:
                df = self.kimi_provider.get_realtime_tech(batch)
                elapsed = time.perf_counter() - start
                status = "成功" if not df.empty else "部分成功"
                if not df.empty and "code" in df.columns:
                    for _, row in df.iterrows():
                        code = str(row["code"]).strip().upper()
                        normalized = SymbolNormalizer.normalize(code)
                        if normalized:
                            tech_map[normalized] = row
                    self._record_call(
                        phase="阶段2",
                        provider="stock_finance_data",
                        method="get_stock_realtime_price (realtime_tech)",
                        symbols=batch,
                        params={"indicator": "MA"},
                        status=status,
                        result_summary=f"返回 {len(df)} 条技术指标",
                        elapsed_seconds=elapsed,
                    )
                else:
                    self._record_call(
                        phase="阶段2",
                        provider="stock_finance_data",
                        method="get_stock_realtime_price (realtime_tech)",
                        symbols=batch,
                        params={"indicator": "MA"},
                        status=status,
                        result_summary="返回为空",
                        elapsed_seconds=elapsed,
                    )
            except Exception as exc:
                # 凭证错误需要向上传播，由策略层触发刷新重试
                if isinstance(exc, KimiAuthError):
                    self._record_call(
                        phase="阶段2",
                        provider="stock_finance_data",
                        method="get_stock_realtime_price (realtime_tech)",
                        symbols=batch,
                        params={"indicator": "MA"},
                        status="凭证过期",
                        result_summary=str(exc),
                        elapsed_seconds=time.perf_counter() - start,
                    )
                    raise
                self._record_call(
                    phase="阶段2",
                    provider="stock_finance_data",
                    method="get_stock_realtime_price (realtime_tech)",
                    symbols=batch,
                    params={"indicator": "MA"},
                    status="失败",
                    result_summary=str(exc),
                    elapsed_seconds=time.perf_counter() - start,
                )
                logger.warning(f"获取技术指标失败（批次 {i // batch_size + 1}）：{exc}")

        field_map = {
            "ma5": "ma5",
            "ma10": "ma10",
            "ma20": "ma20",
            "ma60": "ma60",
            "rsi6": "rsi6",
            "rsi12": "rsi12",
            "rsi14": "rsi14",
            "rsi24": "rsi24",
            "k": "k",
            "d": "d",
            "j": "j",
            "macd": "macd",
            "dif": "dif",
            "dea": "dea",
            "atr14": "atr14",
        }
        for stock in stocks:
            tech_row = tech_map.get(stock.symbol)
            if tech_row is None:
                continue
            for src, dst in field_map.items():
                stock.set(dst, self._to_float(tech_row.get(src)))

    def _fill_historical_data(self, stocks: list[StockData]) -> None:
        """批量获取并填充历史 K 线数据。"""
        if not stocks:
            return

        end_date = datetime.now()
        start_date = end_date - timedelta(days=90)
        end_str = end_date.strftime("%Y-%m-%d")
        start_str = start_date.strftime("%Y-%m-%d")

        symbols = [s.symbol for s in stocks]
        start = time.perf_counter()
        try:
            ohlcv_map = self.gateway.get_ohlcv_multi(
                symbols,
                period="1d",
                start_date=start_str,
                end_date=end_str,
                bars=60,
            )
            elapsed = time.perf_counter() - start
            self._record_call(
                phase="阶段3",
                provider="gateway",
                method="get_ohlcv_multi",
                symbols=symbols,
                params={"period": "1d", "start_date": start_str, "end_date": end_str, "bars": 60},
                result_summary=f"成功返回 {len(ohlcv_map)} 只股票的历史 K 线",
                elapsed_seconds=elapsed,
            )
        except Exception as exc:
            self._record_call(
                phase="阶段3",
                provider="gateway",
                method="get_ohlcv_multi",
                symbols=symbols,
                params={"period": "1d", "start_date": start_str, "end_date": end_str, "bars": 60},
                status="失败",
                result_summary=str(exc),
                elapsed_seconds=time.perf_counter() - start,
            )
            logger.warning(f"批量获取历史 K 线失败：{exc}")
            return

        for stock in stocks:
            df = ohlcv_map.get(stock.symbol)
            if df is None or df.empty or len(df) < 20:
                continue
            try:
                self._compute_historical_fields(stock, df)
            except Exception as exc:
                logger.debug(f"计算 {stock.symbol} 历史字段失败：{exc}")

    def _compute_historical_fields(self, stock: StockData, df: pd.DataFrame) -> None:
        """基于历史 K 线计算衍生字段。"""
        df = df.copy()
        df = df.sort_values("date").reset_index(drop=True)
        close = df["close"]

        price = stock.get("price")
        if price is None or price <= 0:
            return

        # 近 N 日累计涨跌幅
        for days in [5, 10, 20]:
            if len(close) >= days + 1:
                past = close.iloc[-(days + 1)]
                now = close.iloc[-1]
                stock.set(f"change_pct_{days}d", (now / past - 1) * 100)

        # 成交额均值
        if "turnover" not in df.columns and "amount" in df.columns:
            df["turnover"] = df["amount"]
        if "turnover" in df.columns:
            for days in [5, 20]:
                if len(df) >= days:
                    stock.set(f"turnover_{days}d_avg", df["turnover"].tail(days).mean())

        # 60日价格位置与止损线 proximity
        if len(close) >= 60:
            low60 = close.tail(60).min()
            high60 = close.tail(60).max()
            if high60 > low60:
                stock.set("price_position_60d", (price - low60) / (high60 - low60))
                stock.set("dist_to_60d_low_pct", (price - low60) / low60 * 100)
                stock.set("dist_to_60d_high_pct", (high60 - price) / high60 * 100)

        # 20日价格位置与止损线 proximity
        if len(close) >= 20:
            low20 = close.tail(20).min()
            high20 = close.tail(20).max()
            if high20 > low20:
                stock.set("price_position_20d", (price - low20) / (high20 - low20))
                stock.set("dist_to_20d_low_pct", (price - low20) / low20 * 100)
                stock.set("dist_to_20d_high_pct", (high20 - price) / high20 * 100)

    def _fill_capital_flow(self, stocks: list[StockData]) -> None:
        """获取并填充资金流向数据。"""
        success_count = 0
        for stock in stocks:
            start = time.perf_counter()
            try:
                df = self.gateway.get_capital_flow(stock.symbol, days=5)
                elapsed = time.perf_counter() - start
                if df.empty or "main_inflow" not in df.columns:
                    self._record_call(
                        phase="阶段4",
                        provider="akshare",
                        method="get_capital_flow",
                        symbols=[stock.symbol],
                        params={"days": 5},
                        status="部分成功",
                        result_summary="返回数据为空或缺少 main_inflow",
                        elapsed_seconds=elapsed,
                    )
                    continue
                inflows = df["main_inflow"].tail(5).tolist()
                stock.set("main_inflow_3d", sum(1 for v in inflows[-3:] if v > 0))
                stock.set("main_inflow_5d", sum(1 for v in inflows if v > 0))
                success_count += 1
                self._record_call(
                    phase="阶段4",
                    provider="akshare",
                    method="get_capital_flow",
                    symbols=[stock.symbol],
                    params={"days": 5},
                    result_summary="获取主力净流入天数",
                    elapsed_seconds=elapsed,
                )
            except Exception as exc:
                self._record_call(
                    phase="阶段4",
                    provider="akshare",
                    method="get_capital_flow",
                    symbols=[stock.symbol],
                    params={"days": 5},
                    status="失败",
                    result_summary=str(exc),
                    elapsed_seconds=time.perf_counter() - start,
                )
                logger.debug(f"获取 {stock.symbol} 资金流向失败：{exc}")
        if success_count:
            self.trace.add_note(f"资金流向数据：成功 {success_count}/{len(stocks)} 只")


    def _evaluate_stock(self, stock: StockData) -> ScanResult:
        """评估单只股票：否决、入选、评分。"""
        price = stock.get("price", 0) or 0
        change_pct = stock.get("change_pct", 0) or 0
        turnover = stock.get("turnover", 0) or 0

        # 一票否决：任一条件触发即剔除
        vetos = self._evaluate_rules(stock, self.config.veto_rules, fail_when_matched=True)

        # 硬性入选：必须全部满足
        entry_failures = []
        if not vetos:
            entry_failures = self._evaluate_rules(
                stock, self.config.entry_rules, fail_when_matched=False
            )

        passed = not vetos and not entry_failures

        # 评分：没有评分维度时，所有通过硬性条件的股票视为候选，不参与打分
        score_details: dict[str, float] = {}
        notes: list[str] = []
        total_score = 0.0
        scored = False

        if passed and self.config.score_dimensions:
            scored = True
            for dim in self.config.score_dimensions:
                dim_score, dim_notes = self._score_dimension(stock, dim)
                score_details[dim.name] = dim_score
                notes.extend(dim_notes)
                total_score += dim_score
        elif passed:
            notes.append("用户未提供评分标准，仅按硬性条件筛选，不做量化评分")

        recommendation = self._determine_recommendation(vetos, entry_failures, total_score, scored)

        return ScanResult(
            symbol=stock.symbol,
            strategy_name=self.config.name,
            matched_at=datetime.now(),
            match_score=total_score if scored else -1.0,
            trigger_data={"price": price, "change_pct": change_pct, "turnover": turnover},
            name=stock.name,
            price=price,
            change_pct=change_pct,
            turnover=turnover / 1e8 if turnover else 0,
            score_details=score_details,
            notes=notes,
            vetos=vetos + entry_failures,
            recommendation=recommendation,
        )

    def _evaluate_rules(
        self,
        stock: StockData,
        rules: list[FilterRule],
        fail_when_matched: bool,
    ) -> list[str]:
        """评估规则列表，返回失败原因的列表。

        Args:
            stock: 股票数据。
            rules: 规则列表。
            fail_when_matched: True 表示规则满足时视为失败（用于一票否决）；
                              False 表示规则不满足时视为失败（用于硬性入选）。
        """
        failures: list[str] = []
        for rule in rules:
            matched = self._evaluate_rule(stock, rule)
            is_failure = matched if fail_when_matched else not matched
            if is_failure:
                desc = rule.description or f"{rule.field} {rule.op} {rule.value}"
                failures.append(desc)
        return failures

    def _evaluate_rule(self, stock: StockData, rule: FilterRule) -> bool:
        """评估单条规则。"""
        value = stock.get(rule.field)
        if value is None:
            # 缺少数据时，宽松处理：非否决规则视为通过，否决规则视为不通过
            return rule not in self.config.veto_rules

        try:
            actual = float(value)
        except (ValueError, TypeError):
            return True

        target = rule.value
        op = rule.op

        if op in ("==", "="):
            return bool(actual == target)
        if op == "!=":
            return bool(actual != target)
        if op == ">":
            return bool(actual > target)
        if op == ">=":
            return bool(actual >= target)
        if op == "<":
            return bool(actual < target)
        if op == "<=":
            return bool(actual <= target)
        if op == "between":
            return bool(target[0] <= actual <= target[1])
        if op == "not_between":
            return bool(actual < target[0] or actual > target[1])

        return True

    def _score_dimension(self, stock: StockData, dim: ScoreDimension) -> tuple[float, list[str]]:
        """对单个维度评分。

        如果维度没有规则，则该维度不贡献分数（返回 0），避免所有股票得分一致。
        """
        if not dim.rules:
            return 0.0, []

        passed = 0
        notes: list[str] = []
        for rule in dim.rules:
            if self._evaluate_rule(stock, rule):
                passed += 1
                if rule.description:
                    notes.append(rule.description)

        score = dim.weight * (passed / len(dim.rules))
        return round(score, 2), notes

    def _determine_recommendation(
        self,
        vetos: list[str],
        entry_failures: list[str],
        score: float,
        scored: bool,
    ) -> str:
        """确定结论。"""
        if vetos:
            return "剔除"
        if entry_failures:
            return "剔除"
        if not scored:
            return "候选"
        if score >= self.config.min_recommend_score:
            return "推荐"
        if score >= self.config.min_watch_score:
            return "观察"
        return "剔除"

    @staticmethod
    def _to_float(value: Any) -> float | None:
        """安全转为浮点数。"""
        try:
            if value is None or (isinstance(value, float) and pd.isna(value)):
                return None
            return float(value)
        except (ValueError, TypeError):
            return None
