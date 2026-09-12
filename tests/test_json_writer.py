"""Tests for src/output/json_writer.py — 输出模块"""

import json
from datetime import date, timedelta

from src.output.json_writer import (
    analyze_state,
    get_feishu_webhook,
    get_heat_level,
    get_heat_level_cn,
    save_results_v2,
)


def _make_result(trade_date: str, score: float = 50.0) -> dict:
    """构造 save_results_v2 所需的最小 result_v2。"""
    return {
        "trade_date": trade_date,
        "composite_score": score,
        "dimensions": {"valuation": 50.0, "capital": 50.0, "sentiment": 50.0, "structure": 50.0},
        "indicators": {"turnover": 60.0, "pe": 40.0},
        "indicator_raw": {"turnover": 0.0123, "pe": 15.6},
    }


def _history_entries(n: int) -> list:
    base = date(2025, 1, 1)
    return [
        {
            "trade_date": (base + timedelta(days=i)).strftime("%Y-%m-%d"),
            "composite_score": 50.0,
            "level": "yellow",
        }
        for i in range(n)
    ]


class TestGetHeatLevel:
    def test_red(self):
        assert get_heat_level(70) == "red"

    def test_orange(self):
        assert get_heat_level(60) == "orange"

    def test_yellow(self):
        assert get_heat_level(45) == "yellow"

    def test_green(self):
        assert get_heat_level(30) == "green"

    def test_boundary_red(self):
        assert get_heat_level(65) == "red"

    def test_boundary_orange(self):
        assert get_heat_level(55) == "orange"

    def test_boundary_yellow(self):
        assert get_heat_level(40) == "yellow"

    def test_none(self):
        assert get_heat_level(None) == "unknown"


class TestGetHeatLevelCn:
    def test_red(self):
        assert "红色" in get_heat_level_cn(75)

    def test_orange(self):
        assert "橙色" in get_heat_level_cn(60)

    def test_yellow(self):
        assert "黄色" in get_heat_level_cn(50)

    def test_green(self):
        assert "绿色" in get_heat_level_cn(20)


class TestAnalyzeState:
    def test_pending_red(self):
        history = [
            {"trade_date": "2025-06-10", "level": "yellow"},
            {"trade_date": "2025-06-11", "level": "yellow"},
            {"trade_date": "2025-06-12", "level": "red"},
        ]
        event, days = analyze_state(history, "red", "2025-06-13")
        assert event == "pending_red"
        assert days == 1

    def test_enter_red(self):
        history = [
            {"trade_date": "2025-06-09", "level": "yellow"},
            {"trade_date": "2025-06-10", "level": "red"},
            {"trade_date": "2025-06-11", "level": "red"},
            {"trade_date": "2025-06-12", "level": "red"},
        ]
        event, days = analyze_state(history, "red", "2025-06-13")
        assert event == "in_red"

    def test_stable(self):
        history = [
            {"trade_date": "2025-06-11", "level": "yellow"},
            {"trade_date": "2025-06-12", "level": "yellow"},
        ]
        event, days = analyze_state(history, "yellow", "2025-06-13")
        assert event == "stable"

    def test_empty_history(self):
        event, days = analyze_state([], "yellow", "2025-06-13")
        assert event == "stable"
        assert days == 0


class TestHistoryWriteGuards:
    """P0-3: 历史产物防截断覆盖。

    旧行为: history.json 解析失败 → 内存态退化为空列表 → 当日单条覆写整个文件,
    600+ 条历史在没有备份的情况下被抹掉。以下用例锁死"读不到就不写"的契约。
    """

    def test_normal_append(self, tmp_path):
        """基线: 正常文件按去重追加语义工作。"""
        out = tmp_path
        hist_file = out / "history.json"
        hist_file.write_text(json.dumps(_history_entries(3)), encoding="utf-8")

        save_results_v2(_make_result("2025-01-09"), output_dir=str(out))

        data = json.loads(hist_file.read_text(encoding="utf-8"))
        assert len(data) == 4
        assert [h["trade_date"] for h in data] == sorted(h["trade_date"] for h in data)

    def test_same_date_is_deduplicated(self, tmp_path):
        """同一 trade_date 重复调用不产生重复条目，且不触发收缩守卫。"""
        out = tmp_path
        hist_file = out / "history.json"
        hist_file.write_text(json.dumps(_history_entries(3)), encoding="utf-8")

        save_results_v2(_make_result("2025-01-02"), output_dir=str(out))
        save_results_v2(_make_result("2025-01-02"), output_dir=str(out))

        data = json.loads(hist_file.read_text(encoding="utf-8"))
        assert len(data) == 3

    def test_corrupt_history_not_overwritten(self, tmp_path):
        """history.json 内容非法 → 拒绝覆写 + 生成 .corrupt.* 备份。"""
        out = tmp_path
        hist_file = out / "history.json"
        broken = '{"trade_date": "2025-01-01", "composite_sc'  # 截断的非法 JSON
        hist_file.write_text(broken, encoding="utf-8")

        save_results_v2(_make_result("2025-01-09"), output_dir=str(out))

        assert hist_file.read_text(encoding="utf-8") == broken, "损坏文件被覆盖，历史数据丢失"
        corrupt_backups = list(out.glob("history.json.corrupt.*"))
        assert len(corrupt_backups) == 1, "未保留损坏文件证据"
        assert corrupt_backups[0].read_text(encoding="utf-8") == broken

    def test_corrupt_indicator_history_not_overwritten(self, tmp_path):
        """indicator_history.json 损坏 → 同样拒绝覆写（旧实现静默吞异常无日志）。"""
        out = tmp_path
        ind_file = out / "indicator_history.json"
        broken = "[1, 2, 3"  # 非法 JSON
        ind_file.write_text(broken, encoding="utf-8")

        save_results_v2(_make_result("2025-01-09"), output_dir=str(out))

        assert ind_file.read_text(encoding="utf-8") == broken
        assert len(list(out.glob("indicator_history.json.corrupt.*"))) == 1

    def test_wrong_type_not_overwritten(self, tmp_path):
        """内容合法但类型不符（dict 冒充 list）同样视为损坏，不得静默重置为空。"""
        out = tmp_path
        hist_file = out / "history.json"
        hist_file.write_text(json.dumps({"oops": "not a list"}), encoding="utf-8")

        save_results_v2(_make_result("2025-01-09"), output_dir=str(out))

        assert json.loads(hist_file.read_text(encoding="utf-8")) == {"oops": "not a list"}
        assert len(list(out.glob("history.json.corrupt.*"))) == 1

    def test_shrink_guard_blocks_mass_drop(self, tmp_path):
        """大量脏记录被过滤掉时拒绝写回，避免'清洗'变成'删除'。"""
        out = tmp_path
        hist_file = out / "history.json"
        entries = _history_entries(100)
        # 99 条类型错误 → 过滤后只剩 1 条新记录，收缩 99% 应被拦截
        entries[1:] = ["garbage"] * 99
        hist_file.write_text(json.dumps(entries), encoding="utf-8")

        save_results_v2(_make_result("2025-06-01"), output_dir=str(out))

        data = json.loads(hist_file.read_text(encoding="utf-8"))
        assert len(data) == 100, "异常收缩未被守卫拦截"

    def test_missing_file_is_first_run(self, tmp_path):
        """文件不存在 = 首次运行，正常创建。"""
        out = tmp_path
        save_results_v2(_make_result("2025-01-09"), output_dir=str(out))

        data = json.loads((out / "history.json").read_text(encoding="utf-8"))
        assert len(data) == 1
        assert not list(out.glob("*.corrupt.*"))


class TestFeishuWebhookEnv:
    """P1-14: 代码用 FEISHU_WEBHOOK, 文档写 FEISHU_WEBHOOK_URL —— 两个名字都要认。"""

    def test_primary_name(self, monkeypatch):
        monkeypatch.setenv("FEISHU_WEBHOOK", "https://a")
        monkeypatch.delenv("FEISHU_WEBHOOK_URL", raising=False)
        assert get_feishu_webhook() == "https://a"

    def test_documented_alias(self, monkeypatch):
        monkeypatch.delenv("FEISHU_WEBHOOK", raising=False)
        monkeypatch.setenv("FEISHU_WEBHOOK_URL", "https://b")
        assert get_feishu_webhook() == "https://b"

    def test_primary_wins_when_both_set(self, monkeypatch):
        monkeypatch.setenv("FEISHU_WEBHOOK", "https://a")
        monkeypatch.setenv("FEISHU_WEBHOOK_URL", "https://b")
        assert get_feishu_webhook() == "https://a"

    def test_empty_when_neither_set(self, monkeypatch):
        monkeypatch.delenv("FEISHU_WEBHOOK", raising=False)
        monkeypatch.delenv("FEISHU_WEBHOOK_URL", raising=False)
        assert get_feishu_webhook() == ""


class TestNotificationDataQuality:
    """P0-2: 通知里的质量段落由 run_daily 产出的 data_quality 驱动。

    旧代码读的是 ``dim_info['freshness']`` —— 该字段从未有过生产者,
    一旦真的接上生产者就会 KeyError。这里锁死渲染路径的健壮性。
    """

    def _result(self, dq):
        return {
            "trade_date": "2026-09-12",
            "composite_score": 62.0,
            "dimensions": {
                "valuation": {"score": 70.0, "label": "估值"},
                "fund": {"score": 60.0, "label": "资金"},
                "sentiment": {"score": 65.0, "label": "情绪"},
                "structure": {"score": 50.0, "label": "结构"},
            },
            "indicators": {"pe": 85.0},
            "data_quality": dq,
        }

    def _dq(self):
        return {
            "overall_quality": "degraded",
            "missing_indicators": ["turnover", "new_high"],
            "critical_failed_steps": [],
            "dimensions": {
                "valuation": {"status": "ok", "label": "估值", "available": 2, "total": 2, "missing": []},
                "fund": {"status": "ok", "label": "资金", "available": 3, "total": 3, "missing": []},
                "sentiment": {
                    "status": "degraded",
                    "label": "情绪",
                    "available": 1,
                    "total": 2,
                    "missing": ["turnover"],
                },
                "structure": {
                    "status": "degraded",
                    "label": "结构",
                    "available": 1,
                    "total": 2,
                    "missing": ["new_high"],
                },
            },
        }

    def test_quality_section_rendered(self, monkeypatch):
        import src.output.json_writer as jw

        monkeypatch.setattr(jw, "_should_notify", lambda *a, **k: True)
        # analyze_state 空历史返回 "stable" → 函数末尾会拦掉通知, 需放行
        monkeypatch.setattr(jw, "analyze_state", lambda *a, **k: ("enter_red", 1))

        msg = jw.build_feishu_notification(self._result(self._dq()), history=[])
        assert msg is not None
        assert "数据质量" in msg
        assert "估值: 2/2 项计分指标有数据" in msg
        assert "情绪: 1/2 项计分指标有数据" in msg
        # 缺失指标应渲染为中文名而非机器键
        assert "换手率" in msg and "创新高占比" in msg

    def test_good_quality_renders_no_section(self, monkeypatch):
        import src.output.json_writer as jw

        monkeypatch.setattr(jw, "_should_notify", lambda *a, **k: True)
        # analyze_state 空历史返回 "stable" → 函数末尾会拦掉通知, 需放行
        monkeypatch.setattr(jw, "analyze_state", lambda *a, **k: ("enter_red", 1))

        dq = self._dq()
        dq["overall_quality"] = "good"
        msg = jw.build_feishu_notification(self._result(dq), history=[])
        assert msg is not None
        assert "数据质量" not in msg

    def test_missing_data_quality_does_not_break(self, monkeypatch):
        import src.output.json_writer as jw

        monkeypatch.setattr(jw, "_should_notify", lambda *a, **k: True)
        # analyze_state 空历史返回 "stable" → 函数末尾会拦掉通知, 需放行
        monkeypatch.setattr(jw, "analyze_state", lambda *a, **k: ("enter_red", 1))

        result = self._result({})
        result.pop("data_quality")
        msg = jw.build_feishu_notification(result, history=[])
        assert msg is not None
        assert "数据质量" not in msg

    def test_partial_dimension_schema_is_tolerated(self, monkeypatch):
        """生产者字段缺失时用 .get 兜底, 不抛 KeyError。"""
        import src.output.json_writer as jw

        monkeypatch.setattr(jw, "_should_notify", lambda *a, **k: True)
        # analyze_state 空历史返回 "stable" → 函数末尾会拦掉通知, 需放行
        monkeypatch.setattr(jw, "analyze_state", lambda *a, **k: ("enter_red", 1))

        dq = self._dq()
        dq["dimensions"] = {"valuation": {"status": "bad"}}  # 缺 label/available/total
        dq["missing_indicators"] = []
        msg = jw.build_feishu_notification(self._result(dq), history=[])
        assert msg is not None
        assert "valuation: 0/0" in msg
