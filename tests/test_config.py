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
        assert "dimension_weights" in config

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
