"""Tests for src/common.py — 计时/进度工具 + P3-E1 结构化日志"""

import json
import logging
import sys

from src.common import JsonFormatter, setup_logging, Timer, timed


def _mkrecord(msg: str = "hello world", **extra) -> logging.LogRecord:
    record = logging.LogRecord(
        name="tests.common",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    for k, v in extra.items():
        setattr(record, k, v)
    return record


class TestJsonFormatter:
    """P3-E1: 单行 JSON 输出 (ts/level/logger/event) + extra 字段透传。"""

    def _parse(self, record: logging.LogRecord) -> dict:
        return json.loads(JsonFormatter().format(record))

    def test_basic_fields(self):
        row = self._parse(_mkrecord())
        assert set(row) == {"ts", "level", "logger", "event"}
        assert row["level"] == "INFO"
        assert row["logger"] == "tests.common"
        assert row["event"] == "hello world"

    def test_extra_fields_flattened(self):
        """extra 自由字段并入 JSON 顶层 (供 step/phase/trade_date 维度检索)。"""
        row = self._parse(_mkrecord(step="backfill", phase=2, trade_date="2026-09-02"))
        assert row["step"] == "backfill"
        assert row["phase"] == 2
        assert row["trade_date"] == "2026-09-02"

    def test_exc_info_included(self):
        try:
            raise ValueError("boom")
        except ValueError:
            record = logging.LogRecord(
                name="t",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="failed",
                args=(),
                exc_info=sys.exc_info(),  # (type, value, tb) tuple
            )
        row = self._parse(record)
        assert row["level"] == "ERROR"
        assert "boom" in row["exc"]

    def test_non_scalar_extra_dropped(self):
        """非标量 extra (对象/容器) 不并入, 避免 JSON 序列化失败。"""
        row = self._parse(_mkrecord(bad=object(), lst=[1, 2]))
        assert "bad" not in row and "lst" not in row


class TestSetupLogging:
    """P3-E1: 文本/JSON 切换 + 可选文件输出。"""

    def test_text_format(self, capsys):
        setup_logging(json_logs=False)
        logging.getLogger("tests.common").info("plain text")
        out = capsys.readouterr().err
        assert "[INFO]" in out and "plain text" in out

    def test_json_format(self, capsys):
        setup_logging(json_logs=True)
        logging.getLogger("tests.common").info("json text")
        row = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
        assert row["event"] == "json text"
        assert row["level"] == "INFO"

    def test_log_file(self, tmp_path, capsys):
        log_file = tmp_path / "run.log"
        setup_logging(json_logs=True, log_file=str(log_file))
        logging.getLogger("tests.common").info("to file")
        lines = log_file.read_text(encoding="utf-8").strip().splitlines()
        assert json.loads(lines[-1])["event"] == "to file"


class TestTimedHelpers:
    """计时/进度工具基础行为 (P3-C1 基建回归)。"""

    def test_timed_decorator(self, capsys):
        setup_logging(json_logs=False)

        @timed("phase-x")
        def _work():
            return 42

        assert _work() == 42
        out = capsys.readouterr().err
        assert "[timed] >> phase-x start" in out
        assert "[timed] << phase-x done" in out

    def test_timer_context(self, capsys):
        setup_logging(json_logs=False)
        with Timer("block-y"):
            pass
        out = capsys.readouterr().err
        assert "[timed] >> block-y start" in out
        assert "[timed] << block-y done" in out
