from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any


LOGS_DIR = Path("logs")


def create_render_logger() -> tuple[logging.Logger, Path]:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = int(time.time())
    log_path = LOGS_DIR / f"render_{timestamp}.log"

    logger = logging.getLogger("render")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    for existing_handler in list(logger.handlers):
        logger.removeHandler(existing_handler)
        existing_handler.close()

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
