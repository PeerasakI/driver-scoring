"""Task 2 segmentation, feature calculation, and public-contract tests."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from mobility.common.schemas import TripFeature
from mobility.task2_features.feature_extractor import TripFeatureExtractor
from submission.features import build_trip_features
from submission.parser import parse_all


TZ = ZoneInfo("Asia/Bangkok")
ROOT = Path(__file__).resolve().parents[1]
BRONZE = ROOT / "data" / "bronze"


def event(
    minute: float,
    *,
    source: str = "TMT",
    vehicle_id: str = "CAR-01",
    engine_on: bool | None = True,
    speed: float = 10.0,
    lat: float = 13.7,
    lon: float = 100.6,
    odometer: float | None = None,
    rpm: float | None = None,
    fuel: float | None = None,
) -> dict:
    return {
        "source": source,
        "vehicle_id": vehicle_id,
        "recorded_at": datetime(2026, 3, 1, 7, 0, tzinfo=TZ)
        + timedelta(minutes=minute),
        "lat": lat,
        "lon": lon,
        "speed_kmh": speed,
        "engine_on": engine_on,
        "odometer_km": odometer,
        "fuel_rate_lh": fuel,
        "engine_rpm": rpm,
        "accel_position": None,
        "brake_active": None,
    }


def test_engine_transition_builds_one_trip_and_uses_odometer() -> None:
    stream = [
        event(0, engine_on=False, speed=0, odometer=100.0),
        event(1, engine_on=True, speed=0, odometer=100.0),
        event(2, engine_on=True, speed=30, odometer=101.0),
        event(4, engine_on=False, speed=0, odometer=103.0),
    ]
    features = build_trip_features(stream)
    assert len(features) == 1
    row = features.iloc[0]
    assert row["trip_duration_min"] == pytest.approx(3.0)
    assert row["distance_km"] == pytest.approx(3.0)
    assert row["distance_method"] == "odometer"
    assert row["trip_end_reason"] == "engine_off"


def test_short_trip_is_discarded() -> None:
    stream = [event(0, engine_on=True), event(1.9, engine_on=False)]
    assert build_trip_features(stream).empty


def test_gap_over_ten_minutes_closes_trip_and_can_start_another() -> None:
    stream = [
        event(0, engine_on=True),
        event(3, engine_on=True),
        event(14, engine_on=True),
        event(17, engine_on=False),
    ]
    features = build_trip_features(stream)
    assert len(features) == 2
    assert features["trip_end_reason"].tolist() == ["timeout", "engine_off"]


def test_gps_only_stream_uses_gap_fallback_and_grade_b_null_metrics() -> None:
    stream = [
        event(0, source="WDMT", engine_on=None, speed=0, odometer=10.0),
        event(1, source="WDMT", engine_on=None, speed=20, odometer=10.3),
        event(2, source="WDMT", engine_on=None, speed=10, odometer=10.6),
    ]
    row = build_trip_features(stream).iloc[0]
    assert row["segmentation_method"] == "gps_gap_fallback"
    assert row["data_grade"] == "B"
    assert pd.isna(row["harsh_accel_count"])
    assert pd.isna(row["harsh_brake_count"])
    assert pd.isna(row["idle_ratio"])


def test_haversine_is_used_when_odometer_is_unavailable() -> None:
    stream = [
        event(0, engine_on=True, lat=13.7000, lon=100.6000),
        event(1, engine_on=True, lat=13.7010, lon=100.6000),
        event(2, engine_on=False, lat=13.7020, lon=100.6000),
    ]
    row = build_trip_features(stream).iloc[0]
    assert row["distance_method"] == "haversine"
    assert row["distance_km"] > 0


def test_grade_a_harsh_events_use_actual_one_second_intervals() -> None:
    stream = [
        event(0, engine_on=True, speed=0, rpm=800),
        event(1 / 60, engine_on=True, speed=14.4, rpm=1500),
        event(2 / 60, engine_on=True, speed=0, rpm=900),
        event(2, engine_on=False, speed=0, rpm=0),
    ]
    row = build_trip_features(stream).iloc[0]
    assert row["data_grade"] == "A"
    assert row["harsh_accel_count"] == 1
    assert row["harsh_brake_count"] == 1


def test_idle_ratio_is_time_weighted() -> None:
    stream = [
        event(0, engine_on=True, speed=0),
        event(1, engine_on=True, speed=20),
        event(2, engine_on=False, speed=0),
    ]
    row = build_trip_features(stream).iloc[0]
    assert row["idle_ratio"] == pytest.approx(0.5)


@pytest.mark.parametrize(
    ("hour", "expected"),
    [(4, "night"), (7, "peak"), (9, "offpeak"), (17, "peak"), (22, "night")],
)
def test_time_of_day_boundaries(hour: int, expected: str) -> None:
    stream = [event(0), event(2, engine_on=False)]
    for record in stream:
        record["recorded_at"] = record["recorded_at"].replace(hour=hour)
    assert build_trip_features(stream).iloc[0]["time_of_day_risk"] == expected


def test_empty_input_has_stable_schema() -> None:
    output = build_trip_features([])
    assert output.empty
    assert list(output.columns) == list(TripFeature.model_fields)


def test_transform_does_not_mutate_input_and_actual_data_runs() -> None:
    stream = parse_all(
        [str(path) for path in sorted(BRONZE.glob("tm*.json"))],
        str(BRONZE / "wdmt.json"),
        str(BRONZE / "scgjwd.csv"),
    )
    original = deepcopy(stream)
    output = TripFeatureExtractor.from_yaml().transform(stream)
    assert stream == original
    assert not output.empty
    assert output.equals(
        output.sort_values(
            ["source", "vehicle_id", "trip_start"], kind="stable"
        ).reset_index(drop=True)
    )


def test_invalid_record_is_skipped_and_reported() -> None:
    extractor = TripFeatureExtractor.from_yaml()
    stream = [event(0), {"source": "TMT"}, event(2, engine_on=False)]

    output = extractor.transform(stream)

    assert len(output) == 1
    assert len(extractor.last_issues) == 1
    assert extractor.last_issues[0]["row"] == 1


def test_append_write_is_idempotent_by_trip_id(tmp_path: Path) -> None:
    extractor = TripFeatureExtractor.from_yaml()
    extractor.settings.task2.output.path = tmp_path / "trip_features.parquet"
    extractor.settings.task2.output.write_mode = "append"
    features = extractor.transform([event(0), event(2, engine_on=False)])

    output = extractor.write_gold(features)
    extractor.write_gold(features)

    persisted = pd.read_parquet(output)
    assert len(persisted) == 1
    assert persisted.iloc[0]["trip_id"] == features.iloc[0]["trip_id"]
