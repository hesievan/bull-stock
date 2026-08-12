"""Tests for src/config.py — 配置加载"""
import pytest
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from src.config import load_config, BASE_DIR


class TestLoadConfig:
    def test_load_default(self):
        config = load_config()
        assert "heat_levels" in config
        assert "v2_engine" in config

    def test_load_custom_path(self, tmp_path):
        config_file = tmp_path / "test.yaml"
        config_file.write_text("key: value\n", encoding="utf-8")
        config = load_config(config_file)
        assert config["key"] == "value"

    def test_file_not_found(self, tmp_path):
        fake_path = tmp_path / "nonexistent.yaml"
        with pytest.raises(FileNotFoundError):
            load_config(fake_path)

    def test_heat_levels_structure(self):
        config = load_config()
        levels = config["heat_levels"]
        for key in ("red", "orange", "yellow", "green"):
            assert key in levels, f"missing heat level: {key}"
        for level in levels.values():
            assert "min" in level
            assert "max" in level
            assert "label" in level

    def test_env_config(self, tmp_path):
        env_config = tmp_path / "dev.yaml"
        env_config.write_text("custom: true\n", encoding="utf-8")
        with patch("src.config.CONFIG_PATH", str(env_config)):
            config = load_config()
            assert config.get("custom") is True


class TestV2EngineConfig:
    """F7: config/*.yaml 的 v2_engine 配置块与 heat_index_v2 同步"""

    def test_prod_and_dev_have_v2_engine(self):
        """prod.yaml 与 dev.yaml 均含 v2_engine 块, 权重 sum=1.0"""
        for env in ("prod", "dev"):
            cfg = load_config(BASE_DIR / "config" / f"{env}.yaml")
            v2 = cfg.get("v2_engine")
            assert v2 is not None, f"{env}.yaml missing v2_engine"
            weights = v2["weights"]
            assert abs(sum(weights.values()) - 1.0) < 0.001, f"{env} weights must sum to 1.0"
            assert set(weights) == {
                "pe", "buffett", "margin_ratio", "north_ratio", "yield_spread",
                "m1_m2_spread", "seal_rate", "turnover_m2", "turnover",
                "new_high", "ma_alignment",
            }

    def test_v2_engine_matches_code_defaults(self):
        """YAML 值应与 heat_index_v2 内置默认一致 (YAML 为唯一事实源)"""
        from src.indicators.heat_index_v2 import (
            DEFAULT_DIVERGENCE, DEFAULT_WEIGHTS, NEW_HIGH_THRESHOLD,
            TURNOVER_WINDOW_YEARS, PE_N_STOCKS_RATIO, PE_N_STOCKS_MIN,
            SATURATION_CUTOFF, SATURATION_HEADROOM,
        )
        cfg = load_config(BASE_DIR / "config" / "prod.yaml")["v2_engine"]
        assert cfg["weights"] == DEFAULT_WEIGHTS
        assert cfg["divergence"] == DEFAULT_DIVERGENCE
        assert cfg["new_high"]["threshold"] == NEW_HIGH_THRESHOLD
        assert cfg["turnover"]["percentile_window_years"] == TURNOVER_WINDOW_YEARS
        assert tuple(cfg["pe"]["n_stocks_filter_ratio"]) == PE_N_STOCKS_RATIO
        assert cfg["pe"]["n_stocks_filter_min"] == PE_N_STOCKS_MIN
        assert cfg["margin"]["saturation_cutoff"] == SATURATION_CUTOFF
        assert cfg["margin"]["saturation_headroom"] == SATURATION_HEADROOM

    def test_load_v2_config_from_yaml(self, tmp_path):
        """修改 YAML 后 _load_v2_config 返回新值 (分数随之变化的基础)"""
        from unittest.mock import patch as mpatch
        import src.indicators.heat_index_v2 as h
        yaml_file = tmp_path / "cfg.yaml"
        yaml_file.write_text(
            "v2_engine:\n"
            "  weights:\n"
            "    pe: 0.20\n"
            "    buffett: 0.10\n"
            "    margin_ratio: 0.15\n"
            "    seal_rate: 0.25\n"
            "    turnover_m2: 0.10\n"
            "    turnover: 0.10\n"
            "    new_high: 0.06\n"
            "    ma_alignment: 0.04\n",
            encoding="utf-8",
        )
        with mpatch.object(h, "load_config", lambda *a, **k: load_config(yaml_file)):
            cfg = h._load_v2_config()
        assert cfg["weights"]["pe"] == 0.20
        assert abs(sum(cfg["weights"].values()) - 1.0) < 0.001

    def test_fallback_defaults_on_missing_config(self):
        """config 缺失/异常 → _load_v2_config 返回空 dict, 走内置默认值"""
        from unittest.mock import patch as mpatch
        import src.indicators.heat_index_v2 as h

        def _boom(*a, **k):
            raise FileNotFoundError("no config")

        with mpatch.object(h, "load_config", _boom):
            assert h._load_v2_config() == {}

    def test_fallback_defaults_on_missing_block(self):
        """config 存在但无 v2_engine 块 → 返回空 dict"""
        from unittest.mock import patch as mpatch
        import src.indicators.heat_index_v2 as h
        with mpatch.object(h, "load_config", lambda *a, **k: {"heat_levels": {}}):
            assert h._load_v2_config() == {}
