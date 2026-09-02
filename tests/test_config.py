"""Tests for src/config.py — 配置加载"""

import pytest
from unittest.mock import patch

from src.config import (
    BASE_DIR,
    EngineConfig,
    EngineWeights,
    HeatConfig,
    load_config,
    load_config_typed,
    validate_config,
)


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
                # M1.4+M1.5: 权重收敛 16→9 计分键 (移出 7 键仅展示不计分)
                "pe",
                "buffett",
                "yield_spread",
                "m1_m2_spread",
                "margin_buy_ratio",
                "turnover",
                "futures_discount",
                "new_high",
                "ma_alignment",
            }

    def test_v2_engine_matches_code_defaults(self):
        """YAML 值应与 heat_index_v2 内置默认一致 (YAML 为唯一事实源)"""
        from src.indicators.heat_index_v2 import (
            DEFAULT_DIVERGENCE,
            DEFAULT_WEIGHTS,
            NEW_HIGH_THRESHOLD,
            ROLLING_PCT_WINDOW,
            TURNOVER_WINDOW_YEARS,
            PE_N_STOCKS_RATIO,
            PE_N_STOCKS_MIN,
            SATURATION_CUTOFF,
            SATURATION_HEADROOM,
        )

        cfg = load_config(BASE_DIR / "config" / "prod.yaml")["v2_engine"]
        assert cfg["weights"] == DEFAULT_WEIGHTS
        assert cfg["divergence"] == DEFAULT_DIVERGENCE
        assert cfg["new_high"]["threshold"] == NEW_HIGH_THRESHOLD
        assert cfg["turnover"]["percentile_window_years"] == TURNOVER_WINDOW_YEARS
        assert cfg["percentile"]["rolling_window"] == ROLLING_PCT_WINDOW
        assert tuple(cfg["pe"]["n_stocks_filter_ratio"]) == PE_N_STOCKS_RATIO
        assert cfg["pe"]["n_stocks_filter_min"] == PE_N_STOCKS_MIN
        assert cfg["margin"]["saturation_cutoff"] == SATURATION_CUTOFF
        assert cfg["margin"]["saturation_headroom"] == SATURATION_HEADROOM

    def test_yaml_to_typed_engine(self, tmp_path):
        """修改 YAML 后 HeatConfig 读出新值 (分数随之变化的基础; P3-B1 取代 _load_v2_config)"""
        yaml_file = tmp_path / "cfg.yaml"
        yaml_file.write_text(
            "v2_engine:\n"
            "  mode: single6\n"
            "  weights:\n"
            "    pe: 0.30\n"
            "    buffett: 0.20\n"
            "    yield_spread: 0.05\n"
            "    m1_m2_spread: 0.05\n"
            "    margin_buy_ratio: 0.05\n"
            "    turnover: 0.10\n"
            "    futures_discount: 0.05\n"
            "    new_high: 0.10\n"
            "    ma_alignment: 0.10\n",
            encoding="utf-8",
        )
        hc = HeatConfig.from_dict(load_config(yaml_file))
        assert hc.engine.mode == "single6"
        assert hc.engine.weights.to_dict()["pe"] == 0.30
        assert abs(sum(hc.engine.weights.to_dict().values()) - 1.0) < 0.001
        # YAML 未配 detrend 子块 → 回落内置默认
        assert hc.engine.detrend_rolling_window == 750

    def test_fallback_defaults_on_missing_config(self):
        """config 缺失/异常 → 引擎 _load_typed_config 返回全默认 HeatConfig (内置兜底)"""
        from unittest.mock import patch as mpatch
        import src.indicators.heat_index_v2 as h

        def _boom(*a, **k):
            raise FileNotFoundError("no config")

        with mpatch.object(h, "load_config_typed", _boom):
            tc = h._load_typed_config()
        assert isinstance(tc, HeatConfig)
        assert tc.engine.mode == "single9"
        assert abs(sum(tc.engine.weights.to_dict().values()) - 1.0) < 0.001
        assert tc.heat_levels["red"].min == 65  # 默认切点兜底

    def test_fallback_defaults_on_empty_config(self):
        """config 存在但内容为空 → HeatConfig() 全默认构造 (引擎常量仍可导入)"""
        import src.indicators.heat_index_v2 as h

        tc = HeatConfig()
        assert tc.engine.weights.to_dict()["pe"] == h.DEFAULT_WEIGHTS["pe"]
        assert tc.engine.mode == "single9"
        # 引擎模块级常量在默认配置下与内置字面量同值 (可正常导入运行)
        assert h.INDICATOR_WEIGHTS == h.DEFAULT_WEIGHTS


class TestValidateConfig:
    """P3-B1: 配置校验应捕获常见错误配置而不抛异常。"""

    def _good(self):
        return {
            "v2_engine": {
                "weights": {  # M1.4+M1.5: 9 计分键重归一 (原 0.66 → 1.0, 各键 ÷0.66)
                    "pe": 0.212121,
                    "buffett": 0.212121,
                    "yield_spread": 0.045455,
                    "m1_m2_spread": 0.045455,
                    "margin_buy_ratio": 0.045455,
                    "turnover": 0.136364,
                    "futures_discount": 0.030303,
                    "new_high": 0.181818,
                    "ma_alignment": 0.090909,
                }
            },
            "heat_levels": {
                "red": {"min": 65, "max": 100, "label": "红", "color": "#f00"},
                "orange": {"min": 55, "max": 64, "label": "橙", "color": "#e58"},
                "yellow": {"min": 40, "max": 54, "label": "黄", "color": "#d29"},
                "green": {"min": 0, "max": 39, "label": "绿", "color": "#3f9"},
            },
            "data": {"db_path": "data/x.db"},
        }

    def test_valid_config_has_no_issues(self):
        assert validate_config(self._good()) == []

    def test_missing_weight_keys(self):
        cfg = self._good()
        del cfg["v2_engine"]["weights"]["pe"]
        issues = validate_config(cfg)
        assert any("缺失键" in i for i in issues)

    def test_weight_sum_off(self):
        cfg = self._good()
        cfg["v2_engine"]["weights"]["pe"] = 0.5
        issues = validate_config(cfg)
        assert any("求和" in i for i in issues)

    def test_missing_heat_level(self):
        cfg = self._good()
        del cfg["heat_levels"]["red"]
        issues = validate_config(cfg)
        assert any("heat_levels 缺失" in i for i in issues)

    def test_malformed_extreme_level(self):
        """M1.6: extreme_hot 缺 min/max → 报错 (键本身可选, 配置了就必须完整)"""
        cfg = self._good()
        cfg["heat_levels"]["extreme_hot"] = {"label": "极热"}
        issues = validate_config(cfg)
        assert any("heat_levels.extreme_hot 缺 min/max" in i for i in issues)

    def test_missing_db_path(self):
        cfg = self._good()
        del cfg["data"]
        issues = validate_config(cfg)
        assert any("db_path" in i for i in issues)


class TestLoadConfigTyped:
    """P3-B1: 强类型配置视图。"""

    def test_typed_weights_and_levels(self):
        cfg = load_config_typed(BASE_DIR / "config" / "prod.yaml")
        assert abs(sum(cfg.weights.__dict__.values()) - 1.0) < 0.01
        # M1.6: heat_levels 含连续展示 4 档 + 极值叠加档 2 键
        assert set(cfg.heat_levels) >= {"red", "orange", "yellow", "green", "extreme_hot", "extreme_cold"}
        assert cfg.heat_levels["red"].min == 65
        assert cfg.heat_levels["extreme_hot"].min == 80
        assert cfg.heat_levels["extreme_cold"].max == 29
        assert cfg.raw["v2_engine"]["weights"]["pe"] > 0

    def test_engine_typed_fields(self):
        """P3-B1: v2_engine 全子块强类型覆盖 (含 M2b-3 mode 键) — YAML 有值断言"""
        cfg = load_config_typed(BASE_DIR / "config" / "prod.yaml")
        eng = cfg.engine
        assert isinstance(eng, EngineConfig)
        assert eng.mode == "single9"  # M2b-3 mode 键被强类型覆盖
        assert eng.new_high_threshold == 0.98
        assert eng.percentile_window == 1260
        assert eng.turnover_window_years == 10.0
        assert eng.pe_n_stocks_min == 450
        assert eng.pe_n_stocks_ratio == (0.5, 1.5)
        assert eng.margin_saturation_cutoff == 0.85
        assert eng.margin_saturation_headroom == 0.15
        # YAML 无 detrend 子块 → 回落内置默认
        assert eng.detrend_rolling_window == 750
        assert eng.detrend_min_periods == 250
        # typed weights 与 raw dict 一致 (单一事实源穿透)
        assert eng.weights.to_dict() == cfg.raw["v2_engine"]["weights"]
        # YAML label/color 透传到 HeatLevel
        assert cfg.heat_levels["red"].label == "红色预警"
        assert cfg.heat_levels["red"].color == "#f85149"

    def test_engine_defaults_match_engine_literals(self):
        """防漂移: config.py 内置默认 (EngineWeights/EngineConfig) == 引擎 DEFAULT_* 字面量"""
        from src.indicators.heat_index_v2 import DEFAULT_DIVERGENCE, DEFAULT_WEIGHTS

        assert EngineWeights().to_dict() == DEFAULT_WEIGHTS
        assert EngineConfig().divergence == {k: float(v) for k, v in DEFAULT_DIVERGENCE.items()}
        assert EngineConfig().mode == "single9"
        # heat_levels 默认切点 (缺配置时引擎兜底阈值)
        assert HeatConfig().heat_levels["red"].min == 65
        assert HeatConfig().heat_levels["extreme_cold"].max == 29
