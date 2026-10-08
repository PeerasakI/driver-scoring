"""Typed YAML configuration shared by all tasks."""

from __future__ import annotations

from datetime import time
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProjectConfig(BaseModel):
    timezone: str = "Asia/Bangkok"
    environment: str = "development"


class PathsConfig(BaseModel):
    bronze: Path = Path("data/bronze")
    silver: Path = Path("data/silver")
    gold: Path = Path("data/gold")
    quarantine: Path = Path("data/quarantine")
    logs: Path = Path("logs")


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: Literal["jsonl", "text"] = "jsonl"
    file: Path = Path("logs/task1_ingestion.jsonl")


class CommonConfig(BaseModel):
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


class IngestionPolicy(BaseModel):
    missing_source_policy: Literal["warn", "error"] = "warn"
    invalid_record_policy: Literal["skip", "error"] = "skip"


class TMTEventCodes(BaseModel):
    engine_on: str = "33"
    heartbeat: str = "30"
    engine_off: str = "34"


class TMTConfig(BaseModel):
    event_codes: TMTEventCodes = Field(default_factory=TMTEventCodes)
    vehicle_id_fallback: Literal["filename", "error"] = "filename"


class Task1OutputConfig(BaseModel):
    path: Path = Path("data/silver/unified_events/unified_events.parquet")
    format: Literal["parquet"] = "parquet"
    write_mode: Literal["overwrite", "append"] = "overwrite"
    deduplicate: bool = True


class Task1FileConfig(BaseModel):
    ingestion: IngestionPolicy = Field(default_factory=IngestionPolicy)
    tmt: TMTConfig = Field(default_factory=TMTConfig)
    output: Task1OutputConfig = Field(default_factory=Task1OutputConfig)


class Task1Settings(BaseModel):
    """Resolved Task 1 settings plus the project root used for relative paths."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    root: Path
    common: CommonConfig
    task1: Task1FileConfig

    def resolve(self, path: Path) -> Path:
        return path if path.is_absolute() else self.root / path


class Task2InputConfig(BaseModel):
    invalid_record_policy: Literal["skip", "error"] = "skip"


class SegmentationConfig(BaseModel):
    gap_minutes: float = Field(default=10.0, gt=0)
    minimum_duration_minutes: float = Field(default=2.0, ge=0)
    enable_gps_fallback: bool = True
    close_open_trip_at_stream_end: bool = True


class HarshEventConfig(BaseModel):
    acceleration_threshold_mps2: float = Field(default=3.0, gt=0)
    braking_threshold_mps2: float = Field(default=-3.5, lt=0)
    maximum_sample_interval_seconds: float = Field(default=5.0, gt=0)
    grade_a_only: bool = True


class IdleConfig(BaseModel):
    speed_threshold_kmh: float = Field(default=0.0, ge=0)
    require_engine_on: bool = True


class TimeOfDayConfig(BaseModel):
    night_start: time = time(22, 0)
    night_end: time = time(5, 0)
    morning_peak_start: time = time(7, 0)
    morning_peak_end: time = time(9, 0)
    evening_peak_start: time = time(17, 0)
    evening_peak_end: time = time(19, 0)


class Task2OutputConfig(BaseModel):
    path: Path = Path("data/gold/trip_features/trip_features.parquet")
    format: Literal["parquet"] = "parquet"
    write_mode: Literal["overwrite", "append"] = "overwrite"
    deduplicate: bool = True


class Task2LoggingConfig(BaseModel):
    file: Path = Path("logs/task2_features.jsonl")


class Task2FileConfig(BaseModel):
    input: Task2InputConfig = Field(default_factory=Task2InputConfig)
    segmentation: SegmentationConfig = Field(default_factory=SegmentationConfig)
    harsh_events: HarshEventConfig = Field(default_factory=HarshEventConfig)
    idle: IdleConfig = Field(default_factory=IdleConfig)
    time_of_day: TimeOfDayConfig = Field(default_factory=TimeOfDayConfig)
    output: Task2OutputConfig = Field(default_factory=Task2OutputConfig)
    logging: Task2LoggingConfig = Field(default_factory=Task2LoggingConfig)


class Task2Settings(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    root: Path
    common: CommonConfig
    task2: Task2FileConfig

    def resolve(self, path: Path) -> Path:
        return path if path.is_absolute() else self.root / path


class Task3InputConfig(BaseModel):
    invalid_record_policy: Literal["skip", "error"] = "skip"


class ScoreRangeConfig(BaseModel):
    minimum: float = 0.0
    maximum: float = 100.0
    version: str = Field(default="rule-v1", min_length=1)

    @model_validator(mode="after")
    def maximum_must_exceed_minimum(self) -> "ScoreRangeConfig":
        if self.maximum <= self.minimum:
            raise ValueError("score.maximum must exceed score.minimum")
        return self


class RateNormalizationConfig(BaseModel):
    minimum_exposure_hours: float = Field(default=0.25, gt=0)


class RiskComponentConfig(BaseModel):
    weight: float = Field(gt=0, le=1)
    safe: float
    severe: float
    basis: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    references: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def severe_must_exceed_safe(self) -> "RiskComponentConfig":
        if self.severe <= self.safe:
            raise ValueError("component severe threshold must exceed safe threshold")
        return self


class GradeScoreConfig(BaseModel):
    label: str = Field(min_length=1)
    components: dict[str, RiskComponentConfig]

    @model_validator(mode="after")
    def component_weights_must_sum_to_one(self) -> "GradeScoreConfig":
        total = sum(component.weight for component in self.components.values())
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"component weights must sum to 1.0; got {total}")
        return self


class ColdStartConfig(BaseModel):
    prior_score: float = Field(default=70.0, ge=0, le=100)
    prior_strength: float = Field(default=5.0, ge=0)


class ConfidenceConfig(BaseModel):
    medium_trip_count: int = Field(default=5, ge=1)
    high_trip_count: int = Field(default=20, ge=1)

    @model_validator(mode="after")
    def high_threshold_must_exceed_medium(self) -> "ConfidenceConfig":
        if self.high_trip_count <= self.medium_trip_count:
            raise ValueError("high_trip_count must exceed medium_trip_count")
        return self


class Task3LoggingConfig(BaseModel):
    file: Path = Path("logs/task3_scoring.jsonl")


class RobustZScoreConfig(BaseModel):
    version: str = Field(default="robust-zscore-v1", min_length=1)
    cohort_columns: list[Literal["source", "data_grade"]] = Field(
        default_factory=lambda: ["source", "data_grade"]
    )
    minimum_cohort_size: int = Field(default=3, ge=2)
    clipping_limit: float = Field(default=3.5, gt=0)
    neutral_prior: float = Field(default=70.0, ge=0, le=100)


class Task3FileConfig(BaseModel):
    input: Task3InputConfig = Field(default_factory=Task3InputConfig)
    score: ScoreRangeConfig = Field(default_factory=ScoreRangeConfig)
    rate_normalization: RateNormalizationConfig = Field(
        default_factory=RateNormalizationConfig
    )
    grade_a: GradeScoreConfig
    grade_b: GradeScoreConfig
    cold_start: ColdStartConfig = Field(default_factory=ColdStartConfig)
    confidence: ConfidenceConfig = Field(default_factory=ConfidenceConfig)
    robust_zscore: RobustZScoreConfig = Field(default_factory=RobustZScoreConfig)
    logging: Task3LoggingConfig = Field(default_factory=Task3LoggingConfig)


class Task3Settings(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    root: Path
    common: CommonConfig
    task3: Task3FileConfig

    def resolve(self, path: Path) -> Path:
        return path if path.is_absolute() else self.root / path


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _read_yaml(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as stream:
        value = yaml.safe_load(stream) or {}
    if not isinstance(value, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return value


def load_task1_settings(config_dir: str | Path | None = None) -> Task1Settings:
    root = project_root()
    directory = Path(config_dir) if config_dir else root / "config"
    if not directory.is_absolute():
        directory = root / directory
    return Task1Settings(
        root=root,
        common=CommonConfig.model_validate(_read_yaml(directory / "common.yaml")),
        task1=Task1FileConfig.model_validate(
            _read_yaml(directory / "task1_ingestion.yaml")
        ),
    )


def load_task2_settings(config_dir: str | Path | None = None) -> Task2Settings:
    root = project_root()
    directory = Path(config_dir) if config_dir else root / "config"
    if not directory.is_absolute():
        directory = root / directory
    return Task2Settings(
        root=root,
        common=CommonConfig.model_validate(_read_yaml(directory / "common.yaml")),
        task2=Task2FileConfig.model_validate(
            _read_yaml(directory / "task2_features.yaml")
        ),
    )


def load_task3_settings(config_dir: str | Path | None = None) -> Task3Settings:
    root = project_root()
    directory = Path(config_dir) if config_dir else root / "config"
    if not directory.is_absolute():
        directory = root / directory
    return Task3Settings(
        root=root,
        common=CommonConfig.model_validate(_read_yaml(directory / "common.yaml")),
        task3=Task3FileConfig.model_validate(
            _read_yaml(directory / "task3_scoring.yaml")
        ),
    )
