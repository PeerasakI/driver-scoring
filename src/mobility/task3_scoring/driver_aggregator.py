"""Grade-preserving aggregation of trip scores with cold-start shrinkage."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
from pydantic import ValidationError

from mobility.common.config import Task3Settings
from mobility.common.logger import log_event
from mobility.common.schemas import ConfidenceLevel, DriverRisk, TripRisk


DRIVER_SCORE_COLUMNS = list(DriverRisk.model_fields)


class DriverRiskAggregator:
    """Aggregate trip evidence without mixing Grade A and Grade B meanings."""

    def __init__(self, settings: Task3Settings, logger: logging.Logger):
        self.settings = settings
        self.logger = logger

    def aggregate(self, trip_scores: pd.DataFrame) -> pd.DataFrame:
        if not isinstance(trip_scores, pd.DataFrame):
            raise TypeError("trip_scores must be a pandas DataFrame")
        if trip_scores.empty:
            return pd.DataFrame(columns=DRIVER_SCORE_COLUMNS)

        validated = self._validate(trip_scores)
        output: list[dict[str, Any]] = []
        group_keys = [
            "source",
            "vehicle_id",
            "data_grade",
            "score_name",
            "scoring_method",
            "score_version",
        ]
        for key, group in validated.groupby(group_keys, sort=True, observed=True):
            source, vehicle_id, grade, score_name, scoring_method, version = key
            trip_count = len(group)
            observed = float(group["trip_score"].mean())
            cold_start = self.settings.task3.cold_start
            denominator = trip_count + cold_start.prior_strength
            final_score = (
                observed
                if denominator == 0
                else (
                    trip_count * observed
                    + cold_start.prior_strength * cold_start.prior_score
                )
                / denominator
            )
            score_maximum = self.settings.task3.score.maximum
            result = DriverRisk(
                source=source,
                vehicle_id=vehicle_id,
                data_grade=grade,
                score_name=score_name,
                scoring_method=scoring_method,
                observed_score=round(observed, 6),
                final_score=round(final_score, 6),
                risk_points=round(score_maximum - final_score, 6),
                trip_count=trip_count,
                total_duration_min=round(float(group["trip_duration_min"].sum()), 6),
                total_distance_km=round(float(group["distance_km"].sum()), 6),
                evidence_coverage=round(
                    float(group["evidence_coverage"].mean()), 6
                ),
                confidence=self._confidence(trip_count),
                score_version=version,
            ).model_dump(mode="python")
            result["source"] = str(source)
            result["data_grade"] = str(grade)
            result["confidence"] = str(result["confidence"])
            output.append(result)

        frame = pd.DataFrame.from_records(output, columns=DRIVER_SCORE_COLUMNS)
        frame = frame.sort_values(
            ["source", "vehicle_id", "data_grade"], kind="stable"
        ).reset_index(drop=True)
        log_event(
            self.logger,
            logging.INFO,
            "driver_risk_aggregation_completed",
            trip_scores=len(validated),
            driver_grade_profiles=len(frame),
        )
        return frame

    def _validate(self, frame: pd.DataFrame) -> pd.DataFrame:
        valid: list[dict[str, Any]] = []
        for row_number, raw in frame.iterrows():
            clean = {
                key: None if self._is_missing(value) else value
                for key, value in raw.to_dict().items()
            }
            try:
                row = TripRisk.model_validate(clean)
            except ValidationError as exc:
                raise ValueError(f"invalid trip score at row {row_number}: {exc}") from exc
            dumped = row.model_dump(mode="python")
            dumped["source"] = row.source.value
            dumped["data_grade"] = row.data_grade.value
            valid.append(dumped)
        return pd.DataFrame.from_records(valid, columns=list(TripRisk.model_fields))

    def _confidence(self, trip_count: int) -> ConfidenceLevel:
        rules = self.settings.task3.confidence
        if trip_count >= rules.high_trip_count:
            return ConfidenceLevel.HIGH
        if trip_count >= rules.medium_trip_count:
            return ConfidenceLevel.MEDIUM
        return ConfidenceLevel.LOW

    @staticmethod
    def _is_missing(value: Any) -> bool:
        missing = pd.isna(value)
        try:
            return bool(missing)
        except (TypeError, ValueError):
            return False
