"""配置加载"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union
import yaml
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
ENV = os.environ.get("HEAT_INDEX_ENV", "prod")

# ISSUE-14 修复: 空字符串视为未设置, 回退到默认路径
_raw = os.environ.get("HEAT_INDEX_CONFIG", "")
CONFIG_PATH = _raw if _raw else str(BASE_DIR / "config" / f"{ENV}.yaml")


def load_dotenv_safe() -> None:
    """从 .env 文件加载密钥到环境变量（环境变量优先，文件仅作回退）。

    集中处理，避免各脚本重复手写 .env 解析。
    因 fetcher 等在导入时即读取 TUSHARE_TOKEN，调用方必须在 import fetcher 之前调用本函数。
    若可选依赖 python-dotenv 已安装则优先使用，否则回退到内置轻量解析。
    """
    _DOTENV_KEYS = ("TUSHARE_TOKEN", "FEISHU_WEBHOOK", "BARK_KEY", "HEAT_INDEX_ENV", "HEAT_INDEX_DB")
    # 环境变量已设置则无需读取文件
    if all(os.environ.get(k) for k in _DOTENV_KEYS):
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(Path.home() / "daily_stock_analysis" / ".env")
        load_dotenv(BASE_DIR / ".env")
    except ImportError:
        candidates = [
            BASE_DIR / ".env",
            Path.home() / "daily_stock_analysis" / ".env",
        ]
        for p in candidates:
            if p.exists():
                for line in p.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key, val = key.strip(), val.strip().strip("\"'")
                    # 不覆盖已存在的环境变量（环境变量优先）
                    if key and key not in os.environ:
                        os.environ[key] = val


def load_config(path: Optional[Union[str, Path]] = None) -> dict:
    p = Path(path) if path else Path(CONFIG_PATH)
    if not p.exists():
        raise FileNotFoundError(f"Config not found: {p}")
    with open(p, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    # P3-B1: 校验配置（仅告警，不阻断，保持向后兼容）
    for issue in validate_config(cfg):
        logger.warning("config issue: %s", issue)
    return cfg


# ── 强类型配置模型（P3-B1，stdlib dataclasses，无新依赖）───────────────────────

_EXPECTED_WEIGHT_KEYS = (
    "pe",
    "buffett",
    "yield_spread",
    "m1_m2_spread",
    "margin_buy_ratio",
    "turnover",
    "futures_discount",
    "new_high",
    "ma_alignment",
)


@dataclass
class EngineWeights:
    pe: float = 0.0
    buffett: float = 0.0
    yield_spread: float = 0.0
    m1_m2_spread: float = 0.0
    margin_buy_ratio: float = 0.0
    turnover: float = 0.0
    futures_discount: float = 0.0
    new_high: float = 0.0
    ma_alignment: float = 0.0

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EngineWeights":
        return cls(**{k: float(d.get(k, 0.0)) for k in cls.__dataclass_fields__})


@dataclass
class HeatLevel:
    min: int
    max: int
    label: str = ""
    color: str = ""

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "HeatLevel":
        return cls(min=int(d["min"]), max=int(d["max"]), label=str(d.get("label", "")), color=str(d.get("color", "")))


@dataclass
class HeatConfig:
    """配置强类型视图：weights / heat_levels 类型化，其余保留 raw 字典访问。"""

    weights: EngineWeights
    heat_levels: Dict[str, HeatLevel] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, cfg: Dict[str, Any]) -> "HeatConfig":
        return cls(
            weights=EngineWeights.from_dict(cfg.get("v2_engine", {}).get("weights", {})),
            heat_levels={k: HeatLevel.from_dict(v) for k, v in cfg.get("heat_levels", {}).items()},
            raw=cfg,
        )


def validate_config(cfg: Dict[str, Any]) -> List[str]:
    """校验配置结构，返回问题列表（空列表表示通过）。仅告警不抛异常。"""
    issues: List[str] = []
    if not isinstance(cfg, dict):
        return ["config root is not a mapping"]

    # 权重键齐全 + 求和≈1.0
    weights = cfg.get("v2_engine", {}).get("weights", {})
    missing = [k for k in _EXPECTED_WEIGHT_KEYS if k not in weights]
    if missing:
        issues.append(f"v2_engine.weights 缺失键: {missing}")
    try:
        total = sum(float(v) for v in weights.values())
        if abs(total - 1.0) > 0.01:
            issues.append(f"v2_engine.weights 求和={total:.4f}, 应≈1.0")
    except (TypeError, ValueError):
        issues.append("v2_engine.weights 含非数值")

    # 热度等级结构
    levels = cfg.get("heat_levels", {})
    for key in ("red", "orange", "yellow", "green"):
        if key not in levels:
            issues.append(f"heat_levels 缺失: {key}")
            continue
        lv = levels[key]
        if not all(k in lv for k in ("min", "max")):
            issues.append(f"heat_levels.{key} 缺 min/max")

    # M1.6: extreme_hot/extreme_cold 为可选叠加信号档 (向后兼容旧配置);
    # 若配置则校验结构 (需含 min/max)
    for key in ("extreme_hot", "extreme_cold"):
        lv = levels.get(key)
        if lv is not None:
            if not all(k in lv for k in ("min", "max")):
                issues.append(f"heat_levels.{key} 缺 min/max")

    # 数据路径
    if "data" not in cfg or not cfg["data"].get("db_path"):
        issues.append("data.db_path 未配置")

    return issues


def load_config_typed(path: Optional[Union[str, Path]] = None) -> HeatConfig:
    """加载并返回强类型配置（校验已在 load_config 内完成）。"""
    return HeatConfig.from_dict(load_config(path))
