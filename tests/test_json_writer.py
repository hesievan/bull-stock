"""Tests for src/output/json_writer.py — 输出模块"""

import json
from datetime import date, timedelta

from src.output.json_writer import (
    get_heat_level,
    get_heat_level_cn,
    analyze_state,
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
