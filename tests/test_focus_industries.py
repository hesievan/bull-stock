"""Tests for src/indicators/focus_industries.py — 重点行业热度 (含一/二级混算)

覆盖:
  - 配置一致性 (FOCUS_SW_CODES / SW_NAME_MAP / SW_LEVEL_MAP)
  - _get_hist_industry_data 一/二级 OR 过滤逻辑
  - compute_focus_industries 端到端 (mock akshare, 验证 l2 字段与二级估值填充)
"""

import sqlite3
from datetime import date, timedelta

import pandas as pd
import pytest

from src.data import database as dbmod
from src.indicators import focus_industries as fi

TD = "2026-08-11"
PREV = (date.fromisoformat(TD) - timedelta(days=1)).isoformat()

# 食品饮料(801120) 含一只白酒(801125); 非银金融(801790) 含一只保险(801194)
STOCKS = [
    ("sh600000", "801120", "食品饮料", None, None),
    ("sh600519", "801120", "食品饮料", "801125", "白酒"),
    ("sh601318", "801790", "非银金融", "801194", "保险"),
    ("sh600036", "801790", "非银金融", None, None),
]
NAMES = [("sh600000", "食品A"), ("sh600519", "茅台"), ("sh601318", "平安"), ("sh600036", "招行")]
CLOSES = {"sh600000": 10.0, "sh600519": 1800.0, "sh601318": 50.0, "sh600036": 40.0}


@pytest.fixture
def focus_db(tmp_path):
    """临时库 + 完整 schema(v12 索引) + 最小样本数据"""
    db_path = str(tmp_path / "focus.db")
    dbmod.init_database(db_path)
    conn = sqlite3.connect(db_path)
    conn.executemany(
        "INSERT INTO stock_shenwan (stock_code,sw_code,sw_name,sw_l2_code,sw_l2_name) VALUES (?,?,?,?,?)",
        STOCKS,
    )
    conn.executemany("INSERT INTO stock_industry (code,code_name) VALUES (?,?)", NAMES)
    for d in (PREV, TD):
        for code, c in CLOSES.items():
            conn.execute(
                "INSERT INTO stock_daily "
                "(trade_date,stock_code,close,pct_change,peTTM,pbMRQ,turnover_rate,total_mv,amount) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (d, code, c, 1.0, 20.0, 3.0, 2.0, 1000.0, 500.0),
            )
    conn.commit()
    yield db_path
    conn.close()


# ── 配置一致性 (纯函数, 守护本次配置改动) ────────────────────────────────────
class TestConfigConsistency:
    def test_code_count_and_name_map(self):
        assert len(fi.FOCUS_SW_CODES) == 12
        for c in fi.FOCUS_SW_CODES:
            assert c in fi.SW_NAME_MAP

    def test_l2_marked(self):
        assert fi.SW_LEVEL_MAP["801125"] == "l2"  # 白酒
        assert fi.SW_LEVEL_MAP["801194"] == "l2"  # 保险

    def test_l2_parents_present(self):
        # 二级行业的父一级必须在配置中 (sw_code 归属依赖此)
        assert "801120" in fi.FOCUS_SW_CODES  # 白酒父: 食品饮料
        assert "801790" in fi.FOCUS_SW_CODES  # 保险父: 非银金融

    def test_level_map_subset_of_codes(self):
        for c in fi.SW_LEVEL_MAP:
            assert c in fi.FOCUS_SW_CODES


# ── 历史查询一/二级 OR 过滤 (核心改动点) ──────────────────────────────────────
class TestHistFilter:
    def test_l2_only(self, focus_db):
        conn = sqlite3.connect(focus_db)
        hist = fi._get_hist_industry_data(conn, TD, ["801125"])
        assert set(hist["stock_code"]) == {"sh600519"}
        assert (hist["sw_l2_code"] == "801125").all()
        conn.close()

    def test_l1_only_includes_l2_member(self, focus_db):
        # 一级 801120 过滤按 sw_code, 应同时命中普通成员与白酒成员
        conn = sqlite3.connect(focus_db)
        hist = fi._get_hist_industry_data(conn, TD, ["801120"])
        assert set(hist["stock_code"]) == {"sh600000", "sh600519"}
        conn.close()

    def test_mixed_l1_l2_union(self, focus_db):
        conn = sqlite3.connect(focus_db)
        hist = fi._get_hist_industry_data(conn, TD, ["801125", "801790"])
        assert set(hist["stock_code"]) == {"sh600519", "sh601318", "sh600036"}
        conn.close()


# ── 端到端 (mock akshare, 验证 l2 字段 + 二级估值填充) ────────────────────────
class _FakeAK:
    @staticmethod
    def index_hist_sw(symbol, period="day"):
        return pd.DataFrame({"日期": [PREV, TD], "收盘": [100.0, 102.0]})

    @staticmethod
    def sw_index_first_info():
        return pd.DataFrame(
            {
                "行业代码": ["801120.SI", "801790.SI"],
                "静态市盈率": [20, 10],
                "TTM(滚动)市盈率": [21, 11],
                "市净率": [3, 1],
                "静态股息率": [2, 3],
            }
        )

    @staticmethod
    def sw_index_second_info():
        return pd.DataFrame(
            {
                "行业代码": ["801125.SI", "801194.SI"],
                "静态市盈率": [19, 6],
                "TTM(滚动)市盈率": [19, 6],
                "市净率": [3, 1],
                "静态股息率": [5, 3],
            }
        )


class TestComputeE2E:
    def test_compute_marks_l2_and_fills_second_info(self, focus_db, monkeypatch):
        import sys

        monkeypatch.setitem(sys.modules, "akshare", _FakeAK)

        results = fi.compute_focus_industries(TD, focus_db)
        assert results, "compute returned empty"

        by_name = {r["sw_name"]: r for r in results}
        # 二级字段
        assert by_name["白酒"]["sw_level"] == "l2"
        assert by_name["保险"]["sw_level"] == "l2"
        # 成分股数: 白酒仅 1 只, 食品饮料含白酒共 2 只
        assert by_name["白酒"]["n_stocks"] == 1
        assert by_name["食品饮料"]["n_stocks"] == 2
        # 二级估值来自 sw_index_second_info
        assert by_name["白酒"]["index_pe_ttm"] == 19
        assert by_name["保险"]["index_pb"] == 1
        # 一级估值来自 sw_index_first_info
        assert by_name["食品饮料"]["index_pe_ttm"] == 21
