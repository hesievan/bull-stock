"""配置加载"""
import os
from typing import Optional, Union
import yaml
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
ENV = os.environ.get("HEAT_INDEX_ENV", "prod")

# ISSUE-14 修复: 空字符串视为未设置, 回退到默认路径
_raw = os.environ.get("HEAT_INDEX_CONFIG", "")
CONFIG_PATH = _raw if _raw else str(BASE_DIR / "config" / f"{ENV}.yaml")


def load_config(path: Optional[Union[str, Path]] = None) -> dict:
    p = Path(path) if path else Path(CONFIG_PATH)
    if not p.exists():
        raise FileNotFoundError(f"Config not found: {p}")
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
