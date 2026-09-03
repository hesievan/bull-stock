"""watchlist_report.py 单元测试 — 腾讯行情解析 + 本地历史读取"""

import sqlite3
import sys
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

import scripts.watchlist_report as wl  # noqa: E402


def _tencent_line(overrides: dict | None = None) -> str:
    """构造 45+ 字段的腾讯行情行(字段下标对齐 qt.gtimg.cn 约定)。"""
    fields = [""] * 45
    base = {
        1: "顺丰控股",
        2: "002352",
        3: "42.50",
        4: "42.00",
        5: "42.10",
        6: "123456.00",
        32: "2.31",
        33: "43.00",
        34: "41.80",
        37: "56789.00",
    }
    base.update(overrides or {})
    for idx, val in base.items():
        fields[idx] = str(val)
    return 'v_sz002352="' + "~".join(fields) + '"'


class TestParseTencentLine:
    def test_normal_line(self):
        q = wl.parse_tencent_line(_tencent_line())
        assert q["name"] == "顺丰控股"
        assert q["code"] == "002352"
        assert q["close"] == 42.50
        assert q["chg"] == 2.31
        assert q["vol"] == 123456.0
        assert q["amount"] == 56789.0
        assert q["high"] == 43.00
        assert q["low"] == 41.80

    def test_short_line_returns_none(self):
        assert wl.parse_tencent_line('v_sz002352="1~2~3"') is None

    def test_empty_line_returns_none(self):
        assert wl.parse_tencent_line("") is None
        assert wl.parse_tencent_line("\n") is None

    def test_bad_number_returns_none(self):
        assert wl.parse_tencent_line(_tencent_line({3: "abc"})) is None

    def test_empty_optional_fields_default_zero(self):
        q = wl.parse_tencent_line(_tencent_line({5: "", 33: "", 34: ""}))
        assert q["open"] == 0
        assert q["high"] == 0
        assert q["low"] == 0


class TestFetchHistory:
    def test_returns_sorted_tail(self, tmp_path, monkeypatch):
        db = tmp_path / "test.db"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE stock_daily (trade_date TEXT, stock_code TEXT, close REAL, volume REAL, amount REAL)"
        )
        rows = []
        for i in range(10):
            d = (date(2026, 1, 1) + timedelta(days=i)).isoformat()
            rows.append((d, "sz002352", 10 + i, 1000 + i, 10000 + i))
        conn.executemany("INSERT INTO stock_daily VALUES (?,?,?,?,?)", rows)
        conn.commit()
        conn.close()

        @contextmanager
        def _fake_get_conn():
            c = sqlite3.connect(db)
            try:
                yield c
            finally:
                c.close()

        monkeypatch.setattr(wl, "get_conn", _fake_get_conn)
        df = wl.fetch_history("sz002352", days=5)
        assert len(df) == 5
        # 升序且取最近 days 条
        assert list(df["trade_date"]) == sorted(df["trade_date"])
        assert df["close"].iloc[-1] == 19.0

    def test_missing_stock_returns_empty(self, tmp_path, monkeypatch):
        db = tmp_path / "empty.db"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE stock_daily (trade_date TEXT, stock_code TEXT, close REAL, volume REAL, amount REAL)"
        )
        conn.commit()
        conn.close()

        @contextmanager
        def _fake_get_conn():
            c = sqlite3.connect(db)
            try:
                yield c
            finally:
                c.close()

        monkeypatch.setattr(wl, "get_conn", _fake_get_conn)
        df = wl.fetch_history("sz000001", days=5)
        assert df.empty
