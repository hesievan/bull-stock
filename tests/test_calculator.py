"""Tests for src/indicators/calculator.py — 核心指标计算引擎"""
import pytest
import numpy as np
import pandas as pd
from unittest.mock import patch, MagicMock
from datetime import date, timedelta

from src.indicators.calculator import (
    HeatIndexCalculator,
    calculate_heat_index,
)
from src.indicators.utils import (
    _pct_rank,
    _pct_rank_inv,
    _safe_mean,
    _score_with_fallback,
    _to_numeric,
    _query_scalar,
    _query_dataframe,
)


# ── 工具函数测试 ──────────────────────────────────────────────────────────────

class TestPctRank:
    def test_basic(self):
        s = pd.Series([1, 2, 3, 4, 5])
        assert _pct_rank(s, 3) == pytest.approx(0.6)

    def test_value_at_max(self):
        s = pd.Series([1, 2, 3, 4, 5])
        assert _pct_rank(s, 5) == pytest.approx(1.0)

    def test_value_below_min(self):
        s = pd.Series([1, 2, 3, 4, 5])
        assert _pct_rank(s, 0) == pytest.approx(0.0)

    def test_empty_series(self):
        result = _pct_rank(pd.Series([]), 1)
        assert np.isnan(result)

    def test_nan_value(self):
        s = pd.Series([1, 2, 3])
        result = _pct_rank(s, float("nan"))
        assert np.isnan(result)

    def test_series_with_nans(self):
        s = pd.Series([1, np.nan, 3, 4, 5])
        assert _pct_rank(s, 3) == pytest.approx(0.5)


class TestPctRankInv:
    def test_basic(self):
        s = pd.Series([1, 2, 3, 4, 5])
        result = _pct_rank_inv(s, 3)
        assert result == pytest.approx(0.4)

    def test_inversion(self):
        s = pd.Series([1, 2, 3, 4, 5])
        assert _pct_rank(s, 3) + _pct_rank_inv(s, 3) == pytest.approx(1.0)


class TestSafeMean:
    def test_basic(self):
        assert _safe_mean([1, 2, 3]) == pytest.approx(2.0)

    def test_with_nones(self):
        assert _safe_mean([1, None, 3]) == pytest.approx(2.0)

    def test_with_nans(self):
        assert _safe_mean([1, float("nan"), 3]) == pytest.approx(2.0)

    def test_all_invalid(self):
        assert _safe_mean([None, float("nan")]) is None

    def test_empty(self):
        assert _safe_mean([]) is None


class TestScoreWithFallback:
    def test_valid(self):
        assert _score_with_fallback(50.0) == 50.0

    def test_clamp_high(self):
        assert _score_with_fallback(150.0) == 100.0

    def test_clamp_low(self):
        assert _score_with_fallback(-10.0) == 0.0

    def test_none(self):
        assert _score_with_fallback(None) is None

    def test_nan(self):
        assert _score_with_fallback(float("nan")) is None


class TestToNumeric:
    def test_basic(self):
        s = pd.Series(["1", "2", "3"])
        result = _to_numeric(s)
        assert result.dtype in [float, np.float64, np.int64]
        assert list(result) == [1.0, 2.0, 3.0]

    def test_with_invalid(self):
        s = pd.Series(["1", "abc", "3"])
        result = _to_numeric(s)
        assert result[0] == 1.0
        assert np.isnan(result[1])
        assert result[2] == 3.0

    def test_with_fillna(self):
        s = pd.Series(["1", "abc", "3"])
        result = _to_numeric(s, fillna=0)
        assert result[1] == 0.0

    def test_empty_series(self):
        result = _to_numeric(pd.Series([]))
        assert len(result) == 0


class TestQueryScalar:
    def test_found(self):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.execute("INSERT INTO t VALUES (42)")
        assert _query_scalar(conn, "SELECT x FROM t") == 42
        conn.close()

    def test_not_found(self):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE t (x INTEGER)")
        assert _query_scalar(conn, "SELECT x FROM t WHERE x=999") is None
        conn.close()


# ── HeatIndexCalculator 测试 ─────────────────────────────────────────────────

class TestHeatIndexCalculator:
    def test_init_default_date(self):
        calc = HeatIndexCalculator()
        assert calc.trade_date == date.today().strftime("%Y-%m-%d")

    def test_init_custom_date(self):
        calc = HeatIndexCalculator(trade_date="2025-01-15")
        assert calc.trade_date == "2025-01-15"

    def test_lookback_start(self):
        calc = HeatIndexCalculator(trade_date="2025-06-14")
        expected = (date(2025, 6, 14) - timedelta(days=3650)).strftime("%Y-%m-%d")
        assert calc.lookback_start == expected

    def test_combine_dimension_all_none(self):
        calc = HeatIndexCalculator()
        assert calc._combine_dimension([None, None], "test") is None

    def test_combine_dimension_valid(self):
        calc = HeatIndexCalculator()
        result = calc._combine_dimension([60.0, 80.0], "test")
        assert result == 70.0

    def test_combine_dimension_with_nan(self):
        calc = HeatIndexCalculator()
        result = calc._combine_dimension([60.0, np.nan, 80.0], "test")
        assert result == 70.0

    def test_combine_dimension_clamp(self):
        calc = HeatIndexCalculator()
        result = calc._combine_dimension([110.0], "test")
        assert result == 100.0

    def test_combine_dimension_single(self):
        calc = HeatIndexCalculator()
        result = calc._combine_dimension([45.0], "test")
        assert result == 45.0

    def test_series_pct_rank(self):
        calc = HeatIndexCalculator()
        s = pd.Series([10, 20, 30, 40, 50])
        assert calc._series_pct_rank(s, 30) == pytest.approx(0.4)

    def test_cache_mechanism(self):
        calc = HeatIndexCalculator()
        calc._cache["test_key"] = pd.DataFrame({"a": [1]})
        assert "test_key" in calc._cache
        assert len(calc._cache["test_key"]) == 1


# ── 指标计算测试 ──────────────────────────────────────────────────────────────

class TestIndicatorCalculations:
    def test_valuation_composite_pe_only(self):
        calc = HeatIndexCalculator()
        with patch.object(calc, '_calc_pe_percentile', return_value=60.0):
            with patch.object(calc, '_calc_pb_percentile', return_value=None):
                result = calc._calc_valuation_composite()
                assert result == 60.0

    def test_valuation_composite_pb_only(self):
        calc = HeatIndexCalculator()
        with patch.object(calc, '_calc_pe_percentile', return_value=None):
            with patch.object(calc, '_calc_pb_percentile', return_value=50.0):
                result = calc._calc_valuation_composite()
                assert result == 50.0

    def test_valuation_composite_both(self):
        calc = HeatIndexCalculator()
        with patch.object(calc, '_calc_pe_percentile', return_value=60.0):
            with patch.object(calc, '_calc_pb_percentile', return_value=40.0):
                result = calc._calc_valuation_composite()
                assert result == pytest.approx(52.0)

    def test_valuation_composite_none(self):
        calc = HeatIndexCalculator()
        with patch.object(calc, '_calc_pe_percentile', return_value=None):
            with patch.object(calc, '_calc_pb_percentile', return_value=None):
                result = calc._calc_valuation_composite()
                assert result is None


# ── calculate_heat_index 入口测试 ────────────────────────────────────────────

class TestCalculateHeatIndex:
    @patch.object(HeatIndexCalculator, "calculate")
    def test_returns_dict(self, mock_calc):
        mock_calc.return_value = {
            "trade_date": "2025-06-14",
            "composite_score": 65.0,
            "dim_valuation": 70.0,
            "dim_macro": 50.0,
            "dim_fund": 60.0,
            "dim_sentiment": 80.0,
            "dim_technical": 40.0,
            "dim_structure": 55.0,
            "indicators": {},
        }
        result = calculate_heat_index(trade_date="2025-06-14")
        assert result["composite_score"] == 65.0
        assert "indicators" in result


# ── 板块热度测试 ──────────────────────────────────────────────────────────────

class TestSectorCalculator:
    def test_sector_name_map(self):
        from src.indicators.sector_calculator import SECTOR_NAME_MAP, _sector_name
        assert _sector_name("C27") == "医药制造"
        assert _sector_name("XXX") == "XXX"
        assert _sector_name("") == "未知"

    def test_sp_rank(self):
        from src.indicators.sector_calculator import _sp_rank
        s = pd.Series([10, 20, 30, 40, 50])
        assert _sp_rank(s, 30) == pytest.approx(0.4)

    def test_sp_combine(self):
        from src.indicators.sector_calculator import _sp_combine
        assert _sp_combine([60.0, 80.0]) == 70.0
        assert _sp_combine([]) is None
        assert _sp_combine([None, 60.0]) == 60.0
