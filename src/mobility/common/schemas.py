"""Pydantic boundary models and lightweight internal dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DataSource(StrEnum):
    TMT = "TMT"
    WDMT = "WDMT"
    SCGJWD = "SCGJWD"


class DataGrade(StrEnum):
    A = "A"
    B = "B"


class TimeOfDayRisk(StrEnum):
    NIGHT = "night"
    PEAK = "peak"
    OFFPEAK = "offpeak"


class DistanceMethod(StrEnum):
    ODOMETER = "odometer"
    HAVERSINE = "haversine"


class SegmentationMethod(StrEnum):
    ENGINE_AND_GAP = "engine_and_gap"
    GPS_GAP_FALLBACK = "gps_gap_fallback"


class TripEndReason(StrEnum):
    ENGINE_OFF = "engine_off"
    TIMEOUT = "timeout"
    STREAM_END = "stream_end"


class ConfidenceLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class UnifiedGPSEvent(BaseModel):
    """Canonical contract passed from Task 1 to Task 2."""

    model_config = ConfigDict(extra="forbid")

    source: DataSource
    vehicle_id: str = Field(min_length=1)
    recorded_at: datetime
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    speed_kmh: float = Field(ge=0)
    engine_on: bool | None = None
    odometer_km: float | None = Field(default=None, ge=0)
    fuel_rate_lh: float | None = Field(default=None, ge=0)
    engine_rpm: float | None = Field(default=None, ge=0)
    accel_position: float | None = None
    brake_active: bool | None = None

    @field_validator("vehicle_id")
    @classmethod
    def vehicle_id_cannot_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("vehicle_id cannot be blank")
        return cleaned

    @field_validator("recorded_at")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("recorded_at must be timezone-aware")
        return value


class TripFeature(BaseModel):
    """Canonical one-row-per-trip contract passed from Task 2 to Task 3."""

    model_config = ConfigDict(extra="forbid")

    trip_id: str = Field(min_length=1)
    source: DataSource
    vehicle_id: str = Field(min_length=1)
    trip_start: datetime
    trip_end: datetime
    record_count: int = Field(ge=1)
    segmentation_method: SegmentationMethod
    trip_end_reason: TripEndReason
    distance_method: DistanceMethod
    trip_duration_min: float = Field(ge=0)
    distance_km: float = Field(ge=0)
    avg_speed_kmh: float = Field(ge=0)
    max_speed_kmh: float = Field(ge=0)
    speed_variance: float = Field(ge=0)
    harsh_brake_count: int | None = Field(default=None, ge=0)
    harsh_accel_count: int | None = Field(default=None, ge=0)
    idle_ratio: float | None = Field(default=None, ge=0, le=1)
    time_of_day_risk: TimeOfDayRisk
    avg_fuel_rate_lh: float | None = Field(default=None, ge=0)
    avg_rpm: float | None = Field(default=None, ge=0)
    data_grade: DataGrade

    @field_validator("trip_start", "trip_end")
    @classmethod
    def trip_timestamps_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("trip timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def trip_end_must_follow_start(self) -> "TripFeature":
        if self.trip_end < self.trip_start:
            raise ValueError("trip_end cannot be before trip_start")
        return self


class TripRisk(BaseModel):
    """Explainable score for one trip before vehicle-level aggregation."""

    model_config = ConfigDict(extra="forbid")

    trip_id: str = Field(min_length=1)
    source: DataSource
    vehicle_id: str = Field(min_length=1)
    data_grade: DataGrade
    score_name: str = Field(min_length=1)
    scoring_method: str = Field(min_length=1)
    score_status: str = Field(min_length=1)
    trip_start: datetime
    trip_end: datetime
    trip_duration_min: float = Field(ge=0)
    distance_km: float = Field(ge=0)
    trip_score: float = Field(ge=0, le=100)
    risk_points: float = Field(ge=0, le=100)
    evidence_coverage: float = Field(ge=0, le=1)
    top_risk_factor: str = Field(min_length=1)
    score_version: str = Field(min_length=1)

    @field_validator("trip_start", "trip_end")
    @classmethod
    def risk_timestamps_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("trip risk timestamps must be timezone-aware")
        return value


class DriverRisk(BaseModel):
    """Grade-specific vehicle score; vehicle_id is a proxy until driver_id exists."""

    model_config = ConfigDict(extra="forbid")

    source: DataSource
    vehicle_id: str = Field(min_length=1)
    data_grade: DataGrade
    score_name: str = Field(min_length=1)
    scoring_method: str = Field(min_length=1)
    observed_score: float = Field(ge=0, le=100)
    final_score: float = Field(ge=0, le=100)
    risk_points: float = Field(ge=0, le=100)
    trip_count: int = Field(ge=1)
    total_duration_min: float = Field(ge=0)
    total_distance_km: float = Field(ge=0)
    evidence_coverage: float = Field(ge=0, le=1)
    confidence: ConfidenceLevel
    score_version: str = Field(min_length=1)


@dataclass(slots=True)
class ParsedRecord:
    """Internal record carrying state-machine metadata not exposed publicly."""

    event: UnifiedGPSEvent
    source_path: Path
    source_sequence: int
    event_code: str | None = None


@dataclass(slots=True)
class ParseIssue:
    source: DataSource
    source_path: Path | None
    reason: str
    row: int | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ParseResult:
    records: list[ParsedRecord] = field(default_factory=list)
    issues: list[ParseIssue] = field(default_factory=list)

    def extend(self, other: "ParseResult") -> None:
        self.records.extend(other.records)
        self.issues.extend(other.issues)
