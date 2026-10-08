"""Task 1 orchestration and optional Silver persistence."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from mobility.common.config import Task1Settings, load_task1_settings
from mobility.common.logger import get_logger, log_event
from mobility.common.schemas import ParseIssue, ParsedRecord
from mobility.task1_ingestion.parsers import SCGJWDParser, TMTParser, WDMTParser


class IngestionPipeline:
    def __init__(self, settings: Task1Settings):
        self.settings = settings
        self.logger = get_logger(settings)
        self.last_issues: list[ParseIssue] = []

    @classmethod
    def from_yaml(cls, config_dir: str | Path | None = None) -> "IngestionPipeline":
        return cls(load_task1_settings(config_dir))

    def parse_all(
        self,
        tmt_files: list[str],
        wdmt_file: str,
        scgjwd_file: str,
    ) -> list[dict]:
        """Parse available sources and return the assignment's normalized dicts."""

        self.last_issues = []
        records: list[ParsedRecord] = []
        source_inputs: list[tuple[str, object, object]] = [
            ("TMT", tmt_files, TMTParser(self.settings, self.logger)),
            ("WDMT", wdmt_file, WDMTParser(self.settings, self.logger)),
            ("SCGJWD", scgjwd_file, SCGJWDParser(self.settings, self.logger)),
        ]

        for source, raw_paths, parser in source_inputs:
            paths = self._available_paths(source, raw_paths)
            if not paths:
                continue
            result = parser.parse(paths)
            records.extend(result.records)
            self.last_issues.extend(result.issues)
            log_event(
                self.logger,
                logging.INFO,
                "source_parsed",
                source=source,
                files=len(paths),
                accepted=len(result.records),
                rejected=len(result.issues),
            )

        records.sort(key=lambda item: (item.event.vehicle_id, item.event.recorded_at))
        output = [record.event.model_dump(mode="python") for record in records]
        log_event(
            self.logger,
            logging.INFO,
            "ingestion_completed",
            accepted=len(output),
            rejected=len(self.last_issues),
        )
        return output

    def _available_paths(self, source: str, raw_paths: object) -> list[Path]:
        values: Iterable[object]
        if isinstance(raw_paths, (list, tuple)):
            values = raw_paths
        elif raw_paths:
            values = [raw_paths]
        else:
            values = []

        paths: list[Path] = []
        supplied = False
        for value in values:
            if value is None or not str(value).strip():
                continue
            supplied = True
            path = Path(str(value))
            if path.is_file():
                paths.append(path)
            else:
                self._missing_source(source, f"file_not_found: {path}")
        if not paths and not supplied:
            self._missing_source(source, "no_input_file")
        return paths

    def _missing_source(self, source: str, reason: str) -> None:
        log_event(
            self.logger,
            logging.WARNING,
            "source_skipped",
            source=source,
            reason=reason,
        )
        if self.settings.task1.ingestion.missing_source_policy == "error":
            raise FileNotFoundError(f"{source}: {reason}")

    def write_silver(self, records: list[dict]) -> Path:
        """Write the canonical dataset; append mode merges and deduplicates reruns."""

        import pandas as pd

        output = self.settings.resolve(self.settings.task1.output.path)
        output.parent.mkdir(parents=True, exist_ok=True)
        incoming = pd.DataFrame.from_records(records)
        mode = self.settings.task1.output.write_mode

        if mode == "append" and output.exists():
            existing = pd.read_parquet(output)
            incoming = pd.concat([existing, incoming], ignore_index=True)

        if self.settings.task1.output.deduplicate and not incoming.empty:
            incoming = incoming.drop_duplicates().sort_values(
                ["vehicle_id", "recorded_at"], kind="stable"
            )
        incoming.to_parquet(output, index=False)
        log_event(
            self.logger,
            logging.INFO,
            "silver_written",
            path=str(output),
            mode=mode,
            rows=len(incoming),
        )
        return output
