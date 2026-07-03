"""SQLite 持久化存储。"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from outluna.config import settings


class SQLiteStorage:
    """SQLite 持久化存储。"""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or settings.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()

    def _connect(self) -> sqlite3.Connection:
        """创建数据库连接。"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_tables(self) -> None:
        """初始化数据表。"""
        schema = """
        CREATE TABLE IF NOT EXISTS scan_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id TEXT UNIQUE NOT NULL,
            strategy_name TEXT NOT NULL,
            strategy_params TEXT,
            scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            market TEXT,
            total_scanned INTEGER,
            matched_count INTEGER,
            report_path TEXT
        );

        CREATE TABLE IF NOT EXISTS scan_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id TEXT REFERENCES scan_records(scan_id),
            symbol TEXT NOT NULL,
            match_score REAL,
            trigger_data TEXT
        );

        CREATE TABLE IF NOT EXISTS analysis_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id TEXT UNIQUE NOT NULL,
            symbol TEXT NOT NULL,
            strategy_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            risk_rating TEXT,
            recommendation TEXT,
            report_path TEXT
        );

        CREATE TABLE IF NOT EXISTS backtest_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            backtest_id TEXT UNIQUE NOT NULL,
            strategy_name TEXT NOT NULL,
            start_date DATE,
            end_date DATE,
            initial_capital REAL,
            total_return REAL,
            annualized_return REAL,
            win_rate REAL,
            max_drawdown REAL,
            sharpe_ratio REAL,
            total_trades INTEGER,
            result_path TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_scan_strategy ON scan_records(strategy_name);
        CREATE INDEX IF NOT EXISTS idx_analysis_symbol ON analysis_reports(symbol);
        CREATE INDEX IF NOT EXISTS idx_backtest_strategy ON backtest_records(strategy_name);
        """
        with self._connect() as conn:
            conn.executescript(schema)

    def save_scan_record(
        self,
        scan_id: str,
        strategy_name: str,
        strategy_params: dict[str, Any],
        total_scanned: int,
        matched_count: int,
        report_path: str,
        market: str = "A",
    ) -> None:
        """保存扫描记录。"""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO scan_records
                (scan_id, strategy_name, strategy_params, total_scanned, matched_count, report_path, market)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scan_id,
                    strategy_name,
                    json.dumps(strategy_params, ensure_ascii=False),
                    total_scanned,
                    matched_count,
                    report_path,
                    market,
                ),
            )

    def save_scan_matches(
        self,
        scan_id: str,
        matches: list[dict[str, Any]],
    ) -> None:
        """保存扫描命中记录。"""
        with self._connect() as conn:
            conn.execute("DELETE FROM scan_matches WHERE scan_id = ?", (scan_id,))
            for match in matches:
                conn.execute(
                    """
                    INSERT INTO scan_matches (scan_id, symbol, match_score, trigger_data)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        scan_id,
                        match.get("symbol"),
                        match.get("match_score"),
                        json.dumps(match.get("trigger_data", {}), ensure_ascii=False, default=str),
                    ),
                )

    def save_analysis_report(
        self,
        report_id: str,
        symbol: str,
        strategy_name: str,
        risk_rating: str,
        recommendation: str,
        report_path: str,
    ) -> None:
        """保存分析报告元数据。"""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO analysis_reports
                (report_id, symbol, strategy_name, risk_rating, recommendation, report_path)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (report_id, symbol, strategy_name, risk_rating, recommendation, report_path),
            )

    def save_backtest_record(
        self,
        backtest_id: str,
        strategy_name: str,
        start_date: datetime,
        end_date: datetime,
        initial_capital: float,
        total_return: float,
        annualized_return: float,
        win_rate: float,
        max_drawdown: float,
        sharpe_ratio: float,
        total_trades: int,
        result_path: str,
    ) -> None:
        """保存回测记录。"""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO backtest_records
                (backtest_id, strategy_name, start_date, end_date, initial_capital,
                 total_return, annualized_return, win_rate, max_drawdown, sharpe_ratio,
                 total_trades, result_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    backtest_id,
                    strategy_name,
                    start_date.strftime("%Y-%m-%d"),
                    end_date.strftime("%Y-%m-%d"),
                    initial_capital,
                    total_return,
                    annualized_return,
                    win_rate,
                    max_drawdown,
                    sharpe_ratio,
                    total_trades,
                    result_path,
                ),
            )

    def list_reports(self, report_type: str | None = None) -> list[dict[str, Any]]:
        """列出报告。"""
        queries = {
            "scan": "SELECT scan_id as report_id, strategy_name as title, scanned_at as created_at, 'scan' as report_type FROM scan_records ORDER BY scanned_at DESC",
            "analysis": "SELECT report_id, symbol as title, created_at, 'analysis' as report_type FROM analysis_reports ORDER BY created_at DESC",
            "backtest": "SELECT backtest_id as report_id, strategy_name as title, created_at, 'backtest' as report_type FROM backtest_records ORDER BY created_at DESC",
        }

        with self._connect() as conn:
            if report_type and report_type in queries:
                rows = conn.execute(queries[report_type]).fetchall()
            else:
                rows = []
                for query in queries.values():
                    rows.extend(conn.execute(query).fetchall())

            return [
                {
                    "report_id": row["report_id"],
                    "report_type": row["report_type"],
                    "created_at": row["created_at"],
                    "title": row["title"],
                }
                for row in rows
            ]
