"""Minimal structured logging without a framework-specific dependency."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mobility.common.config import Task1Settings, Task2Settings, Task3Settings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        context = getattr(record, "context", None)
        if isinstance(context, dict):
            payload.update(context)
        return json.dumps(payload, ensure_ascii=False, default=str)


def get_logger(
    settings: Task1Settings | Task2Settings | Task3Settings,
    name: str = "mobility.task1",
    log_file: Path | None = None,
) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(settings.common.logging.level.upper())
    logger.propagate = False
    if logger.handlers:
        return logger

    log_path = settings.resolve(log_file or settings.common.logging.file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    if settings.common.logging.format == "jsonl":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    **context: Any,
) -> None:
    logger.log(level, event, extra={"context": context})
