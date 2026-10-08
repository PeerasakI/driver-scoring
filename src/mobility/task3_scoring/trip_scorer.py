"""Literature-informed, grade-aware risk scoring for individual trips."""

from __future__ import annotations

import logging
import math
from typing import Any

import pandas as pd
from pydantic import ValidationError

from mobility.common.config import GradeScoreConfig, Task3Settings
from mobility.common.logger import log_event
from mobility.common.schemas import DataGrade, TimeOfDayRisk, TripFeature, TripRisk


TRIP_SCORE_COLUMNS = list(TripRisk.model_fields)
CONTRIBUTION_COLUMNS = [
    "trip_id",
    "source",
    "vehicle_id",
    "data_grade",
    "score_name",
    "scoring_method",
    "feature",
    "observed_value",
    "configured_weight",
    "effective_weight",
    "normalized_risk",
    "penalty_points",
    "available",
    "rank",
    "basis",
    "rationale",
    "references",
]
RANKED_CONTRIBUTION_COLUMNS = [
    "data_grade",
    "score_name",
    "scoring_method",
    "rank",
    "feature",
    "configured_weight",
    "mean_normalized_risk",
    "mean_penalty_points",
    "trips_scored",
    "basis",
    "rationale",
    "references",
]


class TripRiskScorer:
    """Score each valid trip and retain an auditable contribution ledger."""

    name = "weighted_composite"
    family = "rule_based"

    def __init__(self, settings: Task3Settings, logger: logging.Logger):
        self.settings = settings
        self.logger = logger
        self.last_issues: list[dict[str, Any]] = []

    def score(self, trip_features: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        if not isinstance(trip_features, pd.DataFrame):
            raise TypeError("trip_features must be a pandas DataFrame")

        scores: list[dict[str, Any]] = []
        contributions: list[dict[str, Any]] = []
        for trip in self._validated_trips(trip_features):
            try:
                score, ledger = self._score_trip(trip)
            except ValueError as exc:
                issue = {"trip_id": trip.trip_id, "error": str(exc)}
                self.last_issues.append(issue)
                log_event(
                    self.logger,
                    logging.WARNING,
                    "task3_trip_rejected",
                    **issue,
                )
                if self.settings.task3.input.invalid_record_policy == "error":
                    raise
                continue
            scores.append(score)
            contributions.extend(ledger)

        return self._build_frames(scores, contributions, len(trip_features))

    def ranked_feature_contributions(
        self, contributions: pd.DataFrame
    ) -> pd.DataFrame:
        """Aggregate actual penalty contributions and rank them within each grade."""

        if contributions.empty:
            return pd.DataFrame(columns=RANKED_CONTRIBUTION_COLUMNS)
        available = contributions.loc[contributions["available"]].copy()
        if available.empty:
            return pd.DataFrame(columns=RANKED_CONTRIBUTION_COLUMNS)

        group_columns = [
            "data_grade",
            "score_name",
            "scoring_method",
            "feature",
            "configured_weight",
            "basis",
            "rationale",
            "references",
        ]
        ranked = (
            available.groupby(group_columns, as_index=False, dropna=False)
            .agg(
                mean_normalized_risk=("normalized_risk", "mean"),
                mean_penalty_points=("penalty_points", "mean"),
                trips_scored=("trip_id", "nunique"),
            )
            .sort_values(
                ["data_grade", "mean_penalty_points", "configured_weight"],
                ascending=[True, False, False],
                kind="stable",
            )
            .reset_index(drop=True)
        )
        ranked["rank"] = ranked.groupby("data_grade", sort=False).cumcount() + 1
        return ranked[RANKED_CONTRIBUTION_COLUMNS]

    def _score_trip(
        self, trip: TripFeature
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        grade_config = self._grade_config(trip.data_grade)
        observed = self._observed_features(trip)
        available_weight = sum(
            component.weight
            for feature, component in grade_config.components.items()
            if observed.get(feature) is not None
        )
        if available_weight <= 0:
            raise ValueError("no scoreable features are available for this trip")

        score_range = self.settings.task3.score
        score_width = score_range.maximum - score_range.minimum
        ledger: list[dict[str, Any]] = []
        for feature, component in grade_config.components.items():
            value = observed.get(feature)
            available = value is not None
            effective_weight = component.weight / available_weight if available else None
            normalized = self._normalize(value, component.safe, component.severe)
            penalty = (
                normalized * effective_weight * score_width if available else None
            )
            ledger.append(
                {
                    "trip_id": trip.trip_id,
                    "source": trip.source.value,
                    "vehicle_id": trip.vehicle_id,
                    "data_grade": trip.data_grade.value,
                    "score_name": grade_config.label,
                    "scoring_method": self.name,
                    "feature": feature,
                    "observed_value": value,
                    "configured_weight": component.weight,
                    "effective_weight": effective_weight,
                    "normalized_risk": normalized,
                    "penalty_points": penalty,
                    "available": available,
                    "rank": None,
                    "basis": component.basis,
                    "rationale": component.rationale,
                    "references": ", ".join(component.references),
                }
            )

        available_rows = [row for row in ledger if row["available"]]
        ranked_rows = sorted(
            available_rows,
            key=lambda row: (row["penalty_points"], row["configured_weight"]),
            reverse=True,
        )
        for rank, row in enumerate(ranked_rows, start=1):
            row["rank"] = rank

        total_penalty = sum(float(row["penalty_points"]) for row in available_rows)
        trip_score = self._clip(
            score_range.maximum - total_penalty,
            score_range.minimum,
            score_range.maximum,
        )
        risk_points = score_range.maximum - trip_score
        top_row = ranked_rows[0]
        top_factor = (
            str(top_row["feature"])
            if float(top_row["penalty_points"]) > 0
            else "none"
        )
        result = TripRisk(
            trip_id=trip.trip_id,
            source=trip.source,
            vehicle_id=trip.vehicle_id,
            data_grade=trip.data_grade,
            score_name=grade_config.label,
            scoring_method=self.name,
            score_status="scored",
            trip_start=trip.trip_start,
            trip_end=trip.trip_end,
            trip_duration_min=trip.trip_duration_min,
            distance_km=trip.distance_km,
            trip_score=round(trip_score, 6),
            risk_points=round(risk_points, 6),
            evidence_coverage=round(available_weight, 6),
            top_risk_factor=top_factor,
            score_version=score_range.version,
        ).model_dump(mode="python")
        result["source"] = trip.source.value
        result["data_grade"] = trip.data_grade.value
        return result, ledger

    def _validated_trips(self, trip_features: pd.DataFrame) -> list[TripFeature]:
        self.last_issues = []
        valid: list[TripFeature] = []
        for row_number, raw in trip_features.iterrows():
            try:
                clean = {
                    key: None if self._is_missing(value) else value
                    for key, value in raw.to_dict().items()
                }
                valid.append(TripFeature.model_validate(clean))
            except ValidationError as exc:
                issue = {"row": int(row_number), "error": str(exc)}
                self.last_issues.append(issue)
                log_event(
                    self.logger,
                    logging.WARNING,
                    "task3_trip_rejected",
                    **issue,
                )
                if self.settings.task3.input.invalid_record_policy == "error":
                    raise
        return valid

    def _build_frames(
        self,
        scores: list[dict[str, Any]],
        contributions: list[dict[str, Any]],
        input_count: int,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        if not scores:
            return self._empty_scores(), self._empty_contributions()
        score_frame = pd.DataFrame.from_records(scores, columns=TRIP_SCORE_COLUMNS)
        score_frame = score_frame.sort_values(
            ["source", "vehicle_id", "trip_start", "trip_id"], kind="stable"
        ).reset_index(drop=True)
        contribution_frame = pd.DataFrame.from_records(
            contributions, columns=CONTRIBUTION_COLUMNS
        )
        contribution_frame["rank"] = contribution_frame["rank"].astype("Int64")
        log_event(
            self.logger,
            logging.INFO,
            "trip_risk_scoring_completed",
            scoring_method=self.name,
            input_trips=input_count,
            scored_trips=len(score_frame),
            rejected_trips=len(self.last_issues),
        )
        return score_frame, contribution_frame

    def _grade_config(self, grade: DataGrade) -> GradeScoreConfig:
        if grade == DataGrade.A:
            return self.settings.task3.grade_a
        return self.settings.task3.grade_b

    def _observed_features(self, trip: TripFeature) -> dict[str, float | None]:
        exposure_hours = max(
            trip.trip_duration_min / 60.0,
            self.settings.task3.rate_normalization.minimum_exposure_hours,
        )
        return {
            "max_speed_kmh": trip.max_speed_kmh,
            "avg_speed_kmh": trip.avg_speed_kmh,
            "harsh_brake_rate_per_hour": (
                None
                if trip.harsh_brake_count is None
                else trip.harsh_brake_count / exposure_hours
            ),
            "harsh_accel_rate_per_hour": (
                None
                if trip.harsh_accel_count is None
                else trip.harsh_accel_count / exposure_hours
            ),
            "speed_std_kmh": math.sqrt(trip.speed_variance),
            "night_exposure": (
                1.0 if trip.time_of_day_risk == TimeOfDayRisk.NIGHT else 0.0
            ),
        }

    @staticmethod
    def _normalize(value: float | None, safe: float, severe: float) -> float | None:
        if value is None:
            return None
        return TripRiskScorer._clip((value - safe) / (severe - safe), 0.0, 1.0)

    @staticmethod
    def _clip(value: float, minimum: float, maximum: float) -> float:
        return max(minimum, min(maximum, float(value)))

    @staticmethod
    def _is_missing(value: Any) -> bool:
        missing = pd.isna(value)
        try:
            return bool(missing)
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _empty_scores() -> pd.DataFrame:
        return pd.DataFrame(columns=TRIP_SCORE_COLUMNS)

    @staticmethod
    def _empty_contributions() -> pd.DataFrame:
        return pd.DataFrame(columns=CONTRIBUTION_COLUMNS)


class RobustZScoreScorer(TripRiskScorer):
    """Experimental label-free scorer relative to a source-and-grade cohort."""

    name = "robust_zscore"
    family = "unsupervised"

    def score(self, trip_features: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        if not isinstance(trip_features, pd.DataFrame):
            raise TypeError("trip_features must be a pandas DataFrame")

        prepared: list[dict[str, Any]] = []
        for trip in self._validated_trips(trip_features):
            prepared.append({"trip": trip, **self._observed_features(trip)})
        if not prepared:
            return self._empty_scores(), self._empty_contributions()

        frame = pd.DataFrame(prepared)
        frame["source"] = frame["trip"].map(lambda trip: trip.source.value)
        frame["data_grade"] = frame["trip"].map(lambda trip: trip.data_grade.value)
        rules = self.settings.task3.robust_zscore
        scores: list[dict[str, Any]] = []
        contributions: list[dict[str, Any]] = []
        for _, cohort in frame.groupby(rules.cohort_columns, sort=True, observed=True):
            if len(cohort) < rules.minimum_cohort_size:
                scores.extend(self._neutral_score(trip) for trip in cohort["trip"])
                continue
            for _, row in cohort.iterrows():
                score, ledger = self._score_against_cohort(row["trip"], row, cohort)
                scores.append(score)
                contributions.extend(ledger)
        return self._build_frames(scores, contributions, len(trip_features))

    def _score_against_cohort(
        self, trip: TripFeature, row: pd.Series, cohort: pd.DataFrame
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        grade_config = self._grade_config(trip.data_grade)
        available_weight = sum(
            component.weight
            for feature, component in grade_config.components.items()
            if feature in cohort and pd.notna(row.get(feature))
        )
        if available_weight <= 0:
            return self._neutral_score(trip), []

        score_range = self.settings.task3.score
        rules = self.settings.task3.robust_zscore
        score_width = score_range.maximum - score_range.minimum
        ledger: list[dict[str, Any]] = []
        for feature, component in grade_config.components.items():
            value = row.get(feature)
            if self._is_missing(value):
                continue
            values = pd.to_numeric(cohort[feature], errors="coerce").dropna()
            median = float(values.median())
            mad = float((values - median).abs().median())
            scale = 1.4826 * mad
            if scale <= 0:
                scale = float(values.std(ddof=0))
            positive_z = 0.0 if scale <= 0 else max(0.0, (float(value) - median) / scale)
            normalized = self._clip(positive_z / rules.clipping_limit, 0.0, 1.0)
            effective_weight = component.weight / available_weight
            penalty = normalized * effective_weight * score_width
            ledger.append(
                {
                    "trip_id": trip.trip_id,
                    "source": trip.source.value,
                    "vehicle_id": trip.vehicle_id,
                    "data_grade": trip.data_grade.value,
                    "score_name": grade_config.label,
                    "scoring_method": self.name,
                    "feature": feature,
                    "observed_value": float(value),
                    "configured_weight": component.weight,
                    "effective_weight": effective_weight,
                    "normalized_risk": normalized,
                    "penalty_points": penalty,
                    "available": True,
                    "rank": None,
                    "basis": f"cohort_relative::{component.basis}",
                    "rationale": component.rationale,
                    "references": ", ".join(component.references),
                }
            )

        ranked = sorted(
            ledger,
            key=lambda item: (item["penalty_points"], item["configured_weight"]),
            reverse=True,
        )
        for rank, item in enumerate(ranked, start=1):
            item["rank"] = rank
        total_penalty = sum(float(item["penalty_points"]) for item in ranked)
        trip_score = self._clip(
            score_range.maximum - total_penalty,
            score_range.minimum,
            score_range.maximum,
        )
        top_factor = (
            str(ranked[0]["feature"])
            if ranked and float(ranked[0]["penalty_points"]) > 0
            else "none"
        )
        result = TripRisk(
            trip_id=trip.trip_id,
            source=trip.source,
            vehicle_id=trip.vehicle_id,
            data_grade=trip.data_grade,
            score_name=grade_config.label,
            scoring_method=self.name,
            score_status="experimental",
            trip_start=trip.trip_start,
            trip_end=trip.trip_end,
            trip_duration_min=trip.trip_duration_min,
            distance_km=trip.distance_km,
            trip_score=round(trip_score, 6),
            risk_points=round(score_range.maximum - trip_score, 6),
            evidence_coverage=round(available_weight, 6),
            top_risk_factor=top_factor,
            score_version=rules.version,
        ).model_dump(mode="python")
        result["source"] = trip.source.value
        result["data_grade"] = trip.data_grade.value
        return result, ledger

    def _neutral_score(self, trip: TripFeature) -> dict[str, Any]:
        rules = self.settings.task3.robust_zscore
        score_maximum = self.settings.task3.score.maximum
        grade_config = self._grade_config(trip.data_grade)
        result = TripRisk(
            trip_id=trip.trip_id,
            source=trip.source,
            vehicle_id=trip.vehicle_id,
            data_grade=trip.data_grade,
            score_name=grade_config.label,
            scoring_method=self.name,
            score_status="insufficient_cohort",
            trip_start=trip.trip_start,
            trip_end=trip.trip_end,
            trip_duration_min=trip.trip_duration_min,
            distance_km=trip.distance_km,
            trip_score=rules.neutral_prior,
            risk_points=score_maximum - rules.neutral_prior,
            evidence_coverage=0.0,
            top_risk_factor="insufficient_cohort",
            score_version=rules.version,
        ).model_dump(mode="python")
        result["source"] = trip.source.value
        result["data_grade"] = trip.data_grade.value
        return result
