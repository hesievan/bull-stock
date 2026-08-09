"""V2 引擎单元测试 — heat_index_v2 核心指标

覆盖: calc_pe / calc_erp_v2 / calc_buffett / calc_margin_ratio_v2 / calc_ma_alignment_v2
calc_new_high_v2 / calc_turnover_v2 / 背离函数 / compute_index_v2 端到端。
每个测试使用临时 SQLite 库, 不触碰生产数据。
"""
import math
import sqlite3

import pytest

from src.data.database import init_database
from src.indicators.heat_index_v2 import (
    INDICATOR_WEIGHTS,
    NEW_HIGH_THRESHOLD,
    _apply_new_high_divergence,
    calc_buffett,
    calc_erp_v2,
    calc_margin_ratio_v2,
    calc_ma_alignment_v2,
    calc_new_high_v2,
    calc_pe,
    calc_turnover_v2,
    compute_index_v2,
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


def _months(start: str, n: int):
    """生成连续月份列表 YYYY-MM (m2_monthly 主键为月份, 不能用 step_days=30 凑)"""
    y, m = map(int, start.split("-"))
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
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


# ── F1/F8: 新高占比预计算表 + 10年百分位 + 背离去重 ─────────────────────────

class TestNewHighPercentileScoring:
    def _seed_history(self, v2_db, ratios, td):
        """种 daily_new_high 历史序列 + 当前日"""
        for i, d in enumerate(_dates("2016-09-01", len(ratios))):
            v2_db.execute(
                "INSERT INTO daily_new_high (trade_date, new_high_ratio, n_stocks) VALUES (?, ?, 500)",
                (d, ratios[i]),
            )
        v2_db.execute(
            "INSERT INTO daily_new_high (trade_date, new_high_ratio, n_stocks) VALUES (?, ?, 500)",
            (td, ratios[-1]),
        )
        v2_db.commit()

    def test_high_ratio_scores_high(self, v2_db):
        """当前=历史最高 ratio → 高分 (>90)"""
        td = "2026-08-06"
        ratios = [round(0.05 + i * 0.009, 4) for i in range(100)]  # 0.05 → 0.941 递增
        self._seed_history(v2_db, ratios, td)
        res = calc_new_high_v2(v2_db, td)
        assert res is not None
        score, cur_ratio = res
        # 100条历史全<cur + 当前行(=cur 不计数) → pct=100/101≈0.99 → 99分
        assert score >= 90.0
        assert cur_ratio == pytest.approx(ratios[-1])

    def test_low_ratio_scores_low(self, v2_db):
        """当前=历史最低 ratio → 低分 (<10)"""
        td = "2026-08-06"
        ratios = [round(0.9 - i * 0.008, 4) for i in range(100)]  # 0.9 → 0.108 递减
        v2_db.executemany(
            "INSERT INTO daily_new_high (trade_date, new_high_ratio, n_stocks) VALUES (?, ?, 500)",
            [(d, ratios[i]) for i, d in enumerate(_dates("2016-09-01", 100))],
        )
        v2_db.execute(
            "INSERT INTO daily_new_high (trade_date, new_high_ratio, n_stocks) VALUES (?, ?, 500)",
            (td, ratios[-1]),
        )
        v2_db.commit()
        res = calc_new_high_v2(v2_db, td)
        assert res is not None
        score, cur_ratio = res
        assert score <= 10.0
        assert cur_ratio == pytest.approx(ratios[-1])

    def test_insufficient_history_returns_none(self, v2_db):
        """历史不足 60 条 → 返回 None (宁缺毋滥, 与 PE/ERP 一致)"""
        td = "2026-08-06"
        for i, d in enumerate(_dates("2026-05-01", 30)):
            v2_db.execute(
                "INSERT INTO daily_new_high (trade_date, new_high_ratio, n_stocks) VALUES (?, 0.5, 500)",
                (d,),
            )
        v2_db.execute(
            "INSERT INTO daily_new_high (trade_date, new_high_ratio, n_stocks) VALUES (?, 0.6, 500)",
            (td,),
        )
        v2_db.commit()
        assert calc_new_high_v2(v2_db, td) is None


class TestNewHighDivergencePrecompute:
    def _seed_index(self, v2_db, td, prev_close, cur_close):
        """指数: prev 日与当前日 (不含 prev 日当天 → 用 <= 取最近)"""
        v2_db.execute(
            "INSERT INTO index_daily (trade_date, index_code, close) VALUES (?, 'sh000001', ?)",
            (prev_close[0], prev_close[1]),
        )
        v2_db.execute(
            "INSERT INTO index_daily (trade_date, index_code, close) VALUES (?, 'sh000001', ?)",
            (td, cur_close),
        )
        v2_db.commit()

    def test_divergence_penalty_applied(self, v2_db):
        """指数涨>3% + 新高占比下降>5% + 当前<30% → 扣 15 分"""
        td = "2026-08-06"
        prev_td = "2026-07-17"  # td - 20 天
        # 新高占比: 20 天前 0.30 (30%) → 当前 0.20 (20%) → 下降 10% > 5%
        v2_db.execute(
            "INSERT INTO daily_new_high (trade_date, new_high_ratio, n_stocks) VALUES (?, 0.30, 500)",
            (prev_td,),
        )
        v2_db.execute(
            "INSERT INTO daily_new_high (trade_date, new_high_ratio, n_stocks) VALUES (?, 0.20, 500)",
            (td,),
        )
        # 指数: 20 天前 100 → 当前 105 (+5% > 3%)
        self._seed_index(v2_db, td, ("2026-07-17", 100.0), 105.0)
        v2_db.commit()

        out = _apply_new_high_divergence(v2_db, td, new_high_score=50.0)
        assert out == pytest.approx(35.0)  # 50 - 15

    def test_no_penalty_when_no_table_data(self, v2_db):
        """无预计算表数据 → 安全返回原分 (不崩溃, 不误扣)"""
        td = "2026-08-06"
        assert _apply_new_high_divergence(v2_db, td, new_high_score=50.0) == 50.0

    def test_no_penalty_when_index_falling(self, v2_db):
        """指数跌 → 非顶背离 → 不扣分"""
        td = "2026-08-06"
        v2_db.execute(
            "INSERT INTO daily_new_high (trade_date, new_high_ratio, n_stocks) VALUES (?, 0.30, 500)",
            ("2026-07-17",),
        )
        v2_db.execute(
            "INSERT INTO daily_new_high (trade_date, new_high_ratio, n_stocks) VALUES (?, 0.20, 500)",
            (td,),
        )
        self._seed_index(v2_db, td, ("2026-07-17", 100.0), 98.0)  # 指数 -2%
        v2_db.commit()
        out = _apply_new_high_divergence(v2_db, td, new_high_score=50.0)
        assert out == pytest.approx(50.0)


# ── F3: 换手率 10 年窗口 (daily_turnover 预计算表) ───────────────────────────

class TestTurnover10yWindow:
    def _seed_history(self, v2_db, rates, td):
        """种 daily_turnover 历史序列 (跨年日期, 验证 10 年窗口)"""
        for i, d in enumerate(_dates("2016-09-01", len(rates), step_days=30)):
            v2_db.execute(
                "INSERT INTO daily_turnover (trade_date, turnover_rate) VALUES (?, ?)",
                (d, rates[i]),
            )
        v2_db.execute(
            "INSERT INTO daily_turnover (trade_date, turnover_rate) VALUES (?, ?)",
            (td, rates[-1]),
        )
        v2_db.commit()

    def _seed_today(self, v2_db, td, amount, circ_mv):
        """当日成交额/流通市值 (cur_rate 实时计算, 修复前后一致)"""
        v2_db.execute(
            "INSERT INTO stock_daily (trade_date, stock_code, amount, circ_mv) VALUES (?, '000001.SZ', ?, ?)",
            (td, amount, circ_mv),
        )
        v2_db.commit()

    def test_high_turnover_scores_high_on_10y_window(self, v2_db):
        """10 年窗口: 当日换手高于全部历史 → 高分 (旧 6 月窗口在牛市中会漂移)"""
        td = "2026-08-06"
        rates = [round(0.1 + i * 0.008, 4) for i in range(100)]  # 0.1 → 0.892 递增, 跨 ~8 年
        self._seed_history(v2_db, rates, td)
        self._seed_today(v2_db, td, amount=3_000_000.0, circ_mv=3_000_000.0)  # cur=10.0 > 全部历史
        res = calc_turnover_v2(v2_db, td)
        assert res is not None
        score, cur_rate = res
        assert score >= 90.0
        assert cur_rate == pytest.approx(10.0)

    def test_low_turnover_scores_low_on_10y_window(self, v2_db):
        """当日换手低于全部历史 → 低分"""
        td = "2026-08-06"
        rates = [round(0.9 - i * 0.008, 4) for i in range(100)]  # 0.9 → 0.108 递减
        self._seed_history(v2_db, rates, td)
        self._seed_today(v2_db, td, amount=100.0, circ_mv=3_000_000.0)  # cur≈0.0003 → 最低
        res = calc_turnover_v2(v2_db, td)
        assert res is not None
        score, cur_rate = res
        assert score <= 10.0

    def test_insufficient_history_returns_none(self, v2_db):
        """历史不足 60 条 → 返回 None (F3 阈值 60, 原为 20)"""
        td = "2026-08-06"
        for i, d in enumerate(_dates("2026-05-01", 30, step_days=1)):
            v2_db.execute(
                "INSERT INTO daily_turnover (trade_date, turnover_rate) VALUES (?, 0.5)",
                (d,),
            )
        v2_db.execute(
            "INSERT INTO daily_turnover (trade_date, turnover_rate) VALUES (?, 0.6)",
            (td,),
        )
        self._seed_today(v2_db, td, amount=1_000.0, circ_mv=1_000.0)
        v2_db.commit()
        assert calc_turnover_v2(v2_db, td) is None

    def test_daily_value_matches_live_formula(self, v2_db):
        """cur_rate = Σamount/Σcirc_mv×10, 与修复前实时口径完全一致 (当日值不跳变)"""
        td = "2026-08-06"
        rates = [0.5 + i * 0.001 for i in range(100)]
        self._seed_history(v2_db, rates, td)
        # 三只股票: Σamount=6.0e6, Σcirc_mv=1.2e8 → cur = 6e6/1.2e8*10 = 0.5
        for i, code in enumerate(["000001.SZ", "000002.SZ", "000003.SZ"]):
            v2_db.execute(
                "INSERT INTO stock_daily (trade_date, stock_code, amount, circ_mv) VALUES (?, ?, ?, ?)",
                (td, code, 2_000_000.0, 40_000_000.0),
            )
        v2_db.commit()
        res = calc_turnover_v2(v2_db, td)
        assert res is not None
        score, cur_rate = res
        assert cur_rate == pytest.approx(0.5)
        # 与直接公式比对
        assert cur_rate == pytest.approx(6_000_000.0 / 120_000_000.0 * 10)


# ── calc_pe 方向性: 高PE→高分, 低PE→低分 (百分位正确性) ────────────────────────

class TestPeDirection:
    """calc_pe 历史百分位方向性 — 高PE(贵)高分, 低PE(便宜)低分"""

    def _seed(self, v2_db, cur_pe):
        td = "2026-08-06"
        for i, d in enumerate(_dates("2016-09-01", 120)):
            v2_db.execute(
                "INSERT INTO index_daily_pe (trade_date, pe_med, n_stocks) VALUES (?, ?, 722)",
                (d, round(5.0 + i * 0.03, 4)),  # 5.0 → 8.57 递增
            )
        v2_db.execute("INSERT INTO index_daily_pe (trade_date, pe_med, n_stocks) VALUES (?, ?, 722)",
                      (td, cur_pe))
        v2_db.commit()

    def test_high_pe_scores_high(self, v2_db):
        """当前 PE 高于全部 10 年历史 → 高分 (>95)"""
        self._seed(v2_db, 10.0)
        res = calc_pe(v2_db, "2026-08-06")
        assert res is not None
        score, cur_pe = res
        assert cur_pe == pytest.approx(10.0)
        assert score >= 95.0

    def test_low_pe_scores_low(self, v2_db):
        """当前 PE 低于全部 10 年历史 → 低分 (<5)"""
        self._seed(v2_db, 4.0)
        res = calc_pe(v2_db, "2026-08-06")
        assert res is not None
        score, cur_pe = res
        assert cur_pe == pytest.approx(4.0)
        assert score <= 5.0


# ── calc_erp_v2 反向评分: 高ERP=便宜=低分 ─────────────────────────────────────

class TestErpReverseScoring:
    """ERP 股权风险溢价反向评分 — 高ERP(便宜)低分, 低ERP(贵)高分"""

    def _seed(self, v2_db, cur_erp):
        td = "2026-08-06"
        for i, d in enumerate(_dates("2016-09-01", 120)):
            v2_db.execute(
                "INSERT INTO daily_erp (trade_date, erp) VALUES (?, ?)",
                (d, round(1.0 + i * 0.03, 4)),  # 1.0 → 4.57 递增
            )
        v2_db.execute("INSERT INTO daily_erp (trade_date, erp) VALUES (?, ?)",
                      (td, cur_erp))
        v2_db.commit()

    def test_high_erp_scores_low(self, v2_db):
        """当前 ERP 历史最高 (股便宜) → 低热度分 (<5), 验证反向"""
        self._seed(v2_db, 5.0)
        res = calc_erp_v2(v2_db, "2026-08-06")
        assert res is not None
        score, cur_erp = res
        assert cur_erp == pytest.approx(5.0)
        assert score <= 5.0

    def test_low_erp_scores_high(self, v2_db):
        """当前 ERP 历史最低 (股昂贵) → 高热度分 (>95)"""
        self._seed(v2_db, 0.5)
        res = calc_erp_v2(v2_db, "2026-08-06")
        assert res is not None
        score, cur_erp = res
        assert cur_erp == pytest.approx(0.5)
        assert score >= 95.0


# ── calc_buffett: GDP 年份回退 + 方向性 ──────────────────────────────────────

class TestBuffettCalc:
    """巴菲特指标 — GDP 年度缺失回退 + 高市值→高分"""

    def _seed_gdp(self, v2_db, years):
        for y in years:
            for q in range(1, 5):
                v2_db.execute(
                    "INSERT INTO gdp_quarterly (quarter, gdp) VALUES (?, 1e6)",
                    (f"{y}Q{q}",),
                )
        v2_db.commit()

    def _seed_mv(self, v2_db, td):
        for i, d in enumerate(_dates("2016-09-01", 70, step_days=30)):
            v2_db.execute(
                "INSERT INTO stock_market_cap (trade_date, total_mv) VALUES (?, ?)",
                (d, 1e8 + i * 1e7),  # 递增
            )
        v2_db.execute("INSERT INTO stock_market_cap (trade_date, total_mv) VALUES (?, 1e9)",
                      (td,))  # 当前 = 历史最高
        v2_db.commit()

    def test_gdp_year_fallback(self, v2_db):
        """td=2026 但 GDP 只到 2024 → 回退用 2024 年度 GDP, 不返回 None"""
        td = "2026-08-06"
        self._seed_gdp(v2_db, range(2015, 2025))  # 2015-2024, 无 2025
        self._seed_mv(v2_db, td)
        res = calc_buffett(v2_db, td)
        assert res is not None
        score, ratio = res
        assert 0 <= score <= 100
        assert score >= 90.0  # 当前市值最高 → 高分

    def test_no_gdp_returns_none(self, v2_db):
        """无 GDP 数据 → 返回 None (不崩溃)"""
        td = "2026-08-06"
        self._seed_mv(v2_db, td)
        assert calc_buffett(v2_db, td) is None

    def test_high_mv_scores_high(self, v2_db):
        """GDP 齐全时, 当前市值最高 → 高分 (>90)"""
        td = "2026-08-06"
        self._seed_gdp(v2_db, range(2015, 2026))
        self._seed_mv(v2_db, td)
        res = calc_buffett(v2_db, td)
        assert res is not None
        score, _ = res
        assert score >= 90.0

    def test_missing_gdp_warns(self, v2_db, caplog):
        """GDP 表为空 → 返回 None 且产生 warning 日志 (F9 回归)"""
        import logging
        td = "2026-08-06"
        self._seed_mv(v2_db, td)
        with caplog.at_level(logging.WARNING, logger="src.indicators.heat_index_v2"):
            assert calc_buffett(v2_db, td) is None
        assert any("gdp" in r.message.lower() for r in caplog.records)

    def test_stale_gdp_year_logs_info(self, v2_db, caplog):
        """GDP 延迟 1 年 (无 2025) → 使用 2024 年度 GDP 并输出 info 日志 (F9 回归)"""
        import logging
        td = "2026-08-06"
        self._seed_gdp(v2_db, range(2015, 2025))
        self._seed_mv(v2_db, td)
        with caplog.at_level(logging.INFO, logger="src.indicators.heat_index_v2"):
            res = calc_buffett(v2_db, td)
        assert res is not None
        assert any("using GDP from year 2024" in r.message for r in caplog.records)


# ── compute_index_v2 端到端: 加权合成 + 维度聚合 ──────────────────────────────

class TestComputeIndexV2EndToEnd:
    """compute_index_v2 — 全 9 指标种子数据 → 综合分=加权和, 维度分=维度内均值"""

    TD = "2026-08-06"

    def _full_seed(self, db_path):
        """种满 9 个指标所需的全部预计算表"""
        conn = sqlite3.connect(db_path)
        td = self.TD
        # 1. PE: 125 条历史 (n=722) + 当前
        for i, d in enumerate(_dates("2016-09-01", 125)):
            conn.execute(
                "INSERT INTO index_daily_pe (trade_date, pe_med, n_stocks) VALUES (?, ?, 722)",
                (d, round(5.0 + i * 0.03, 4)),
            )
        conn.execute("INSERT INTO index_daily_pe (trade_date, pe_med, n_stocks) VALUES (?, 10.0, 722)",
                     (td,))
        # 2. ERP: 125 条历史 + 当前 (高ERP → 低分, 反向)
        for i, d in enumerate(_dates("2016-09-01", 125)):
            conn.execute("INSERT INTO daily_erp (trade_date, erp) VALUES (?, ?)",
                         (d, round(1.0 + i * 0.03, 4)),
                         )
        conn.execute("INSERT INTO daily_erp (trade_date, erp) VALUES (?, 5.0)", (td,))
        # 3. GDP (2015-2025) + 总市值 (70 个月递增)
        for y in range(2015, 2026):
            for q in range(1, 5):
                conn.execute("INSERT INTO gdp_quarterly (quarter, gdp) VALUES (?, 1e6)",
                             (f"{y}Q{q}",))
        for i, d in enumerate(_dates("2016-09-01", 70, step_days=30)):
            conn.execute("INSERT INTO stock_market_cap (trade_date, total_mv) VALUES (?, ?)",
                         (d, 1e8 + i * 1e7))
        conn.execute("INSERT INTO stock_market_cap (trade_date, total_mv) VALUES (?, 1e9)", (td,))
        # 4. 两融: 65 条 5 年窗口 + 当前 (高杠杆 → 高分)
        for i, d in enumerate(_dates("2021-08-10", 65, step_days=30)):
            conn.execute("INSERT INTO daily_circ_mv (trade_date, total_circ_mv) VALUES (?, 1e8)", (d,))
            conn.execute("INSERT INTO margin_history (trade_date, rzye, rqye) VALUES (?, ?, 0)",
                         (d, 1e11 + i * 1e9))
        conn.execute("INSERT INTO daily_circ_mv (trade_date, total_circ_mv) VALUES (?, 1e8)", (td,))
        conn.execute("INSERT INTO margin_history (trade_date, rzye, rqye) VALUES (?, 2e11, 0)", (td,))
        # 5. M2 (70 个月) + 成交额 (70 个月) + 当日
        for m in _months("2016-01", 70):
            conn.execute("INSERT INTO m2_monthly (month, m2_billion) VALUES (?, 2e5)", (m,))
        for i, d in enumerate(_dates("2016-09-01", 70, step_days=30)):
            conn.execute("INSERT INTO stock_daily (trade_date, stock_code, amount, circ_mv) "
                         "VALUES (?, '000001.SZ', ?, 1e8)",
                         (d, 1e5 + i * 1e3))
        conn.execute("INSERT INTO stock_daily (trade_date, stock_code, amount, circ_mv) "
                     "VALUES (?, '000001.SZ', 1e6, 1e8)", (td,))
        # 6. 换手率历史 (70 条, 10 年窗口)
        for i, d in enumerate(_dates("2016-09-01", 70, step_days=30)):
            conn.execute("INSERT INTO daily_turnover (trade_date, turnover_rate) VALUES (?, ?)",
                         (d, round(0.2 + i * 0.004, 4)))
        # 7. 新高占比 (70 条) + 当前 (最高 → 高分)
        for i, d in enumerate(_dates("2016-09-01", 70, step_days=30)):
            conn.execute("INSERT INTO daily_new_high (trade_date, new_high_ratio, n_stocks) "
                         "VALUES (?, ?, 500)",
                         (d, round(0.05 + i * 0.009, 4)))
        conn.execute("INSERT INTO daily_new_high (trade_date, new_high_ratio, n_stocks) "
                     "VALUES (?, 0.7, 500)", (td,))
        # 8. MA 排列 (70 条) + 当前 (最高 → 高分)
        for i, d in enumerate(_dates("2016-09-01", 70, step_days=30)):
            conn.execute("INSERT INTO daily_ma_alignment (trade_date, ma_alignment_ratio) VALUES (?, ?)",
                         (d, round(0.1 + i * 0.008, 4)))
        conn.execute("INSERT INTO daily_ma_alignment (trade_date, ma_alignment_ratio) VALUES (?, 0.9)",
                     (td,))
        conn.commit()
        conn.close()

    def test_all_indicators_weighted_sum(self, tmp_path):
        """全部 9 指标有分 → composite=Σ(w_i·score_i), 维度分按指标权重加权 (F10)"""
        db_path = str(tmp_path / "e2e.db")
        init_database(db_path)
        self._full_seed(db_path)

        res = compute_index_v2(trade_date=self.TD, db_path=db_path)
        assert res["trade_date"] == self.TD

        ind = res["indicators"]
        # result 键→权重键映射 (两融在 result 中名为 margin_ratio_v2, 权重表用 margin_ratio)
        result_to_weight = {
            "pe": "pe", "erp": "erp", "buffett": "buffett",
            "margin_ratio_v2": "margin_ratio", "deposit_ratio": "deposit_ratio",
            "turnover_m2": "turnover_m2", "turnover": "turnover",
            "new_high": "new_high", "ma_alignment": "ma_alignment",
        }
        # 全部 9 指标均有分数
        for rk in result_to_weight:
            assert ind[rk] is not None, f"{rk} 无分数"
        # 综合分 = 指标加权和 (权重总和=1.0)
        expected = sum(ind[rk] * INDICATOR_WEIGHTS[result_to_weight[rk]] for rk in result_to_weight)
        assert res["composite_score"] == pytest.approx(round(expected, 1), abs=0.1)

        # 维度分 = 维度内指标按权重加权 (F10: 与综合分口径一致)
        def _dim_weighted(keys):
            w = sum(INDICATOR_WEIGHTS[result_to_weight[k]] for k in keys)
            return sum(ind[k] * INDICATOR_WEIGHTS[result_to_weight[k]] for k in keys) / w

        val = _dim_weighted(["pe", "erp", "buffett"])
        assert res["dimensions"]["valuation"]["score"] == pytest.approx(round(val, 1), abs=0.1)
        fund = _dim_weighted(["margin_ratio_v2", "deposit_ratio"])
        assert res["dimensions"]["fund"]["score"] == pytest.approx(round(fund, 1), abs=0.1)
        sent = _dim_weighted(["turnover_m2", "turnover"])
        assert res["dimensions"]["sentiment"]["score"] == pytest.approx(round(sent, 1), abs=0.1)
        struct = _dim_weighted(["new_high", "ma_alignment"])
        assert res["dimensions"]["structure"]["score"] == pytest.approx(round(struct, 1), abs=0.1)

        # F10 关键不变量: Σ(维度分 × 维度权重占比) ≈ 综合分
        # 维度权重 = 维度内指标权重之和
        dim_weights = {
            "valuation": sum(INDICATOR_WEIGHTS[result_to_weight[k]] for k in ("pe", "erp", "buffett")),
            "fund": sum(INDICATOR_WEIGHTS[result_to_weight[k]] for k in ("margin_ratio_v2", "deposit_ratio")),
            "sentiment": sum(INDICATOR_WEIGHTS[result_to_weight[k]] for k in ("turnover_m2", "turnover")),
            "structure": sum(INDICATOR_WEIGHTS[result_to_weight[k]] for k in ("new_high", "ma_alignment")),
        }
        recon = sum(res["dimensions"][d]["score"] * w for d, w in dim_weights.items())
        assert res["composite_score"] == pytest.approx(recon, abs=0.2)

    def test_partial_data_renormalizes_weights(self, tmp_path):
        """仅 PE 有数据 → 综合分 = PE 分 (权重重归一化, 不因缺失指标而失真)"""
        db_path = str(tmp_path / "pe_only.db")
        init_database(db_path)
        conn = sqlite3.connect(db_path)
        for i, d in enumerate(_dates("2016-09-01", 125)):
            conn.execute(
                "INSERT INTO index_daily_pe (trade_date, pe_med, n_stocks) VALUES (?, ?, 722)",
                (d, round(5.0 + i * 0.03, 4)),
            )
        conn.execute("INSERT INTO index_daily_pe (trade_date, pe_med, n_stocks) VALUES (?, 10.0, 722)",
                     (self.TD,))
        conn.commit()
        conn.close()

        res = compute_index_v2(trade_date=self.TD, db_path=db_path)
        assert res["indicators"]["pe"] is not None
        # 其余指标无数据 → None
        for k in ("erp", "buffett", "margin_ratio_v2", "deposit_ratio",
                  "turnover_m2", "turnover", "new_high", "ma_alignment"):
            assert res["indicators"][k] is None, f"{k} 应无数据"
        # 仅 PE 有效 → 综合分=PE 分 (total_weight 归一化)
        assert res["composite_score"] == pytest.approx(round(res["indicators"]["pe"], 1), abs=0.1)
