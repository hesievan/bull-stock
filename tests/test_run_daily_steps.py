"""Tests for scripts/run_daily.py — step 三态语义 (P1-7)、数据质量合成 (P0-2)、step 分级 (P0-1)。

不跑真实流水线（那由 test_run_daily_integration.py 覆盖），只锁死三件容易静默失效的事：
  1. step 返回 None 不能被记成 OK
  2. data_quality 摘要必须能被 build_feishu_notification 消费
  3. 致命/降级 step 分级必须覆盖实际出现的 step 名
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# engine_mode=single9 的 9 个计分键
_SCORING_KEYS = (
    "pe",
    "buffett",
    "yield_spread",
    "m1_m2_spread",
    "margin_buy_ratio",
    "turnover",
    "futures_discount",
    "new_high",
    "ma_alignment",
)
# single6 剔除的三键
_SINGLE6_DROP = ("turnover", "ma_alignment", "new_high")


def _rd():
    """惰性 import：run_daily 顶层会 setup_logging 并写 run_daily.log，避免在收集期执行。"""
    import scripts.run_daily as rd

    return rd


def _full_indicators(**overrides):
    inds = {k: 50.0 for k in _SCORING_KEYS}
    inds.update(overrides)
    return inds


class TestRunStepSemantics:
    def test_true_is_ok(self):
        rd = _rd()
        st = {}
        assert rd._run_step(st, "S_ok", lambda: True) is True
        assert st["S_ok"]["status"] == "OK"

    def test_false_is_skipped(self):
        rd = _rd()
        st = {}
        assert rd._run_step(st, "S_skip", lambda: False) is False
        assert st["S_skip"]["status"] == "SKIPPED"

    def test_none_is_failed(self):
        """P1-7 回归: None 曾被 else 分支记成 OK —— "什么都没产出" 看起来却是成功的。"""
        rd = _rd()
        st = {}
        rd._run_step(st, "S_none", lambda: None)
        assert st["S_none"]["status"] == "FAILED"
        assert "None" in st["S_none"]["detail"]

    def test_zero_is_not_failure(self):
        """0 是合法计数（写 0 行），只有显式 False/None 才降级。"""
        rd = _rd()
        st = {}
        rd._run_step(st, "S_zero", lambda: 0)
        assert st["S_zero"]["status"] == "OK"

    def test_dict_result_is_ok(self):
        rd = _rd()
        st = {}
        rd._run_step(st, "S_dict", lambda: {"a": 1})
        assert st["S_dict"]["status"] == "OK"

    def test_exception_is_failed(self):
        rd = _rd()

        def boom():
            raise RuntimeError("kaboom")

        st = {}
        assert rd._run_step(st, "S_boom", boom) is None
        assert st["S_boom"]["status"] == "FAILED"
        assert "kaboom" in st["S_boom"]["detail"]

    def test_step_name_reused_overwrites(self):
        rd = _rd()
        st = {}
        rd._run_step(st, "S_x", lambda: None)
        rd._run_step(st, "S_x", lambda: True)
        assert st["S_x"]["status"] == "OK"


class TestDataQuality:
    def _q(self, indicators, step_status=None, mode="single9"):
        rd = _rd()
        return rd._build_data_quality({"engine_mode": mode, "indicators": indicators}, step_status or {})

    def test_all_present_is_good(self):
        q = self._q(_full_indicators())
        assert q["overall_quality"] == "good"
        assert q["indicator_available"] == 9
        assert q["indicator_total"] == 9
        assert q["missing_indicators"] == []
        assert q["dimensions"]["valuation"]["status"] == "ok"

    def test_missing_indicator_degrades(self):
        q = self._q(_full_indicators(turnover=None))
        assert q["overall_quality"] == "degraded"
        assert q["missing_indicators"] == ["turnover"]
        assert q["dimensions"]["sentiment"]["status"] == "degraded"
        assert q["dimensions"]["sentiment"]["available"] == 1
        assert q["dimensions"]["sentiment"]["total"] == 2
        assert q["dimensions"]["sentiment"]["missing"] == ["turnover"]

    def test_dimension_with_zero_available_is_bad(self):
        q = self._q(_full_indicators(yield_spread=None, m1_m2_spread=None, margin_buy_ratio=None))
        assert q["dimensions"]["fund"]["status"] == "bad"
        assert q["dimensions"]["fund"]["available"] == 0

    def test_critical_step_failure_is_poor(self):
        q = self._q(
            _full_indicators(),
            {"S5_calc": {"status": "FAILED", "detail": "boom"}},
        )
        assert q["overall_quality"] == "poor"
        assert q["critical_failed_steps"] == ["S5_calc"]
        assert q["degraded_failed_steps"] == []

    def test_degraded_step_failure_is_degraded(self):
        q = self._q(
            _full_indicators(),
            {"S7_sectors": {"status": "FAILED", "detail": "no data"}},
        )
        assert q["overall_quality"] == "degraded"
        assert q["critical_failed_steps"] == []
        assert q["degraded_failed_steps"] == ["S7_sectors"]

    def test_non_step_entries_are_ignored(self):
        """step_status 里混有 precompute_staleness(list) —— 旧写法会在 .get() 上炸。"""
        q = self._q(
            _full_indicators(),
            {"precompute_staleness": [{"table": "x"}], "S1_index": {"status": "OK", "detail": ""}},
        )
        assert q["overall_quality"] == "good"
        assert q["failed_steps"] == []
        assert q["skipped_steps"] == []

    def test_single6_counts_six_keys(self):
        inds = _full_indicators()
        for k in _SINGLE6_DROP:
            inds.pop(k, None)
        q = self._q(inds, mode="single6")
        assert q["indicator_total"] == 6
        assert q["overall_quality"] == "good"

    def test_stats_failure_degrades_but_does_not_raise(self):
        """indicators 结构异常 → 统计失败, 但不能抛异常, 且必须体现为降级。"""
        rd = _rd()
        q = rd._build_data_quality({"engine_mode": "single9", "indicators": "not-a-dict"}, {})
        assert q["stats_ok"] is False
        assert q["overall_quality"] == "degraded"

    def test_schema_matches_notification_consumer(self):
        """消费者读的是 dimensions[].status/label/available/total 与 missing_indicators。"""
        q = self._q(_full_indicators(pe=None))
        assert set(q["dimensions"]) == {"valuation", "fund", "sentiment", "structure"}
        for info in q["dimensions"].values():
            assert set(info) >= {"status", "label", "available", "total", "missing"}
            assert info["status"] in ("ok", "degraded", "bad")


class TestStepClassification:
    def test_sets_are_disjoint(self):
        rd = _rd()
        assert not (rd.CRITICAL_STEPS & rd.DEGRADED_STEPS)

    def test_actual_step_names_are_classified(self):
        """退出码判定依赖分级 —— 新增 step 却忘了登记会静默漏判。"""
        rd = _rd()
        known = rd.CRITICAL_STEPS | rd.DEGRADED_STEPS
        actual = [
            "init_db",
            "S1_index",
            "S2_market",
            "S25_index_pe",
            "S26_circ_mv",
            "S26b_total_mv",
            "S27_updown",
            "S28_limit",
            "S29_below_net",
            "S30_ma_alignment",
            "S30b_new_high",
            "S30c_turnover",
            "S31_qvix",
            "S31b_seal_rate",
            "S31c_index_pe",
            "S24_precompute_check",
            "S24c_m2",
            "S24d_m1",
            "S24f_south",
            "S24g_futures",
            "S24h_accounts",
            "S24i_etf_flow",
            "S3_tushare",
            "S3_margin",
            "S3_bond_yield",
            "S3_shenwan",
            "S3_industry",
            "S5_calc",
            "S55_index_heat",
            "S6_save",
            "S7_sectors",
            "S75_focus",
            "S8_final_save",
            "S9_notify",
            "S10_analyze",
        ]
        unclassified = sorted(s for s in actual if s not in known)
        assert unclassified == [], f"以下 step 未分级: {unclassified}"

    def test_save_steps_are_critical(self):
        rd = _rd()
        for s in ("init_db", "S1_index", "S2_market", "S5_calc", "S6_save", "S8_final_save"):
            assert s in rd.CRITICAL_STEPS


class TestExitCode:
    """P0-1: 致命 step 失败必须让进程非零退出, 否则 CI 会把坏数据当成功提交。"""

    def test_all_ok_exits_zero(self):
        rd = _rd()
        st = {"S1_index": {"status": "OK"}, "S7_sectors": {"status": "OK"}}
        assert rd._exit_code_for(st) == 0

    def test_degraded_failure_exits_zero(self):
        rd = _rd()
        st = {"S5_calc": {"status": "OK"}, "S7_sectors": {"status": "FAILED"}}
        assert rd._exit_code_for(st) == 0

    def test_skipped_exits_zero(self):
        rd = _rd()
        st = {"S31_qvix": {"status": "SKIPPED"}, "S5_calc": {"status": "OK"}}
        assert rd._exit_code_for(st) == 0

    def test_critical_failure_exits_one(self):
        rd = _rd()
        for critical in sorted(rd.CRITICAL_STEPS):
            assert rd._exit_code_for({"S1_index": {"status": "OK"}, critical: {"status": "FAILED"}}) == 1

    def test_non_step_entries_ignored(self):
        rd = _rd()
        st = {"precompute_staleness": [{"table": "x"}], "S1_index": {"status": "OK"}}
        assert rd._exit_code_for(st) == 0

    def test_failed_steps_helper(self):
        rd = _rd()
        st = {
            "S5_calc": {"status": "FAILED"},
            "S7_sectors": {"status": "FAILED"},
            "S1_index": {"status": "OK"},
            "precompute_staleness": [],
        }
        assert rd._failed_steps(st) == {"S5_calc", "S7_sectors"}
