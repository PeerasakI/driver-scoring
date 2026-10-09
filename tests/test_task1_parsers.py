"""Task 1 contract, source parsing, state-machine, and resilience tests."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
import pytest
from mobility.task1_ingestion.pipeline import IngestionPipeline
from submission.parser import parse_all


ROOT = Path(__file__).resolve().parents[1]
BRONZE = ROOT / "data" / "bronze"
EXPECTED_FIELDS = {
    "source",
    "vehicle_id",
    "recorded_at",
    "lat",
    "lon",
    "speed_kmh",
    "engine_on",
    "odometer_km",
    "fuel_rate_lh",
    "engine_rpm",
    "accel_position",
    "brake_active",
}


def test_actual_files_produce_sorted_unified_stream() -> None:
    records = parse_all(
        [str(path) for path in sorted(BRONZE.glob("tm*.json"))],
        str(BRONZE / "wdmt.json"),
        str(BRONZE / "scgjwd.csv"),
    )

    assert records
    assert {str(record["source"]) for record in records} == {
        "TMT",
        "WDMT",
        "SCGJWD",
    }
    assert all(set(record) == EXPECTED_FIELDS for record in records)
    assert all(
        record["recorded_at"].utcoffset() == timedelta(hours=7) for record in records
    )
    assert records == sorted(
        records, key=lambda record: (record["vehicle_id"], record["recorded_at"])
    )


def test_missing_sources_are_skipped_in_warn_mode() -> None:
    records = parse_all([], str(BRONZE / "wdmt.json"), "")
    assert records
    assert {str(record["source"]) for record in records} == {"WDMT"}


def test_tmt_engine_state_uses_on_heartbeat_off_sequence(tmp_path: Path) -> None:
    def gps(timestamp: str) -> dict:
        return {
            "Lat angle": "13.7",
            "Lon angle": "100.6",
            "Timestamp": timestamp,
        }

    on = {
        "B2B event": {
            "Event type": "33",
            "Timestamp": "20260301070000",
            "Vehicle speed": "0",
            "GPS": gps("20260301070000"),
            "CAN List": [],
        }
    }
    heartbeat = {
        "B2B Event List": [
            {
                "Event type": "30",
                "Timestamp": "20260301070100",
                "Vehicle speed": "10",
                "GPS": gps("20260301070100"),
                "CAN List": [],
            }
        ]
    }
    off = {
        "B2B Event List": [
            {
                "Event type": "34",
                "Timestamp": "20260301070200",
                "Vehicle speed": "0",
                "GPS": gps("20260301070200"),
                "CAN List": [],
            }
        ]
    }
    paths = []
    for name, payload in (
        ("vehicle_a_52.json", on),
        ("vehicle_a_51_heartbeat.json", heartbeat),
        ("vehicle_a_51.json", off),
    ):
        path = tmp_path / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths.append(str(path))

    records = parse_all(paths, "", "")
    assert [record["engine_on"] for record in records] == [True, True, False]


def test_numeric_zero_is_not_treated_as_missing(tmp_path: Path) -> None:
    path = tmp_path / "wdmt.json"
    path.write_text(
        json.dumps(
            [
                {
                    "registration": "TEST-001",
                    "local_timestamp": "2026-03-01 07:00:00",
                    "lat": 0.0,
                    "lon": 0.0,
                    "speed": "0.000",
                    "mileage": 0.0,
                }
            ]
        ),
        encoding="utf-8",
    )
    record = parse_all([], str(path), "")[0]
    assert record["lat"] == pytest.approx(0.0)
    assert record["lon"] == pytest.approx(0.0)
    assert record["speed_kmh"] == pytest.approx(0.0)
    assert record["odometer_km"] == pytest.approx(0.0)


def test_invalid_record_is_rejected_without_stopping_valid_rows(tmp_path: Path) -> None:
    path = tmp_path / "wdmt.json"
    rows = [
        {
            "registration": "GOOD-001",
            "local_timestamp": "2026-03-01 07:00:00",
            "lat": 13.7,
            "lon": 100.6,
            "speed": 10,
        },
        {
            "registration": "BAD-001",
            "local_timestamp": "not-a-time",
            "lat": "-",
            "lon": None,
            "speed": 10,
        },
    ]
    path.write_text(json.dumps(rows), encoding="utf-8")
    pipeline = IngestionPipeline.from_yaml()
    records = pipeline.parse_all([], str(path), "")
    assert len(records) == 1
    assert records[0]["vehicle_id"] == "GOOD-001"
    assert len(pipeline.last_issues) == 1


def test_append_mode_is_idempotent_for_exact_reruns(tmp_path: Path) -> None:
    pipeline = IngestionPipeline.from_yaml()
    pipeline.settings.task1.output.path = tmp_path / "events.parquet"
    pipeline.settings.task1.output.write_mode = "append"
    records = parse_all([], str(BRONZE / "wdmt.json"), "")[:3]

    pipeline.write_silver(records)
    output = pipeline.write_silver(records)

    import pandas as pd

    assert len(pd.read_parquet(output)) == 3
