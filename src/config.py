"""配置加载"""
import os
import yaml
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
CONFIG_PATH = os.environ.get("HEAT_INDEX_CONFIG", BASE_DIR / "config" / "default.yaml")


def load_config(path: str | Path = None) -> dict:
    p = Path(path) if path else Path(CONFIG_PATH)
    if not p.exists():
        raise FileNotFoundError(f"Config not found: {p}")
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
