"""Tests for trip-first scoring, grade separation, and cold-start behavior."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from submission.features import build_trip_features
from submission.model import DriverRiskModel, list_scoring_methods
from submission.parser import parse_all


TZ = ZoneInfo("Asia/Bangkok")
ROOT = Path(__file__).resolve().parents[1]
BRONZE = ROOT / "data" / "bronze"


def trip(
    trip_id: str = "trip-1",
    *,
    vehicle_id: str = "CAR-01",
    grade: str = "A",
    duration: float = 60.0,
    distance: float = 40.0,
    avg_speed: float = 40.0,
    max_speed: float = 70.0,
    variance: float = 25.0,
    harsh_brake: int | None = 0,
    harsh_accel: int | None = 0,
    time_risk: str = "offpeak",
) -> dict:
    start = datetime(2026, 3, 1, 10, 0, tzinfo=TZ)
    return {
        "trip_id": trip_id,
        "source": "TMT" if grade == "A" else "WDMT",
        "vehicle_id": vehicle_id,
        "trip_start": start,
        "trip_end": start + timedelta(minutes=duration),
        "record_count": 61,
        "segmentation_method": (
            "engine_and_gap" if grade == "A" else "gps_gap_fallback"
        ),
        "trip_end_reason": "engine_off" if grade == "A" else "stream_end",
        "distance_method": "odometer",
        "trip_duration_min": duration,
        "distance_km": distance,
        "avg_speed_kmh": avg_speed,
        "max_speed_kmh": max_speed,
        "speed_variance": variance,
        "harsh_brake_count": harsh_brake if grade == "A" else None,
        "harsh_accel_count": harsh_accel if grade == "A" else None,
        "idle_ratio": 0.1 if grade == "A" else None,
        "time_of_day_risk": time_risk,
        "avg_fuel_rate_lh": 2.0 if grade == "A" else None,
        "avg_rpm": 1500.0 if grade == "A" else None,
        "data_grade": grade,
    }


def test_safe_grade_a_trip_scores_100() -> None:
    model = DriverRiskModel()
    result = model.score_trips(pd.DataFrame([trip()])).iloc[0]

    assert result["score_name"] == "Driver Risk Score"
    assert result["trip_score"] == pytest.approx(100.0)
    assert result["risk_points"] == pytest.approx(0.0)
    assert result["evidence_coverage"] == pytest.approx(1.0)


def test_severe_grade_a_trip_scores_zero_with_ranked_contributions() -> None:
    model = DriverRiskModel()
    risky = trip(
        avg_speed=100,
        max_speed=130,
        variance=35**2,
        harsh_brake=4,
        harsh_accel=6,
        time_risk="night",
    )

    result = model.score_trips(pd.DataFrame([risky])).iloc[0]

    assert result["trip_score"] == pytest.approx(0.0)
    assert result["top_risk_factor"] == "max_speed_kmh"
    assert model.trip_contributions_["rank"].notna().all()
    assert model.feature_contributions_.iloc[0]["feature"] == "max_speed_kmh"
    assert model.feature_contributions_["rationale"].str.len().gt(0).all()


def test_grade_b_is_honestly_labelled_and_uses_only_speed_components() -> None:
    model = DriverRiskModel()
    grade_b = trip(grade="B", max_speed=105, avg_speed=70, variance=20**2)

    result = model.score_trips(pd.DataFrame([grade_b])).iloc[0]

    assert result["score_name"] == "Speed Behavior Index"
    assert set(model.trip_contributions_["feature"]) == {
        "max_speed_kmh",
        "avg_speed_kmh",
        "speed_std_kmh",
    }
    assert result["evidence_coverage"] == pytest.approx(1.0)


def test_missing_grade_a_components_reduce_coverage_not_become_safe_zeros() -> None:
    model = DriverRiskModel()
    incomplete = trip(
        max_speed=130,
        avg_speed=100,
        variance=35**2,
        harsh_brake=None,
        harsh_accel=None,
        time_risk="night",
    )

    result = model.score_trips(pd.DataFrame([incomplete])).iloc[0]

    assert result["evidence_coverage"] == pytest.approx(0.68)
    assert result["trip_score"] == pytest.approx(0.0)
    missing = model.trip_contributions_.loc[~model.trip_contributions_["available"]]
    assert set(missing["feature"]) == {
        "harsh_brake_rate_per_hour",
        "harsh_accel_rate_per_hour",
    }


def test_one_trip_is_shrunk_to_prior_and_marked_low_confidence() -> None:
    model = DriverRiskModel()
    risky = trip(
        avg_speed=100,
        max_speed=130,
        variance=35**2,
        harsh_brake=4,
        harsh_accel=6,
        time_risk="night",
    )

    result = model.score(pd.DataFrame([risky])).iloc[0]

    expected = (1 * 0 + 5 * 70) / 6
    assert result["observed_score"] == pytest.approx(0.0)
    assert result["final_score"] == pytest.approx(expected)
    assert result["confidence"] == "low"
    assert result["trip_count"] == 1


def test_grade_a_and_b_are_not_aggregated_together() -> None:
    model = DriverRiskModel()
    frame = pd.DataFrame(
        [
            trip("a", vehicle_id="SHARED", grade="A"),
            trip("b", vehicle_id="SHARED", grade="B"),
        ]
    )

    output = model.score(frame)

    assert len(output) == 2
    assert set(output["data_grade"]) == {"A", "B"}
    assert set(output["score_name"]) == {
        "Driver Risk Score",
        "Speed Behavior Index",
    }


def test_null_engine_case_arrives_as_grade_b_gps_fallback() -> None:
    model = DriverRiskModel()
    gps_only = trip(grade="B")

    output = model.score(pd.DataFrame([gps_only])).iloc[0]

    assert output["data_grade"] == "B"
    assert output["score_name"] == "Speed Behavior Index"


def test_invalid_trip_is_skipped_and_empty_input_has_stable_schema() -> None:
    model = DriverRiskModel()
    invalid = pd.DataFrame([trip(), {"trip_id": "bad"}])

    output = model.score_trips(invalid)
    empty = DriverRiskModel().score(pd.DataFrame())

    assert len(output) == 1
    assert len(model._trip_scorer.last_issues) == 1
    assert list(empty.columns) == list(model.driver_scores_.columns)


def test_scoring_does_not_mutate_input() -> None:
    model = DriverRiskModel()
    frame = pd.DataFrame([trip()])
    original = deepcopy(frame)

    model.score(frame)

    pd.testing.assert_frame_equal(frame, original)


def test_config_weights_sum_to_one_and_are_versioned() -> None:
    model = DriverRiskModel()
    task3 = model.settings.task3

    assert sum(value.weight for value in task3.grade_a.components.values()) == 1.0
    assert sum(value.weight for value in task3.grade_b.components.values()) == 1.0
    assert task3.score.version == "rule-v1-literature-informed"


def test_actual_task2_output_can_be_scored_end_to_end() -> None:
    stream = parse_all(
        [str(path) for path in sorted(BRONZE.glob("tm*.json"))],
        str(BRONZE / "wdmt.json"),
        str(BRONZE / "scgjwd.csv"),
    )
    features = build_trip_features(stream)
    model = DriverRiskModel()

    drivers = model.score(features)

    assert len(model.trip_scores_) == len(features) == 5
    assert len(drivers) == 5
    assert set(drivers["score_name"]) == {"Speed Behavior Index"}
    assert set(drivers["confidence"]) == {"low"}


def test_two_scoring_methods_are_implemented_and_selectable() -> None:
    methods = list_scoring_methods().set_index("method")

    assert set(methods.index) == {"weighted_composite", "robust_zscore"}
    assert methods.loc["weighted_composite", "status"] == "primary"
    assert methods.loc["robust_zscore", "status"] == "experimental"
    with pytest.raises(ValueError, match="Unknown scoring method"):
        DriverRiskModel(method="not-a-method")


def test_robust_zscore_returns_neutral_prior_for_small_cohort() -> None:
    model = DriverRiskModel(method="robust_zscore")

    result = model.score_trips(pd.DataFrame([trip(grade="B")])).iloc[0]

    assert result["trip_score"] == pytest.approx(70.0)
    assert result["score_status"] == "insufficient_cohort"
    assert result["top_risk_factor"] == "insufficient_cohort"
    assert result["evidence_coverage"] == pytest.approx(0.0)


def test_robust_zscore_penalizes_positive_cohort_outlier() -> None:
    model = DriverRiskModel(method="robust_zscore")
    cohort = pd.DataFrame(
        [
            trip("b1", vehicle_id="B1", grade="B", max_speed=50),
            trip("b2", vehicle_id="B2", grade="B", max_speed=55),
            trip("b3", vehicle_id="B3", grade="B", max_speed=130),
        ]
    )

    scores = model.score_trips(cohort).set_index("vehicle_id")

    assert scores.loc["B1", "trip_score"] == pytest.approx(100.0)
    assert scores.loc["B3", "trip_score"] < scores.loc["B1", "trip_score"]
    assert scores.loc["B3", "top_risk_factor"] == "max_speed_kmh"
    assert scores.loc["B3", "scoring_method"] == "robust_zscore"


def test_actual_data_runs_both_methods_without_labels() -> None:
    features = pd.read_parquet(
        ROOT / "data" / "gold" / "trip_features" / "trip_features.parquet"
    )
    rule = DriverRiskModel(method="weighted_composite").score(features)
    anomaly_model = DriverRiskModel(method="robust_zscore")
    anomaly = anomaly_model.score(features)

    assert len(rule) == len(anomaly) == 5
    assert set(rule["scoring_method"]) == {"weighted_composite"}
    assert set(anomaly["scoring_method"]) == {"robust_zscore"}
    assert set(anomaly_model.trip_scores_["score_status"]) == {
        "insufficient_cohort",
        "experimental",
    }
