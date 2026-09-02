"""配置加载

P3-B1 (#103): config/*.yaml 的强类型视图 — YAML 为唯一事实源, 代码内置默认仅当
配置缺失/键缺失时兜底。消费方 (heat_index_v2 / json_writer) 统一走
load_config_typed() 的 HeatConfig, 消灭散落 `cfg.get(...)` 手写解析与键名漂移
(含 M2b-3 新增的 v2_engine.mode 计分键模式键)。

层次:
    raw dict          ← load_config()             (validate_config 告警校验)
    HeatConfig        ← load_config_typed()       (强类型视图, 推荐消费)
        .engine       = EngineConfig              (v2_engine 全子块)
        .heat_levels  = Dict[str, HeatLevel]      (恒含 6 档默认切点, YAML 覆盖)
        .raw          = 完整原始 dict             (未类型化的顶层块仍可读)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import yaml

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


# ── 内置默认值 (引擎兜底) ─────────────────────────────────────────────────────
# 数值须与 src/indicators/heat_index_v2.py 的 DEFAULT_WEIGHTS / DEFAULT_DIVERGENCE
# 及旧硬编码切点保持一致; tests/test_config.py 有防漂移断言。

_WEIGHT_DEFAULTS: Dict[str, float] = {
    "pe": 0.212121,  # 大盘PE (估值主锚, 原14% / 0.66)
    "buffett": 0.212121,  # 巴菲特指标 (估值主锚)
    "yield_spread": 0.045455,  # 国债期限利差 10Y-2Y (momentum 流动性宽松)
    "m1_m2_spread": 0.045455,  # M1-M2剪刀差 (momentum 货币活化)
    "margin_buy_ratio": 0.045455,  # 融资买入占比 (momentum 主力)
    "turnover": 0.136364,  # 换手率 (成交热度确认, 原9%)
    "futures_discount": 0.030303,  # IF基差 (独立拐点信号)
    "new_high": 0.181818,  # 创新高占比 (结构确认, 原12%)
    "ma_alignment": 0.090909,  # MA排列比 (结构确认, 原6%)
}

_DIVERGENCE_DEFAULTS: Dict[str, float] = {
    "turnover_threshold": 70.0,  # 换手率超过此值才触发背离检查
    "decline_threshold": -1.5,  # 指数跌幅超过此值(%)触发惩罚
    "penalty_factor": 0.2,  # 每次背离扣除的分数（×100=20分，匹配README文档"最多20分"）
    "lookback_days": 20.0,  # 背离检测的回看天数
    "new_high_penalty": 15.0,  # 顶背离时扣除的结构分
}

# heat_levels 6 档默认切点 (min, max)。yaml 同名档覆盖; 消费方恒可 `hl["red"]` 无 KeyError。
_HEAT_LEVEL_DEFAULTS: Dict[str, Tuple[int, int]] = {
    "red": (65, 100),
    "orange": (55, 64),
    "yellow": (40, 54),
    "green": (0, 39),
    "extreme_hot": (80, 100),
    "extreme_cold": (0, 29),
}

_EXPECTED_WEIGHT_KEYS = tuple(_WEIGHT_DEFAULTS)


# ── 强类型配置模型（P3-B1，stdlib dataclasses，无新依赖）───────────────────────


@dataclass
class EngineWeights:
    """9 计分键权重表。字段默认值 = v3.0 权重字面量 (与 heat_index_v2.DEFAULT_WEIGHTS 同值)。"""

    pe: float = _WEIGHT_DEFAULTS["pe"]
    buffett: float = _WEIGHT_DEFAULTS["buffett"]
    yield_spread: float = _WEIGHT_DEFAULTS["yield_spread"]
    m1_m2_spread: float = _WEIGHT_DEFAULTS["m1_m2_spread"]
    margin_buy_ratio: float = _WEIGHT_DEFAULTS["margin_buy_ratio"]
    turnover: float = _WEIGHT_DEFAULTS["turnover"]
    futures_discount: float = _WEIGHT_DEFAULTS["futures_discount"]
    new_high: float = _WEIGHT_DEFAULTS["new_high"]
    ma_alignment: float = _WEIGHT_DEFAULTS["ma_alignment"]

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EngineWeights":
        """缺失键回落内置默认权重 (YAML 缺键时引擎仍可跑, validate_config 会先告警)。"""
        return cls(**{k: float(d.get(k, _WEIGHT_DEFAULTS[k])) for k in cls.__dataclass_fields__})

    def to_dict(self) -> Dict[str, float]:
        return {k: float(getattr(self, k)) for k in self.__dataclass_fields__}


@dataclass
class HeatLevel:
    min: int
    max: int
    label: str = ""
    color: str = ""

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "HeatLevel":
        return cls(min=int(d["min"]), max=int(d["max"]), label=str(d.get("label", "")), color=str(d.get("color", "")))


def _default_heat_levels() -> Dict[str, HeatLevel]:
    """6 档默认切点 (label/color 空) — 保证消费方无需判缺键。"""
    return {k: HeatLevel(min=lo, max=hi) for k, (lo, hi) in _HEAT_LEVEL_DEFAULTS.items()}


@dataclass
class EngineConfig:
    """v2_engine 配置块强类型视图。各字段默认值 = 引擎内置兜底, YAML 覆盖。

    字段 ↔ YAML 映射 (YAML 为唯一事实源):
        mode                          → v2_engine.mode (M2b-3 计分键模式, 默认 single9)
        weights                       → v2_engine.weights (EngineWeights)
        divergence                    → v2_engine.divergence
        percentile_window             → v2_engine.percentile.rolling_window
        turnover_window_years         → v2_engine.turnover.percentile_window_years
        pe_n_stocks_ratio/min         → v2_engine.pe.n_stocks_filter_{ratio,min}
        margin_saturation_{cutoff,h}  → v2_engine.margin.saturation_{cutoff,headroom}
        new_high_threshold            → v2_engine.new_high.threshold
        detrend_{window,min_periods}  → v2_engine.detrend.{rolling_window,min_periods} (可缺省)
    """

    mode: str = "single9"
    weights: EngineWeights = field(default_factory=EngineWeights)
    divergence: Dict[str, float] = field(default_factory=lambda: dict(_DIVERGENCE_DEFAULTS))
    percentile_window: int = 1260
    turnover_window_years: float = 10.0
    pe_n_stocks_ratio: Tuple[float, float] = (0.5, 1.5)
    pe_n_stocks_min: int = 450
    margin_saturation_cutoff: float = 0.85
    margin_saturation_headroom: float = 0.15
    new_high_threshold: float = 0.98
    detrend_rolling_window: int = 750
    detrend_min_periods: int = 250

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "EngineConfig":
        """d = YAML v2_engine 块 (可空/缺子块) — 缺失键一律回落字段默认 (引擎兜底)。"""
        d = d or {}
        _pe = d.get("pe") or {}
        _margin = d.get("margin") or {}
        _detrend = d.get("detrend") or {}
        return cls(
            mode=str((d.get("mode") or "single9")),
            weights=EngineWeights.from_dict(d.get("weights") or {}),
            divergence={
                **_DIVERGENCE_DEFAULTS,
                **{k: float(v) for k, v in (d.get("divergence") or {}).items()},
            },
            percentile_window=int((d.get("percentile") or {}).get("rolling_window", 1260)),
            turnover_window_years=float((d.get("turnover") or {}).get("percentile_window_years", 10.0)),
            pe_n_stocks_ratio=tuple(float(x) for x in _pe.get("n_stocks_filter_ratio", [0.5, 1.5])),
            pe_n_stocks_min=int(_pe.get("n_stocks_filter_min", 450)),
            margin_saturation_cutoff=float(_margin.get("saturation_cutoff", 0.85)),
            margin_saturation_headroom=float(_margin.get("saturation_headroom", 0.15)),
            new_high_threshold=float((d.get("new_high") or {}).get("threshold", 0.98)),
            detrend_rolling_window=int(_detrend.get("rolling_window", 750)),
            detrend_min_periods=int(_detrend.get("min_periods", 250)),
        )


@dataclass
class HeatConfig:
    """配置强类型视图：engine (v2_engine 全子块) + heat_levels 类型化，raw 保留完整原始 dict。

    heat_levels 恒含 6 档默认切点 (red/orange/yellow/green/extreme_hot/extreme_cold),
    YAML 同名档覆盖其 min/max/label/color → 消费方直接 `hl["red"].min` 无需判缺键。
    `weights` 为兼容 property (→ engine.weights), 旧访问方式仍可用。
    """

    engine: EngineConfig = field(default_factory=EngineConfig)
    heat_levels: Dict[str, HeatLevel] = field(default_factory=_default_heat_levels)
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def weights(self) -> EngineWeights:
        """兼容访问: HeatConfig.weights → engine.weights (9 计分键权重表)。"""
        return self.engine.weights

    @classmethod
    def from_dict(cls, cfg: Dict[str, Any]) -> "HeatConfig":
        cfg = cfg or {}
        hl = _default_heat_levels()
        for k, v in (cfg.get("heat_levels") or {}).items():
            hl[k] = HeatLevel.from_dict(v)
        return cls(engine=EngineConfig.from_dict(cfg.get("v2_engine")), heat_levels=hl, raw=cfg)


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

    # M2b-3: mode 若配置须为已知模式 (未知模式引擎回退 single9 并 WARN, 这里前置告警)
    mode = cfg.get("v2_engine", {}).get("mode")
    if mode is not None and mode not in ("single9", "single6"):
        issues.append(f"v2_engine.mode={mode!r} 未知, 应为 single9/single6")

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
