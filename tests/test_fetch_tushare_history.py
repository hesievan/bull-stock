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

from scripts.fetch_tushare_history import _default_start, main  # noqa: E402
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


class TestMainDefaultStart:
    """回归 (2026-09-01 二次修复): argparse 曾把 --start 默认值写死为 '2015-01-01',
    main(start=args.start) 收到的永远不是 None, 导致 _default_start() 从未被调用,
    增量模式形同虚设, rebuild 依旧全量重拉超时。"""

    def _mock_main_deps(self, monkeypatch):
        """mock main() 的 tushare/akshare 依赖, 只保留循环与库交互。

        main() 内部是函数级 `from src.data.fetcher import ...`, 必须 patch
        src.data.fetcher 模块上的属性才会生效。
        """
        import src.data.fetcher as fetcher

        monkeypatch.setattr(fetcher, "fetch_all_index_incremental", lambda: None)
        monkeypatch.setattr(fetcher, "fetch_margin_history", lambda *a, **k: None)
        monkeypatch.setattr(fetcher, "fetch_northbound_history", lambda *a, **k: None)
        monkeypatch.setattr(fetcher, "fetch_bond_yield_history", lambda *a, **k: None)
        monkeypatch.setattr(fetcher, "fetch_m2_history", lambda *a, **k: None)
        monkeypatch.setattr(fetcher, "fetch_m1_history", lambda: None)
        monkeypatch.setattr(fetcher, "_save", lambda *a, **k: None)
        return fetcher

    def test_main_none_triggers_incremental(self, tmp_path, monkeypatch):
        """main(start=None): 必须走增量起点 (首个交易日 ≈ 最新日回推 30 天后)。"""
        import src.data.database as db
        from src.data.database import init_database

        db_path = str(tmp_path / "t.db")
        monkeypatch.setattr(db, "DB_PATH", db_path)
        monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
        init_database(db_path)
        with get_conn(db_path) as conn:
            conn.execute("INSERT INTO stock_daily (trade_date, stock_code) VALUES ('2026-08-11', '600000')")

        fetcher = self._mock_main_deps(monkeypatch)
        first_day = {}

        def fake_daily(td):
            first_day.setdefault("td", td)
            return 0

        monkeypatch.setattr(fetcher, "fetch_daily_basic_to_stock_daily", fake_daily)

        main(start=None)

        # 2026-08-11 回推 30 天 = 2026-07-12 (周日), 首个交易日 2026-07-13
        assert first_day["td"] == "2026-07-13"

    def test_main_explicit_start_stays_full(self, tmp_path, monkeypatch):
        """main(start='2015-01-01'): 显式全量应保留 (首个交易日 2015-01-01)。"""
        import src.data.database as db
        from src.data.database import init_database

        db_path = str(tmp_path / "t.db")
        monkeypatch.setattr(db, "DB_PATH", db_path)
        monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
        init_database(db_path)

        fetcher = self._mock_main_deps(monkeypatch)
        first_day = {}

        def fake_daily(td):
            first_day.setdefault("td", td)
            return 0

        monkeypatch.setattr(fetcher, "fetch_daily_basic_to_stock_daily", fake_daily)

        main(start="2015-01-01")

        assert first_day["td"] == "2015-01-01"
