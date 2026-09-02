"""公共工具：计时与日志辅助（P3-C1）。

提供轻量的耗时统计装饰器/上下文管理器，供 backfill / run_daily 等长任务
记录各阶段与总耗时，便于定位慢步骤。不改动任何计算逻辑。
"""

from __future__ import annotations

import functools
import json as _json
import logging
import platform
import sys
import time
from typing import Any, Callable, Iterator

logger = logging.getLogger(__name__)


def timed(label: str | None = None) -> Callable:
    """函数级耗时装饰器：进入/退出打印耗时。

    用法::

        @timed("backfill_indicator_history")
        def run():
            ...

    也可不传 label，默认用函数名。
    """

    def decorator(fn: Callable) -> Callable:
        name = label or fn.__name__

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            t0 = time.time()
            logger.info("[timed] >> %s start", name)
            try:
                result = fn(*args, **kwargs)
            finally:
                elapsed = time.time() - t0
                logger.info("[timed] << %s done (%.2fs)", name, elapsed)
            return result

        return wrapper

    # 允许 @timed 不带括号使用
    if callable(label):
        fn, label = label, None
        return decorator(fn)
    return decorator


class Timer:
    """上下文管理器：记录一个代码块耗时。

    用法::

        with Timer("phase-a"):
            do_work()
    """

    def __init__(self, label: str) -> None:
        self.label = label
        self._t0 = 0.0

    def __enter__(self) -> "Timer":
        self._t0 = time.time()
        logger.info("[timed] >> %s start", self.label)
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        elapsed = time.time() - self._t0
        logger.info("[timed] << %s done (%.2fs)", self.label, elapsed)


def progress(it: Iterator, total: int | None = None, label: str = "items") -> Iterator:
    """遍历迭代器并按固定步长打印进度（避免刷屏）。

    适合无法预知总数的长循环；每 10% 或每 1000 项打印一次。
    """
    if total is None:
        try:
            total = len(it)  # type: ignore[arg-type]
        except TypeError:
            total = None
    step = max(1, (total // 10) if total else 1000)
    for i, item in enumerate(it, 1):
        if i == 1 or i % step == 0 or (total and i == total):
            if total:
                logger.info("[progress] %s %d/%d (%.0f%%)", label, i, total, 100.0 * i / total)
            else:
                logger.info("[progress] %s %d", label, i)
        yield item


def process_memory_mb() -> float | None:
    """返回当前进程常驻内存(MB)，不可用则返回 None。

    优先用 psutil；回退到 resource.getrusage（macOS 单位字节，Linux 单位 KB）。
    """
    try:
        import psutil

        return psutil.Process().memory_info().rss / (1024 * 1024)
    except Exception:
        pass
    try:
        import resource

        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # macOS 返回字节，Linux 返回 KB
        if sys.platform == "darwin":
            return rss / (1024 * 1024)
        return rss / 1024
    except Exception:
        return None


def runtime_meta() -> dict:
    """收集运行环境元数据，供 run_status.json 等监控输出。"""
    return {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "memory_mb": process_memory_mb(),
    }


class JsonFormatter(logging.Formatter):
    """将日志记录输出为单行 JSON（便于日志采集/ELK）。

    默认文本格式保持不变；仅在启用 JSON 日志时使用本 formatter。
    P3-E1 (#16): 输出键语义对齐结构化日志惯例 — 消息放 ``event``; 日志调用
    通过 ``extra={...}`` 传入的自由字段 (非 logging 内建属性) 自动并入 JSON
    顶层 (仅收标量), 供按 step/phase/trade_date 等维度检索。
    """

    # logging.LogRecord 内建属性 + formatter 派生属性 (不并入 extra)
    _RESERVED = frozenset(
        {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_text",
            "exc_info",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "taskName",
            "message",
            "asctime",
        }
    )

    def _extra(self, record: logging.LogRecord) -> dict:
        out: dict = {}
        for k, v in record.__dict__.items():
            if k in self._RESERVED or k.startswith("_"):
                continue
            if isinstance(v, (str, int, float, bool)) or v is None:
                out[k] = v
        return out

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        payload.update(self._extra(record))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return _json.dumps(payload, ensure_ascii=False)


def setup_logging(
    level: int = logging.INFO,
    json_logs: bool = False,
    log_file: str | None = None,
) -> None:
    """配置根日志（幂等替换 root handlers）。

    Args:
        level: 根日志级别。
        json_logs: True 时输出单行 JSON (JsonFormatter), 否则保持文本格式。
        log_file: 可选日志文件路径; 给出时同时写 stdout 与文件 (同 formatter)。

    用法::

        from src.common import setup_logging
        setup_logging(json_logs=bool(os.environ.get("HEAT_LOG_JSON")), log_file="run.log")
    """
    formatter = JsonFormatter() if json_logs else logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    handlers = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    for h in handlers:
        h.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = handlers
    root.setLevel(level)
