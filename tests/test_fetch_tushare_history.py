"""Tests for scripts/fetch_tushare_history.py — 历史回补脚本

回归 (2026-09-01): rebuild_seed 工作流超时 — 原默认固定 2015-01-01 全量逐日重拉
(~2.6s/日 × 3044 日 ≈ 2.2h > 120min), 而跳过条件 (existing>7000) 在单日全市场
~5400 只的情况下永不满足。修复: 默认改为"取 stock_daily 最新交易日回推 30 天"的
增量起点, 空库/异常时回退 2015-01-01 全量首建。
"""

import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from scripts.fetch_tushare_history import _default_start  # noqa: E402
from src.data.database import get_conn, init_database  # noqa: E402


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """临时数据库, 并把 src.data.database.DB_PATH 指过去。"""
    db_path = str(tmp_path / "test.db")
    init_database(db_path)
    import src.data.database as db

    monkeypatch.setattr(db, "DB_PATH", db_path)
    return db_path


class TestDefaultStart:
    def test_with_data_returns_latest_minus_30d(self, tmp_db):
        """有数据: 返回 MAX(trade_date) 回推 30 天的增量起点。"""
        latest = "2026-08-11"
        with get_conn(tmp_db) as conn:
            conn.execute(
                "INSERT INTO stock_daily (trade_date, stock_code) VALUES (?, '600000')",
                (latest,),
            )
        expected = (date.fromisoformat(latest) - timedelta(days=30)).isoformat()
        assert _default_start() == expected

    def test_empty_db_returns_full_start(self, tmp_db):
        """空库: 回退 2015-01-01 全量首建。"""
        assert _default_start() == "2015-01-01"

    def test_db_error_returns_full_start(self, tmp_path, monkeypatch):
        """数据库异常: 回退 2015-01-01, 不抛异常。"""
        import src.data.database as db

        # 指向无法打开的路径 (目录不存在)
        monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "no_such_dir" / "x.db"))
        assert _default_start() == "2015-01-01"
