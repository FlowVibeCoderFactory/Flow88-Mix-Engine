from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from runtime_config import get_runtime_settings


def create_render_logger() -> tuple[logging.Logger, Path]:
    logs_dir = get_runtime_settings().logs_dir
    logs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = int(time.time())
    log_path = logs_dir / f"render_{timestamp}_{time.time_ns()}.log"

    logger = logging.getLogger(f"render.{timestamp}.{time.time_ns()}")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger, log_path


def close_render_logger(logger: logging.Logger | None) -> None:
    if logger is None:
        return

    for handler in list(logger.handlers):
        try:
            handler.flush()
            handler.close()
        finally:
            logger.removeHandler(handler)


def log_structured(logger: logging.Logger | None, event: str, **fields: Any) -> None:
    if logger is None:
        return

    payload = {"event": event, **fields}
    logger.info(json.dumps(payload, sort_keys=True, default=str))
