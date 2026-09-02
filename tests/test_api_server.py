"""Tests for scripts/api_server.py — REST API 端点测试 (P3-E3 #12 健康检查)。

/api/health 已于 #12 前实现 (读 index.json/run_status.json 时效 + DB 连接/schema + 运行时元数据);
本文件为该端点补上行为锁定, 并对公共只读端点做冒烟测试。
fastapi/httpx 为可选依赖 — 缺失时整模块 skip (本地精简环境不影响其它测试)。
"""

import json
import platform
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# fastapi / httpx 仅在装有依赖的环境可用 (CI 经 requirements.txt + requirements-dev.txt 安装)
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

import scripts.api_server as api_server  # noqa: E402
from src.data.database import SCHEMA_VERSION  # noqa: E402


def _write_json(dirpath: Path, name: str, data: dict) -> None:
    (dirpath / name).write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def api_data(tmp_path):
    """最小 web/data 目录: index.json + run_status.json"""
    _write_json(
        tmp_path,
        "index.json",
        {"trade_date": "2026-09-02", "updated_at": "2026-09-02T09:00:00Z", "composite_score": 51.6},
    )
    _write_json(tmp_path, "run_status.json", {"generated_at": "2026-09-02T09:05:00Z", "n_failed": 0})
    return tmp_path


@pytest.fixture
def client(api_data, monkeypatch):
    monkeypatch.setattr(api_server, "WEB_DATA", api_data)
    return TestClient(api_server.create_app())


# ── 假 DB: 让 /api/health 的 DB 分支不依赖真实 data/heat_index.db ──────────────
class _FakeCursor:
    def fetchone(self):
        return (42,)


class _FakeConn:
    def execute(self, _sql):
        return _FakeCursor()


@contextmanager
def _fake_get_conn(*_args, **_kwargs):
    yield _FakeConn()


def _raise_get_conn(*_args, **_kwargs):
    raise RuntimeError("db unavailable")


class TestHealth:
    """P3-E3: /api/health 返回数据时效 + DB 状态 + 运行元数据。"""

    def test_health_ok(self, client, monkeypatch):
        monkeypatch.setattr("src.data.database.get_conn", _fake_get_conn)
        resp = client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        # 数据时效 (来自 index.json / run_status.json)
        assert body["data_date"] == "2026-09-02"
        assert body["generated_at"] == "2026-09-02T09:00:00Z"
        assert body["last_run"] == "2026-09-02T09:05:00Z"
        assert body["last_run_ok"] is True
        # DB 连接 + schema 版本
        assert body["db"] == {"status": "ok", "tables": 42, "schema_version": SCHEMA_VERSION}
        # 运行环境元数据
        assert body["python_version"] == platform.python_version()

    def test_health_db_degraded(self, client, monkeypatch):
        """DB 异常时 status=degraded 且数据时效字段不受影响。"""
        monkeypatch.setattr("src.data.database.get_conn", _raise_get_conn)
        body = client.get("/api/health").json()
        assert body["status"] == "degraded"
        assert body["db"]["status"] == "error"
        assert "detail" in body["db"]
        assert body["data_date"] == "2026-09-02"

    def test_health_last_run_failed(self, client, api_data, monkeypatch):
        """run_status.n_failed > 0 → last_run_ok=False。"""
        _write_json(api_data, "run_status.json", {"generated_at": "2026-09-02T09:05:00Z", "n_failed": 2})
        monkeypatch.setattr("src.data.database.get_conn", _fake_get_conn)
        body = client.get("/api/health").json()
        assert body["last_run_ok"] is False

    def test_health_missing_files(self, tmp_path, monkeypatch):
        """无 index.json / run_status.json → 时效字段为 None (非 degraded)。"""
        monkeypatch.setattr(api_server, "WEB_DATA", tmp_path)
        monkeypatch.setattr("src.data.database.get_conn", _fake_get_conn)
        body = TestClient(api_server.create_app()).get("/api/health").json()
        assert body["status"] == "ok"
        assert body["data_date"] is None
        assert body["generated_at"] is None
        assert body["last_run"] is None
        assert body["last_run_ok"] is None


class TestPublicEndpoints:
    """公共只读端点冒烟 (GET-only, 不写任何文件)。"""

    def test_heat_returns_index(self, client):
        body = client.get("/api/heat").json()
        assert body["trade_date"] == "2026-09-02"
        assert body["composite_score"] == 51.6

    def test_heat_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(api_server, "WEB_DATA", tmp_path)
        body = TestClient(api_server.create_app()).get("/api/heat").json()
        assert body == {"error": "No data available"}

    def test_strategy_hold(self, client):
        body = client.get("/api/strategy").json()
        assert body["signal"] == "hold"
        assert body["level"] == "yellow"

    def test_sectors_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(api_server, "WEB_DATA", tmp_path)
        body = TestClient(api_server.create_app()).get("/api/sectors").json()
        assert body == {"error": "No sector data available"}
