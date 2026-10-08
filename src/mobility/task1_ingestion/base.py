"""Base parser contract, sanitization, and consistent rejection handling."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from math import isfinite
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from mobility.common.config import Task1Settings
from mobility.common.logger import log_event
from mobility.common.schemas import (
    DataSource,
    ParseIssue,
    ParsedRecord,
    ParseResult,
    UnifiedGPSEvent,
)


NULL_TOKENS = {"", "-", "null", "none", "n/a", "na"}


class BaseParser(ABC):
    source: DataSource

    def __init__(self, settings: Task1Settings, logger: logging.Logger):
        self.settings = settings
        self.logger = logger
        self.timezone = ZoneInfo(settings.common.project.timezone)

    @abstractmethod
    def parse(self, paths: Iterable[Path]) -> ParseResult:
        """Parse one or more paths without aborting on a bad individual record."""

    @staticmethod
    def load_json(path: Path) -> Any:
        with path.open("r", encoding="utf-8-sig") as stream:
            return json.load(stream)

    @staticmethod
    def text(value: Any) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        return None if cleaned.lower() in NULL_TOKENS else cleaned

    @classmethod
    def number(cls, value: Any) -> float | None:
        cleaned = cls.text(value)
        if cleaned is None:
            return None
        try:
            parsed = float(cleaned)
        except (TypeError, ValueError):
            return None
        return parsed if isfinite(parsed) else None

    @classmethod
    def switch(cls, value: Any) -> bool | None:
        cleaned = cls.text(value)
        if cleaned is None:
            return None
        normalized = cleaned.casefold()
        if normalized in {"1", "true", "y", "yes", "on"}:
            return True
        if normalized in {"0", "false", "n", "no", "off"}:
            return False
        return None

    def timestamp(self, value: Any, *formats: str) -> datetime | None:
        cleaned = self.text(value)
        if cleaned is None:
            return None
        for timestamp_format in formats:
            try:
                return datetime.strptime(cleaned, timestamp_format).replace(
                    tzinfo=self.timezone
                )
            except ValueError:
                continue
        try:
            parsed = datetime.fromisoformat(cleaned)
        except ValueError:
            return None
        return parsed.replace(tzinfo=self.timezone) if parsed.tzinfo is None else parsed

    def build_record(
        self,
        *,
        path: Path,
        sequence: int,
        event_code: str | None = None,
        **values: Any,
    ) -> tuple[ParsedRecord | None, ParseIssue | None]:
        try:
            event = UnifiedGPSEvent.model_validate(values)
        except ValidationError as exc:
            issue = ParseIssue(
                source=self.source,
                source_path=path,
                row=sequence,
                reason="record_validation_failed",
                details={"errors": exc.errors(include_url=False)},
            )
            log_event(
                self.logger,
                logging.WARNING,
                "record_rejected",
                source=self.source.value,
                source_path=str(path),
                row=sequence,
                reason=issue.reason,
            )
            if self.settings.task1.ingestion.invalid_record_policy == "error":
                raise
            return None, issue
        return ParsedRecord(event, path, sequence, event_code), None

    def file_issue(self, path: Path, reason: str, error: Exception) -> ParseIssue:
        log_event(
            self.logger,
            logging.ERROR,
            "source_file_failed",
            source=self.source.value,
            source_path=str(path),
            reason=reason,
            error=str(error),
        )
        if self.settings.task1.ingestion.invalid_record_policy == "error":
            raise error
        return ParseIssue(self.source, path, reason, details={"error": str(error)})
