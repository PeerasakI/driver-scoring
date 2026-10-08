"""WDMT / True Leasing JSON adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from mobility.common.schemas import DataSource, ParseResult
from mobility.task1_ingestion.base import BaseParser


class WDMTParser(BaseParser):
    source = DataSource.WDMT

    def parse(self, paths: Iterable[Path]) -> ParseResult:
        result = ParseResult()
        for path in paths:
            try:
                payload = self.load_json(path)
                if not isinstance(payload, list):
                    raise ValueError("WDMT JSON root must be an array")
                for sequence, row in enumerate(payload, start=1):
                    if not isinstance(row, dict):
                        continue
                    record, issue = self.build_record(
                        path=path,
                        sequence=sequence,
                        source=self.source,
                        vehicle_id=self.text(row.get("registration")),
                        recorded_at=self.timestamp(
                            row.get("local_timestamp"), "%Y-%m-%d %H:%M:%S"
                        ),
                        lat=self.number(row.get("lat")),
                        lon=self.number(row.get("lon")),
                        speed_kmh=self.number(row.get("speed")),
                        engine_on=None,
                        odometer_km=self.number(row.get("mileage")),
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
                result.issues.append(self.file_issue(path, "invalid_wdmt_file", exc))
        return result
