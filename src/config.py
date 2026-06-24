"""配置加载"""
import os
from typing import Optional, Union
import yaml
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
ENV = os.environ.get("HEAT_INDEX_ENV", "prod")
CONFIG_PATH = os.environ.get(
    "HEAT_INDEX_CONFIG",
    BASE_DIR / "config" / f"{ENV}.yaml"
)


def load_config(path: Optional[Union[str, Path]] = None) -> dict:
    p = Path(path) if path else Path(CONFIG_PATH)
    if not p.exists():
        raise FileNotFoundError(f"Config not found: {p}")
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
