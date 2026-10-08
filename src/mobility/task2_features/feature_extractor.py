"""Task 2 orchestration and one-row-per-trip feature extraction."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, time
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import ValidationError

from mobility.common.config import Task2Settings, load_task2_settings
from mobility.common.geo_utils import haversine_km
from mobility.common.logger import get_logger, log_event
from mobility.common.schemas import (
    DataGrade,
    DistanceMethod,
    TimeOfDayRisk,
    TripFeature,
    UnifiedGPSEvent,
)
from mobility.task2_features.harsh_events import detect_harsh_events
from mobility.task2_features.segmenter import TripSegment, TripSegmenter


OUTPUT_COLUMNS = list(TripFeature.model_fields)
CAN_COLUMNS = ("fuel_rate_lh", "engine_rpm", "accel_position", "brake_active")


class TripFeatureExtractor:
    def __init__(self, settings: Task2Settings):
        self.settings = settings
        self.logger = get_logger(
            settings,
            name="mobility.task2",
            log_file=settings.task2.logging.file,
        )
        self.segmenter = TripSegmenter(settings.task2.segmentation, self.logger)
        self.last_issues: list[dict[str, Any]] = []

    @classmethod
    def from_yaml(cls, config_dir: str | Path | None = None) -> "TripFeatureExtractor":
        return cls(load_task2_settings(config_dir))

    def transform(self, unified_stream: list[dict]) -> pd.DataFrame:
        """Validate, segment, and summarize a unified event stream in memory."""

        events = self._normalize_input(unified_stream)
        if events.empty:
            return self._empty_output()

        features: list[dict[str, Any]] = []
        grouped = events.groupby(["source", "vehicle_id"], sort=True, observed=True)
        for _, vehicle_events in grouped:
            for segment in self.segmenter.segment(vehicle_events):
                features.append(self._extract_segment(segment))

        if not features:
            return self._empty_output()
        output = pd.DataFrame.from_records(features, columns=OUTPUT_COLUMNS)
        output = output.sort_values(
            ["source", "vehicle_id", "trip_start"], kind="stable"
        ).reset_index(drop=True)
        output["harsh_brake_count"] = output["harsh_brake_count"].astype("Int64")
        output["harsh_accel_count"] = output["harsh_accel_count"].astype("Int64")
        log_event(
            self.logger,
            logging.INFO,
            "trip_features_completed",
            input_records=len(events),
            trips=len(output),
            rejected_records=len(self.last_issues),
        )
        return output

    def _normalize_input(self, records: list[dict]) -> pd.DataFrame:
        self.last_issues = []
        valid: list[dict[str, Any]] = []
        for index, record in enumerate(records):
            try:
                event = (
                    record
                    if isinstance(record, UnifiedGPSEvent)
                    else UnifiedGPSEvent.model_validate(record)
                )
            except ValidationError as exc:
                issue = {"row": index, "errors": exc.errors(include_url=False)}
                self.last_issues.append(issue)
                log_event(
                    self.logger,
                    logging.WARNING,
                    "task2_input_rejected",
                    row=index,
                    errors=issue["errors"],
                )
                if self.settings.task2.input.invalid_record_policy == "error":
                    raise
                continue
            row = event.model_dump(mode="python")
            row["source"] = event.source.value
            valid.append(row)

        if not valid:
            return pd.DataFrame(columns=list(UnifiedGPSEvent.model_fields))
        frame = pd.DataFrame.from_records(valid)
        frame["recorded_at"] = pd.to_datetime(frame["recorded_at"], utc=True).dt.tz_convert(
            self.settings.common.project.timezone
        )
        return frame.sort_values(
            ["source", "vehicle_id", "recorded_at"], kind="stable"
        ).reset_index(drop=True)

    def _extract_segment(self, segment: TripSegment) -> dict[str, Any]:
        events = segment.events.sort_values("recorded_at", kind="stable").reset_index(
            drop=True
        )
        trip_start = events["recorded_at"].iloc[0].to_pydatetime()
        trip_end = events["recorded_at"].iloc[-1].to_pydatetime()
        grade = self._data_grade(events)
        distance_km, distance_method = self._distance(events)
        harsh = detect_harsh_events(
            events,
            self.settings.task2.harsh_events,
            eligible=grade == DataGrade.A,
        )
        speed = pd.to_numeric(events["speed_kmh"], errors="coerce")

        feature = TripFeature(
            trip_id=self._trip_id(
                segment.source.value, segment.vehicle_id, trip_start, trip_end
            ),
            source=segment.source,
            vehicle_id=segment.vehicle_id,
            trip_start=trip_start,
            trip_end=trip_end,
            record_count=len(events),
            segmentation_method=segment.segmentation_method,
            trip_end_reason=segment.end_reason,
            distance_method=distance_method,
            trip_duration_min=segment.duration_minutes,
            distance_km=distance_km,
            avg_speed_kmh=float(speed.mean()),
            max_speed_kmh=float(speed.max()),
            speed_variance=float(speed.var(ddof=0)),
            harsh_brake_count=harsh.harsh_brake_count,
            harsh_accel_count=harsh.harsh_accel_count,
            idle_ratio=self._idle_ratio(events),
            time_of_day_risk=self._time_of_day_risk(trip_start),
            avg_fuel_rate_lh=self._mean_or_none(events["fuel_rate_lh"]),
            avg_rpm=self._mean_or_none(events["engine_rpm"]),
            data_grade=grade,
        )
        row = feature.model_dump(mode="python")
        for key in (
            "source",
            "segmentation_method",
            "trip_end_reason",
            "distance_method",
            "time_of_day_risk",
            "data_grade",
        ):
            row[key] = str(row[key])
        return row

    @staticmethod
    def _trip_id(source: str, vehicle_id: str, start: datetime, end: datetime) -> str:
        raw = f"{source}|{vehicle_id}|{start.isoformat()}|{end.isoformat()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _data_grade(events: pd.DataFrame) -> DataGrade:
        has_can = any(events[column].notna().any() for column in CAN_COLUMNS)
        return DataGrade.A if has_can else DataGrade.B

    @staticmethod
    def _distance(events: pd.DataFrame) -> tuple[float, DistanceMethod]:
        odometer = pd.to_numeric(events["odometer_km"], errors="coerce").dropna()
        if len(odometer) >= 2:
            delta = float(odometer.iloc[-1] - odometer.iloc[0])
            if delta >= 0:
                return delta, DistanceMethod.ODOMETER

        coordinates = events[["lat", "lon"]].dropna().astype(float)
        distance = 0.0
        for index in range(1, len(coordinates)):
            previous = coordinates.iloc[index - 1]
            current = coordinates.iloc[index]
            distance += haversine_km(
                previous["lat"],
                previous["lon"],
                current["lat"],
                current["lon"],
            )
        return distance, DistanceMethod.HAVERSINE

    def _idle_ratio(self, events: pd.DataFrame) -> float | None:
        engine = events["engine_on"]
        if self.settings.task2.idle.require_engine_on and not engine.notna().any():
            return None
        delta_seconds = events["recorded_at"].shift(-1) - events["recorded_at"]
        interval_seconds = delta_seconds.dt.total_seconds().clip(lower=0).fillna(0)
        total_seconds = float(interval_seconds.sum())
        if total_seconds <= 0:
            return 0.0
        speed = pd.to_numeric(events["speed_kmh"], errors="coerce")
        idle = speed <= self.settings.task2.idle.speed_threshold_kmh
        if self.settings.task2.idle.require_engine_on:
            idle &= engine.eq(True)
        idle_seconds = float(interval_seconds.where(idle, 0).sum())
        return idle_seconds / total_seconds

    def _time_of_day_risk(self, timestamp: datetime) -> TimeOfDayRisk:
        value = timestamp.timetz().replace(tzinfo=None)
        rules = self.settings.task2.time_of_day
        if self._in_wrapped_window(value, rules.night_start, rules.night_end):
            return TimeOfDayRisk.NIGHT
        if self._in_window(value, rules.morning_peak_start, rules.morning_peak_end):
            return TimeOfDayRisk.PEAK
        if self._in_window(value, rules.evening_peak_start, rules.evening_peak_end):
            return TimeOfDayRisk.PEAK
        return TimeOfDayRisk.OFFPEAK

    @staticmethod
    def _in_window(value: time, start: time, end: time) -> bool:
        return start <= value < end

    @staticmethod
    def _in_wrapped_window(value: time, start: time, end: time) -> bool:
        if start > end:
            return value >= start or value < end
        return start <= value < end

    @staticmethod
    def _mean_or_none(values: pd.Series) -> float | None:
        numeric = pd.to_numeric(values, errors="coerce").dropna()
        return None if numeric.empty else float(numeric.mean())

    @staticmethod
    def _empty_output() -> pd.DataFrame:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    def write_gold(self, features: pd.DataFrame) -> Path:
        output = self.settings.resolve(self.settings.task2.output.path)
        output.parent.mkdir(parents=True, exist_ok=True)
        frame = features.copy()
        if self.settings.task2.output.write_mode == "append" and output.exists():
            frame = pd.concat([pd.read_parquet(output), frame], ignore_index=True)
        if self.settings.task2.output.deduplicate and not frame.empty:
            frame = frame.drop_duplicates(subset=["trip_id"], keep="last")
            frame = frame.sort_values(
                ["source", "vehicle_id", "trip_start"], kind="stable"
            ).reset_index(drop=True)
        frame.to_parquet(output, index=False)
        log_event(
            self.logger,
            logging.INFO,
            "gold_trip_features_written",
            path=str(output),
            mode=self.settings.task2.output.write_mode,
            rows=len(frame),
        )
        return output
