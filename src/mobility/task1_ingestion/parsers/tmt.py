"""Toyota TMT adapter for asymmetric 0x51/0x52 packet structures."""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from mobility.common.logger import log_event
from mobility.common.schemas import DataSource, ParsedRecord, ParseResult
from mobility.task1_ingestion.base import BaseParser


class TMTParser(BaseParser):
    source = DataSource.TMT

    _vehicle_keys = (
        "vehicle_id",
        "vehicle id",
        "vehicleid",
        "imei",
        "vin",
        "chassis no",
        "registration",
        "device id",
    )

    def parse(self, paths: Iterable[Path]) -> ParseResult:
        result = ParseResult()
        for path in paths:
            try:
                payload = self.load_json(path)
                if not isinstance(payload, dict):
                    raise ValueError("TMT JSON root must be an object")
                events = self._events(payload)
                vehicle_id = self._vehicle_id(payload, events, path)
                for sequence, raw_event in enumerate(events, start=1):
                    record, issue = self._parse_event(
                        payload, raw_event, path, sequence, vehicle_id
                    )
                    if record:
                        result.records.append(record)
                    if issue:
                        result.issues.append(issue)
            except Exception as exc:  # isolate a malformed partner file
                result.issues.append(self.file_issue(path, "invalid_tmt_file", exc))

        self._resolve_engine_state(result.records)
        return result

    @staticmethod
    def _events(payload: dict[str, Any]) -> list[dict[str, Any]]:
        singular = payload.get("B2B event")
        if isinstance(singular, dict):
            return [singular]
        plural = payload.get("B2B Event List")
        if isinstance(plural, list):
            return [item for item in plural if isinstance(item, dict)]
        raise ValueError("Missing 'B2B event' or 'B2B Event List'")

    def _vehicle_id(
        self,
        payload: dict[str, Any],
        events: list[dict[str, Any]],
        path: Path,
    ) -> str:
        for container in [payload, *events]:
            for key, value in container.items():
                if key.strip().casefold() in self._vehicle_keys:
                    candidate = self.text(value)
                    if candidate:
                        return candidate

        if self.settings.task1.tmt.vehicle_id_fallback == "error":
            raise ValueError("TMT payload has no usable vehicle identifier")
        vehicle_id = re.sub(
            r"_(?:51|52)(?:_heartbeat)?$", "", path.stem, flags=re.IGNORECASE
        )
        if not vehicle_id:
            raise ValueError("Cannot derive provisional TMT vehicle ID from filename")
        log_event(
            self.logger,
            logging.WARNING,
            "tmt_vehicle_id_fallback",
            source_path=str(path),
            vehicle_id=vehicle_id,
            strategy="filename",
        )
        return vehicle_id

    def _parse_event(
        self,
        payload: dict[str, Any],
        raw: dict[str, Any],
        path: Path,
        sequence: int,
        vehicle_id: str,
    ):
        common_header = payload.get("Common header")
        common_gps = (
            common_header.get("GPS", {}) if isinstance(common_header, dict) else {}
        )
        gps = raw.get("GPS") if isinstance(raw.get("GPS"), dict) else common_gps
        can = self._can_values(raw.get("CAN List"))
        engine_rpm = self.number(can.get("Engine Speed"))
        if engine_rpm is None:
            engine_rpm = self.number(raw.get("Engine RPM by direct line"))
        recorded_at = self.timestamp(
            raw.get("Timestamp") or gps.get("Timestamp"), "%Y%m%d%H%M%S"
        )
        event_code = self.text(raw.get("Event type"))
        return self.build_record(
            path=path,
            sequence=sequence,
            event_code=event_code,
            source=self.source,
            vehicle_id=vehicle_id,
            recorded_at=recorded_at,
            lat=self.number(gps.get("Lat angle")),
            lon=self.number(gps.get("Lon angle")),
            speed_kmh=self.number(raw.get("Vehicle speed")),
            engine_on=None,
            odometer_km=self.number(can.get("Total Distance Traveled")),
            fuel_rate_lh=self.number(can.get("Vehicle Fuel Rate")),
            engine_rpm=engine_rpm,
            accel_position=self.number(can.get("Accel Position")),
            brake_active=self.switch(can.get("Stop Light Switch")),
        )

    @staticmethod
    def _can_values(raw_can_list: Any) -> dict[str, Any]:
        values: dict[str, Any] = {}
        if not isinstance(raw_can_list, list):
            return values
        for item in raw_can_list:
            if not isinstance(item, dict):
                continue
            can = item.get("CAN")
            if isinstance(can, dict):
                values.update(can)
        return values

    def _resolve_engine_state(self, records: list[ParsedRecord]) -> None:
        grouped: dict[str, list[ParsedRecord]] = defaultdict(list)
        for record in records:
            grouped[record.event.vehicle_id].append(record)

        codes = self.settings.task1.tmt.event_codes
        for vehicle_records in grouped.values():
            vehicle_records.sort(
                key=lambda row: (
                    row.event.recorded_at,
                    row.source_path.name,
                    row.source_sequence,
                )
            )
            state: bool | None = None
            for record in vehicle_records:
                if record.event_code == codes.engine_on:
                    state = True
                elif record.event_code == codes.engine_off:
                    state = False
                elif record.event_code == codes.heartbeat:
                    pass
                else:
                    log_event(
                        self.logger,
                        logging.WARNING,
                        "unknown_tmt_event_code",
                        vehicle_id=record.event.vehicle_id,
                        event_code=record.event_code,
                    )
                record.event.engine_on = state
