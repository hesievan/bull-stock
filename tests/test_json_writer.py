"""Tests for src/output/json_writer.py — 输出模块"""
import pytest
import json
import os
from pathlib import Path
from datetime import date

from src.output.json_writer import (
    get_heat_level,
    get_heat_level_cn,
    analyze_state,
)


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
