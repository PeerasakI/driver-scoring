"""SCGJWD Logistics CSV/XLSX adapter."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Iterable, Iterator

from mobility.common.schemas import DataSource, ParseResult
from mobility.task1_ingestion.base import BaseParser


class SCGJWDParser(BaseParser):
    source = DataSource.SCGJWD

    def parse(self, paths: Iterable[Path]) -> ParseResult:
        result = ParseResult()
        for path in paths:
            try:
                for sequence, row in enumerate(self._rows(path), start=2):
                    record, issue = self.build_record(
                        path=path,
                        sequence=sequence,
                        source=self.source,
                        vehicle_id=self.text(row.get("IMEI")),
                        recorded_at=self.timestamp(
                            row.get("GPS_TIME"), "%Y-%m-%d %H:%M:%S"
                        ),
                        lat=self.number(row.get("LATITUDE")),
                        lon=self.number(row.get("LONGITUDE")),
                        speed_kmh=self.number(row.get("SPEED")),
                        engine_on=self.switch(row.get("ENGINE_STATUS")),
                        odometer_km=None,
                        fuel_rate_lh=None,
                        engine_rpm=None,
                        accel_position=None,
                        brake_active=None,
                    )
                    if record:
                        result.records.append(record)
                    if issue:
                        result.issues.append(issue)
            except Exception as exc:
                result.issues.append(self.file_issue(path, "invalid_scgjwd_file", exc))
        return result

    @staticmethod
    def _rows(path: Path) -> Iterator[dict[str, Any]]:
        if path.suffix.casefold() == ".csv":
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                yield from csv.DictReader(stream)
            return
        if path.suffix.casefold() in {".xlsx", ".xls"}:
            try:
                import pandas as pd
            except ImportError as exc:  # pragma: no cover - dependency error path
                raise RuntimeError(
                    "pandas/openpyxl is required for Excel input"
                ) from exc
            frame = pd.read_excel(path, dtype={"IMEI": str})
            yield from frame.to_dict(orient="records")
            return
        raise ValueError(f"Unsupported SCGJWD file type: {path.suffix}")
