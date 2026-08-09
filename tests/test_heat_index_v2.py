"""V2 引擎单元测试 — heat_index_v2 核心指标

覆盖: calc_pe / calc_margin_ratio_v2 / calc_ma_alignment_v2 / _apply_sentiment_divergence
及其余 V2 函数。每个测试使用临时 SQLite 库, 不触碰生产数据。
"""
import math
import sqlite3

import pytest

from src.data.database import init_database
from src.indicators.heat_index_v2 import (
    NEW_HIGH_THRESHOLD,
    calc_erp_v2,
    calc_margin_ratio_v2,
    calc_ma_alignment_v2,
    calc_new_high_v2,
    calc_pe,
    calc_turnover_v2,
    _apply_sentiment_divergence,
    _pct_rank,
)


@pytest.fixture
def v2_db(tmp_path):
    """临时数据库 + 原生 sqlite3 连接 (calc_* 函数均接收 sqlite3.Connection)"""
    db_path = str(tmp_path / "v2.db")
    init_database(db_path)
    conn = sqlite3.connect(db_path)
    yield conn
    conn.close()


def _dates(start: str, n: int, step_days: int = 1):
    """生成连续日期列表 YYYY-MM-DD"""
    from datetime import date, timedelta
    d = date.fromisoformat(start)
    out = []
    for _ in range(n):
        out.append(d.isoformat())
        d += timedelta(days=step_days)
    return out


# ── 工具函数 ────────────────────────────────────────────────────────────────

class TestPctRank:
    def test_mid_value(self):
        assert _pct_rank([1, 2, 3, 4, 5], 3) == pytest.approx(0.4)

    def test_empty_returns_half(self):
        assert _pct_rank([], 5) == 0.5

    def test_ignores_nan(self):
        assert _pct_rank([1.0, float("nan"), 3.0], 2.0) == pytest.approx(0.5)


# ── F2: 情绪背离单次扣分 (方案B) ─────────────────────────────────────────────

class TestSentimentDivergence:
    def test_single_penalty_only_turnover(self, v2_db):
        """只扣换手率(turnover), turnover_m2 不变, 总额=20分"""
        td = "2026-08-06"
        v2_db.executemany(
            "INSERT INTO index_daily (trade_date, index_code, close) VALUES (?, 'sh000001', ?)",
            [("2026-07-20", 100.0), (td, 98.0)],  # 指数 -2% < -1.5% 触发
        )
        v2_db.commit()
        scores = {"turnover_m2": 80.0, "turnover": 85.0}
        out = _apply_sentiment_divergence(v2_db, td, scores)
        assert out["turnover"] == pytest.approx(65.0)   # 85 - 20
        assert out["turnover_m2"] == pytest.approx(80.0)  # 不再被扣

    def test_no_penalty_when_turnover_low(self, v2_db):
        td = "2026-08-06"
        v2_db.executemany(
            "INSERT INTO index_daily (trade_date, index_code, close) VALUES (?, 'sh000001', ?)",
            [("2026-07-20", 100.0), (td, 98.0)],
        )
        v2_db.commit()
        scores = {"turnover_m2": 60.0, "turnover": 65.0}  # 均 ≤70 不触发
        out = _apply_sentiment_divergence(v2_db, td, scores)
        assert out == scores

    def test_no_penalty_when_index_rising(self, v2_db):
        td = "2026-08-06"
        v2_db.executemany(
            "INSERT INTO index_daily (trade_date, index_code, close) VALUES (?, 'sh000001', ?)",
            [("2026-07-20", 98.0), (td, 100.0)],  # 指数 +2% 不触发
        )
        v2_db.commit()
        scores = {"turnover_m2": 80.0, "turnover": 85.0}
        out = _apply_sentiment_divergence(v2_db, td, scores)
        assert out == scores


# ── F4: 两融余额高分位单调饱和 ───────────────────────────────────────────────

class TestMarginRatioSaturation:
    def _seed(self, v2_db, n_hist=100):
        """历史 rzye 线性递增 + 每日流通市值 → 当前值处历史最高分位 (pct≈0.99)"""
        td = "2026-08-06"
        circ = 1e8  # 万元 → 对应流通市值 1e12 元
        hist_dates = _dates("2026-02-01", n_hist, 1)
        for i, d in enumerate(hist_dates):
            v2_db.execute("INSERT INTO daily_circ_mv (trade_date, total_circ_mv) VALUES (?, ?)",
                          (d, circ))
            rzye = 100e9 + i * 9e9  # 1000亿 → 991亿 递增
            v2_db.execute("INSERT INTO margin_history (trade_date, rzye, rqye) VALUES (?, ?, 0)",
                          (d, rzye))
        v2_db.execute("INSERT INTO daily_circ_mv (trade_date, total_circ_mv) VALUES (?, ?)",
                      (td, circ))
        v2_db.execute("INSERT INTO margin_history (trade_date, rzye, rqye) VALUES (?, ?, 0)",
                      (td, 1000e9))  # 当前 = 历史最高
        v2_db.commit()

    def test_high_percentile_not_collapsed(self, v2_db):
        """pct≈0.99 时分数应保持高位 (≥90), 原实现 900*(1-pct)≈9分"""
        self._seed(v2_db)
        res = calc_margin_ratio_v2(v2_db, "2026-08-06")
        assert res is not None
        score, cur_ratio = res
        assert score >= 90.0
        assert cur_ratio > 0

    def test_monotonicity_by_construction(self):
        """饱和函数本身单调不减 (直接验证公式)"""
        prev = -1
        for pct in [x / 100 for x in range(50, 100)]:
            if pct <= 0.85:
                s = pct * 100
            else:
                s = (0.85 + 0.15 * (1 - math.exp(-(pct - 0.85) * 20))) * 100
            assert s >= prev, f"pct={pct}: score={s} < prev={prev}"
            prev = s
        assert (0.85 + 0.15 * (1 - math.exp(-(0.95 - 0.85) * 20))) * 100 >= 95


# ── F5: PE n_stocks 过滤收紧 ────────────────────────────────────────────────

class TestPeNStocksFilter:
    def test_modern_filters_out_legacy(self, v2_db):
        """现代口径(cur_n=722): 过滤掉 n=300 的旧数据, 分数基于 n>=450 序列"""
        td = "2026-08-06"
        # 100 条现代数据 n=722, pe 5.0~8.4 (均 < cur_pe)
        for i, d in enumerate(_dates("2016-09-01", 100)):
            v2_db.execute("INSERT INTO index_daily_pe (trade_date, pe_med, n_stocks) VALUES (?, ?, 722)",
                          (d, round(5.0 + i * 0.034, 4)))
        # 20 条旧口径 n=300, pe 较高 (旧代码会混入压低 pct)
        for i, d in enumerate(_dates("2017-02-01", 20)):
            v2_db.execute("INSERT INTO index_daily_pe (trade_date, pe_med, n_stocks) VALUES (?, 15.0, 300)",
                          (d,))
        # 当前值
        v2_db.execute("INSERT INTO index_daily_pe (trade_date, pe_med, n_stocks) VALUES (?, 10.0, 722)",
                      (td,))
        v2_db.commit()

        res = calc_pe(v2_db, td)
        assert res is not None
        score, cur_pe = res
        # 新代码: 100条n=722全<10 + 当前行(=10不计数) → pct=100/101≈99.0 → 99分
        # 旧代码: 120条中100条<10 → pct≈0.826 → 82.6分
        assert score >= 98.0, f"got {score} (old code ≈82.6)"
        assert cur_pe == pytest.approx(10.0)

    def test_early_era_no_collapse(self, v2_db):
        """早期口径(cur_n=300): 下限保护不触发, 过滤范围不坍缩, 分数正常"""
        td = "2015-06-12"
        # 120 条 n=150~450 的历史 (pe 5.0~8.4), 模拟 2015 早期成分
        for i, d in enumerate(_dates("2014-06-01", 120, 3)):
            v2_db.execute("INSERT INTO index_daily_pe (trade_date, pe_med, n_stocks) VALUES (?, ?, ?)",
                          (d, round(5.0 + i * 0.028, 4), 150 + i * 2))
        v2_db.execute("INSERT INTO index_daily_pe (trade_date, pe_med, n_stocks) VALUES (?, 10.0, 300)",
                      (td,))
        v2_db.commit()

        res = calc_pe(v2_db, td)
        assert res is not None
        score, _ = res
        # 含当前行: pct=120/121≈99.2 → 99分; 若过滤坍缩会返回 None
        assert score >= 98.0


# ── F6: MA fallback 收敛 ────────────────────────────────────────────────────

class TestMaAlignmentFallback:
    def _seed(self, v2_db, cur_val):
        td = "2026-08-06"
        for i, d in enumerate(_dates("2026-05-01", 10)):  # 仅 10 条 < 60 → 触发 fallback
            v2_db.execute("INSERT INTO daily_ma_alignment (trade_date, ma_alignment_ratio) VALUES (?, 0.5)",
                          (d,))
        v2_db.execute("INSERT INTO daily_ma_alignment (trade_date, ma_alignment_ratio) VALUES (?, ?)",
                      (td, cur_val))
        v2_db.commit()

    def test_fallback_clamped_high(self, v2_db):
        self._seed(v2_db, 0.90)
        res = calc_ma_alignment_v2(v2_db, "2026-08-06")
        assert res is not None
        score, cur_val = res
        assert score == pytest.approx(80.0)  # min(90, 80) 封顶
        assert cur_val == pytest.approx(0.90)

    def test_fallback_clamped_low(self, v2_db):
        self._seed(v2_db, 0.10)
        res = calc_ma_alignment_v2(v2_db, "2026-08-06")
        assert res is not None
        score, cur_val = res
        assert score == pytest.approx(20.0)  # max(10, 20) 托底
        assert cur_val == pytest.approx(0.10)

    def test_normal_path_uses_percentile(self, v2_db):
        """历史充足时走百分位路径, 不受收敛影响"""
        td = "2026-08-06"
        for i, d in enumerate(_dates("2016-09-01", 100)):
            v2_db.execute("INSERT INTO daily_ma_alignment (trade_date, ma_alignment_ratio) VALUES (?, ?)",
                          (d, round(0.1 + i * 0.008, 4)))
        v2_db.execute("INSERT INTO daily_ma_alignment (trade_date, ma_alignment_ratio) VALUES (?, 0.90)",
                      (td,))
        v2_db.commit()
        res = calc_ma_alignment_v2(v2_db, td)
        assert res is not None
        score, _ = res
        # cur 为历史最高(含当前行计数) → pct=100/101≈99.0 → 99分, 走百分位路径
        assert score >= 98.0


# ── 其他 V2 指标冒烟测试 ─────────────────────────────────────────────────────

class TestOtherIndicatorsSmoke:
    def test_erp_missing_data_returns_none(self, v2_db):
        assert calc_erp_v2(v2_db, "2026-08-06") is None

    def test_new_high_insufficient_data_none(self, v2_db):
        assert calc_new_high_v2(v2_db, "2026-08-06") is None

    def test_turnover_insufficient_data_none(self, v2_db):
        assert calc_turnover_v2(v2_db, "2026-08-06") is None
