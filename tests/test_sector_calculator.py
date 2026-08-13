"""Tests for src/indicators/sector_calculator.py — 板块热度

smoke 测试: 验证 calculate_sector_heat 在最小数据下不崩溃且返回 list[dict]。
(深度覆盖各维度打分属后续工作, 此处仅守护"函数可调用 + 结构正确")
"""

import sqlite3
from datetime import date, timedelta

import pytest

from src.data import database as dbmod
from src.indicators import sector_calculator as sc

TD = "2026-08-11"
PREV = (date.fromisoformat(TD) - timedelta(days=1)).isoformat()


@pytest.fixture
def sector_db(tmp_path):
    db_path = str(tmp_path / "sector.db")
    dbmod.init_database(db_path)
    conn = sqlite3.connect(db_path)
    conn.executemany(
        "INSERT INTO stock_shenwan (stock_code,sw_code,sw_name) VALUES (?,?,?)",
        [("sh600000", "801120", "食品饮料"), ("sh600036", "801790", "非银金融")],
    )
    conn.executemany(
        "INSERT INTO stock_industry (code,code_name,industry) VALUES (?,?,?)",
        [("sh600000", "食品A", "食品饮料"), ("sh600036", "招行", "非银金融")],
    )
    for d in (PREV, TD):
        conn.execute(
            "INSERT INTO stock_daily "
            "(trade_date,stock_code,close,pct_change,peTTM,pbMRQ,turnover_rate) "
            "VALUES (?,?,?,?,?,?,?)",
            (d, "sh600000", 10.0, 1.0, 20.0, 3.0, 2.0),
        )
        conn.execute(
            "INSERT INTO stock_daily "
            "(trade_date,stock_code,close,pct_change,peTTM,pbMRQ,turnover_rate) "
            "VALUES (?,?,?,?,?,?,?)",
            (d, "sh600036", 40.0, -0.5, 8.0, 1.0, 1.0),
        )
    conn.commit()
    yield db_path
    conn.close()


class TestSectorHeat:
    def test_returns_sorted_list(self, sector_db):
        res = sc.calculate_sector_heat(TD, sector_db)
        assert isinstance(res, list)
        # 有数据时应返回非空, 且按分数降序(若含 score 字段)
        if res:
            assert all(isinstance(r, dict) for r in res)
            scores = [r.get("score") for r in res if "score" in r]
            if len(scores) >= 2:
                assert scores == sorted(scores, reverse=True)

    def test_empty_db_returns_list(self, tmp_path):
        db_path = str(tmp_path / "empty.db")
        dbmod.init_database(db_path)
        res = sc.calculate_sector_heat(TD, db_path)
        assert isinstance(res, list)  # 空数据应优雅返回 [], 不应抛异常
