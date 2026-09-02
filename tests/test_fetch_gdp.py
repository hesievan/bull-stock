#!/usr/bin/env python3
"""fetch_gdp.py 的 GDP 口径归一化测试。

核心回归: tushare cn_gdp 的 gdp 字段是**年初至今累计值**(YTD),
直接入库会让巴菲特指标的年度 GDP 分母翻倍级失真。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from scripts.fetch_gdp import (  # noqa: E402
    detect_cumulative_series,
    normalize_to_quarterly,
    parse_quarter,
)


# 2024/2025 真实累计序列(tushare 原样返回)
RAW_CUMULATIVE = [
    {"quarter": "2025Q1", "gdp": 318466.4, "gdp_yoy": 5.4},
    {"quarter": "2025Q2", "gdp": 659861.6, "gdp_yoy": 5.3},
    {"quarter": "2025Q3", "gdp": 1013967.9, "gdp_yoy": 5.2},
    {"quarter": "2025Q4", "gdp": 1401879.2, "gdp_yoy": 5.0},
    {"quarter": "2026Q1", "gdp": 334192.9, "gdp_yoy": 5.0},
    {"quarter": "2026Q2", "gdp": 695704.0, "gdp_yoy": 4.7},
]

# 上表对应的当季值(与库内 2024/2025 历史值一致)
EXPECTED_QUARTERLY = {
    "2025Q1": 318466.4,
    "2025Q2": 341395.2,
    "2025Q3": 354106.3,
    "2025Q4": 387911.3,
    "2026Q1": 334192.9,
    "2026Q2": 361511.1,
}

# 库内已知的上一年度当季值
KNOWN_2024 = {
    "2024Q1": 304761.8,
    "2024Q2": 328837.6,
    "2024Q3": 340954.4,
    "2024Q4": 373512.4,
}


class TestParseQuarter:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("2026Q2", (2026, 2)),
            ("202602", (2026, 2)),
            ("2026Q4", (2026, 4)),
            (" 2025Q1 ", (2025, 1)),
            ("garbage", (0, 0)),
            ("", (0, 0)),
        ],
    )
    def test_parse(self, raw, expected):
        assert parse_quarter(raw) == expected


class TestDetectCumulativeSeries:
    def test_cumulative_series(self):
        raw = {r["quarter"]: r["gdp"] for r in RAW_CUMULATIVE}
        # 2026Q1(334192.9) / 2025Q4(1401879.2) = 0.238 -> 累计值
        assert detect_cumulative_series(raw) is True

    def test_quarterly_series(self):
        raw = {q: v for q, v in EXPECTED_QUARTERLY.items()}
        # 2026Q1(334192.9) / 2025Q4(387911.3) = 0.862 -> 当季值
        assert detect_cumulative_series(raw) is False

    def test_undetermined_without_year_boundary(self):
        """批次内没有 (Y,Q1)/(Y-1,Q4) 配对时无法判定。"""
        assert detect_cumulative_series({"2026Q1": 334192.9, "2026Q2": 695704.0}) is None
        assert detect_cumulative_series({}) is None

    def test_median_robust_to_single_outlier(self):
        """极端离群年份不应翻转整批判定(取中位数)。"""
        raw = {q: v for q, v in EXPECTED_QUARTERLY.items()}
        raw["2020Q1"] = 1.0  # 脏数据: 2020Q1/2019Q4 比值趋近 0
        assert detect_cumulative_series(raw) is False


class TestNormalizeToQuarterly:
    def test_cumulative_series_diffed_back(self):
        """tushare 累计序列 -> 全部差分回当季值, 且保留原始累计到 gdp_accumulate。"""
        rows = [dict(r) for r in RAW_CUMULATIVE]
        out = normalize_to_quarterly(rows, known=dict(KNOWN_2024))

        got = {r["quarter"]: r["gdp"] for r in out}
        assert len(out) == 6
        for q, v in EXPECTED_QUARTERLY.items():
            assert got[q] == pytest.approx(v, abs=0.15), f"{q} 差分结果 {got[q]} != {v}"

    def test_accumulate_preserved(self):
        out = normalize_to_quarterly([dict(r) for r in RAW_CUMULATIVE], known=dict(KNOWN_2024))
        acc = {r["quarter"]: r["gdp_accumulate"] for r in out}
        assert acc["2026Q2"] == pytest.approx(695704.0)
        assert acc["2025Q4"] == pytest.approx(1401879.2)
        # Q1 的累计值等于当季值, 不额外打标
        assert acc["2026Q1"] is None

    def test_quarterly_series_left_untouched(self):
        """若上游改回当季值口径, 不应误判。"""
        rows = [
            {"quarter": "2026Q1", "gdp": 334192.9},
            {"quarter": "2026Q2", "gdp": 341395.2},
            {"quarter": "2026Q3", "gdp": 354106.3},
            {"quarter": "2026Q4", "gdp": 387911.3},
        ]
        out = normalize_to_quarterly(rows, known=dict(KNOWN_2024))
        got = {r["quarter"]: r["gdp"] for r in out}
        assert got == pytest.approx({"2026Q1": 334192.9, "2026Q2": 341395.2, "2026Q3": 354106.3, "2026Q4": 387911.3})
        assert all(r["gdp_accumulate"] is None for r in out)

    def test_fallback_when_prev_year_unknown(self):
        """known 缺上年同季度时, 用同年上一季度源值兜底判定(Q2/Q3 可判, Q4 判不出)。"""
        rows = [
            {"quarter": "2026Q1", "gdp": 334192.9},
            {"quarter": "2026Q2", "gdp": 695704.0},
        ]
        out = normalize_to_quarterly(rows, known={})
        got = {r["quarter"]: r["gdp"] for r in out}
        assert got["2026Q2"] == pytest.approx(361511.1, abs=0.15)

    def test_undiffable_row_dropped(self):
        """累计值但同年缺前序季度 -> 丢弃, 绝不写入错误量级。

        场景: 只拉了 2026Q2 一行(没有 2026Q1 源值可差分), 库里已有上年同季度当季值。
        """
        rows = [{"quarter": "2026Q2", "gdp": 695704.0}]
        out = normalize_to_quarterly(rows, known={"2025Q2": 341395.2})
        assert out == []

    def test_q4_diffed_even_without_prior_year_baseline(self):
        """回归: Q4 曾因"最早一年缺上年基线 -> 漏判 -> 沿年份级联"而全部写错。

        累计序列里 Q4/上年Q4≈3.8 本应能判出, 但最早一年的 Q4 走同年兜底时
        累计Q4/累计Q3≈1.38 < 1.5 阈值 -> 漏判 -> 该值进 known 后污染后续所有 Q4。
        序列级判定不依赖 known 状态, 可覆盖此场景。
        """
        rows = [
            {"quarter": "2025Q1", "gdp": 318466.4},
            {"quarter": "2025Q2", "gdp": 659861.6},
            {"quarter": "2025Q3", "gdp": 1013967.9},
            {"quarter": "2025Q4", "gdp": 1401879.2},
            {"quarter": "2026Q1", "gdp": 334192.9},
            {"quarter": "2026Q2", "gdp": 695704.0},
            {"quarter": "2026Q3", "gdp": 1050000.0},
            {"quarter": "2026Q4", "gdp": 1460000.0},
        ]
        out = normalize_to_quarterly(rows, known={})
        got = {r["quarter"]: r["gdp"] for r in out}
        assert len(out) == 8, "累计值应全部差分回当季, 不得丢弃"
        assert got["2025Q4"] == pytest.approx(387911.3, abs=0.15)
        assert got["2026Q3"] == pytest.approx(354296.0, abs=0.15)
        assert got["2026Q4"] == pytest.approx(410000.0, abs=0.15)

    def test_implausible_diff_dropped(self):
        """源数据错乱导致差分结果不合理(如为负)时丢弃, 不写错量级。

        场景: 2026Q2 源值 100000.0(小于同年 Q1, 明显是脏数据), 差分会得到负值。
        """
        rows = [
            {"quarter": "2025Q1", "gdp": 318466.4},
            {"quarter": "2025Q2", "gdp": 659861.6},
            {"quarter": "2025Q3", "gdp": 1013967.9},
            {"quarter": "2025Q4", "gdp": 1401879.2},
            {"quarter": "2026Q1", "gdp": 334192.9},
            {"quarter": "2026Q2", "gdp": 100000.0},
        ]
        out = normalize_to_quarterly(rows, known={})
        got = {r["quarter"]: r["gdp"] for r in out}
        assert "2026Q2" not in got, "差分结果为负不应入库"
        assert got["2026Q1"] == pytest.approx(334192.9)
        assert got["2025Q4"] == pytest.approx(387911.3, abs=0.15)

    def test_input_not_mutated(self):
        rows = [dict(r) for r in RAW_CUMULATIVE]
        normalize_to_quarterly(rows, known=dict(KNOWN_2024))
        assert rows == RAW_CUMULATIVE

    def test_null_gdp_passthrough(self):
        rows = [{"quarter": "2026Q3", "gdp": None, "gdp_yoy": None}]
        out = normalize_to_quarterly(rows, known=dict(KNOWN_2024))
        assert len(out) == 1 and out[0]["gdp"] is None

    def test_annual_total_matches_known_gdp(self):
        """差分后的 2025 年四季之和应等于官方年度 GDP 1401879.2。"""
        out = normalize_to_quarterly(
            [r for r in RAW_CUMULATIVE if r["quarter"].startswith("2025")],
            known=dict(KNOWN_2024),
        )
        total = sum(r["gdp"] for r in out)
        assert total == pytest.approx(1401879.2, abs=0.5)
