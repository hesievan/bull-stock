"""Tests for src/data/database.py — 数据库管理"""
import pytest
import os
import sqlite3
import tempfile
from pathlib import Path

from src.data.database import (
    get_conn,
    init_database,
    save_dataframe,
    read_dataframe,
    get_latest_date,
    SCHEMA_VERSION,
)
import pandas as pd


@pytest.fixture
def tmp_db(tmp_path):
    """创建临时数据库"""
    db_path = str(tmp_path / "test.db")
    init_database(db_path)
    return db_path


class TestGetConn:
    def test_creates_db_file(self, tmp_path):
        db_path = str(tmp_path / "new.db")
        with get_conn(db_path) as conn:
            conn.execute("SELECT 1")
        assert os.path.exists(db_path)

    def test_commit_on_success(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        with get_conn(db_path) as conn:
            conn.execute("CREATE TABLE t(x)")
            conn.execute("INSERT INTO t VALUES(1)")
        with get_conn(db_path) as conn:
            row = conn.execute("SELECT x FROM t").fetchone()
            assert row[0] == 1

    def test_rollback_on_error(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        with get_conn(db_path) as conn:
            conn.execute("CREATE TABLE t(x)")
        try:
            with get_conn(db_path) as conn:
                conn.execute("INSERT INTO t VALUES(1)")
                raise ValueError("force rollback")
        except ValueError:
            pass
        with get_conn(db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
            assert count == 0

    def test_wal_mode(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        with get_conn(db_path) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode == "wal"


class TestInitDatabase:
    def test_creates_tables(self, tmp_db):
        with get_conn(tmp_db) as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            table_names = {t[0] for t in tables}
            assert "stock_daily" in table_names
            assert "index_daily" in table_names
            assert "heat_index" in table_names
            assert "metadata" in table_names

    def test_schema_version(self, tmp_db):
        with get_conn(tmp_db) as conn:
            ver = conn.execute(
                "SELECT value FROM metadata WHERE key='schema_version'"
            ).fetchone()
            assert ver is not None
            assert int(ver[0]) == SCHEMA_VERSION

    def test_idempotent(self, tmp_db):
        init_database(tmp_db)
        init_database(tmp_db)
        with get_conn(tmp_db) as conn:
            count = conn.execute("SELECT COUNT(*) FROM metadata").fetchone()[0]
            assert count == 1


class TestSaveDataframe:
    def test_save_and_read(self, tmp_db):
        df = pd.DataFrame({"trade_date": ["2025-01-01"], "stock_code": ["000001"], "close": [10.5]})
        save_dataframe(df, "stock_daily", tmp_db)
        result = read_dataframe("SELECT * FROM stock_daily", db_path=tmp_db)
        assert len(result) == 1
        assert result.iloc[0]["stock_code"] == "000001"

    def test_upsert(self, tmp_db):
        df1 = pd.DataFrame({"trade_date": ["2025-01-01"], "stock_code": ["000001"], "close": [10.0]})
        df2 = pd.DataFrame({"trade_date": ["2025-01-01"], "stock_code": ["000001"], "close": [11.0]})
        save_dataframe(df1, "stock_daily", tmp_db)
        save_dataframe(df2, "stock_daily", tmp_db)
        result = read_dataframe("SELECT close FROM stock_daily", db_path=tmp_db)
        assert len(result) == 1
        assert result.iloc[0]["close"] == 11.0

    def test_empty_df(self, tmp_db):
        df = pd.DataFrame()
        save_dataframe(df, "stock_daily", tmp_db)
        result = read_dataframe("SELECT COUNT(*) as c FROM stock_daily", db_path=tmp_db)
        assert result.iloc[0]["c"] == 0


class TestReadDataframe:
    def test_with_params(self, tmp_db):
        df = pd.DataFrame({
            "trade_date": ["2025-01-01", "2025-01-02"],
            "index_code": ["sh000001", "sh000001"],
            "close": [3000.0, 3100.0],
        })
        save_dataframe(df, "index_daily", tmp_db)
        result = read_dataframe(
            "SELECT * FROM index_daily WHERE close > ?", params=(3000,), db_path=tmp_db
        )
        assert len(result) == 1


class TestGetLatestDate:
    def test_empty_table(self, tmp_db):
        result = get_latest_date("stock_daily", db_path=tmp_db)
        assert result is None

    def test_with_data(self, tmp_db):
        df = pd.DataFrame({
            "trade_date": ["2025-01-01", "2025-01-02"],
            "index_code": ["sh000001", "sh000001"],
            "close": [3000.0, 3100.0],
        })
        save_dataframe(df, "index_daily", tmp_db)
        result = get_latest_date("index_daily", db_path=tmp_db)
        assert result == "2025-01-02"



