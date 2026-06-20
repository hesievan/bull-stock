"""Tests for scripts/run_daily.py — 每日运行入口"""
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestRunStep:
    def test_success(self):
        from scripts.run_daily import _run_step
        status = {}
        result = _run_step(status, "test", lambda: "ok")
        assert result == "ok"
        assert status["test"]["status"] == "OK"

    def test_skip(self):
        from scripts.run_daily import _run_step
        status = {}
        result = _run_step(status, "test", lambda: False)
        assert result is False
        assert status["test"]["status"] == "SKIPPED"

    def test_failure(self):
        from scripts.run_daily import _run_step
        status = {}
        result = _run_step(status, "test", lambda: 1 / 0)
        assert result is None
        assert status["test"]["status"] == "FAILED"
        assert "division" in status["test"]["detail"]
