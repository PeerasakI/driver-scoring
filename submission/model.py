"""Required Task 3 public API with trip-first, grade-aware risk scoring."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from mobility.common.config import load_task3_settings
from mobility.common.logger import get_logger
from mobility.task3_scoring.driver_aggregator import (
    DRIVER_SCORE_COLUMNS,
    DriverRiskAggregator,
)
from mobility.task3_scoring.trip_scorer import (
    CONTRIBUTION_COLUMNS,
    RANKED_CONTRIBUTION_COLUMNS,
    TRIP_SCORE_COLUMNS,
    RobustZScoreScorer,
    TripRiskScorer,
)


SCORING_METHODS = {
    "weighted_composite": {
        "family": "rule_based",
        "description": "Configured, explainable deductions from 100",
        "status": "primary",
    },
    "robust_zscore": {
        "family": "unsupervised",
        "description": "Median/MAD cohort-relative anomaly challenger",
        "status": "experimental",
    },
}


class DriverRiskModel:
    """Score trips, then conservatively aggregate evidence per vehicle and grade."""

    def __init__(
        self,
        config_dir: str | Path | None = None,
        *,
        method: str = "weighted_composite",
    ):
        if method not in SCORING_METHODS:
            available = ", ".join(SCORING_METHODS)
            raise ValueError(f"Unknown scoring method {method!r}; choose {available}")
        self.method = method
        self.settings = load_task3_settings(config_dir)
        self.logger = get_logger(
            self.settings,
            name="mobility.task3",
            log_file=self.settings.task3.logging.file,
        )
        scorer_type = (
            TripRiskScorer if method == "weighted_composite" else RobustZScoreScorer
        )
        self._trip_scorer = scorer_type(self.settings, self.logger)
        self._driver_aggregator = DriverRiskAggregator(self.settings, self.logger)
        self.trip_scores_ = pd.DataFrame(columns=TRIP_SCORE_COLUMNS)
        self.trip_contributions_ = pd.DataFrame(columns=CONTRIBUTION_COLUMNS)
        self.feature_contributions_ = pd.DataFrame(
            columns=RANKED_CONTRIBUTION_COLUMNS
        )
        self.driver_scores_ = pd.DataFrame(columns=DRIVER_SCORE_COLUMNS)

    def score_trips(self, trip_features: pd.DataFrame) -> pd.DataFrame:
        """Return one explainable 0–100 safety score per valid trip."""

        scores, contributions = self._trip_scorer.score(trip_features)
        self.trip_scores_ = scores
        self.trip_contributions_ = contributions
        self.feature_contributions_ = (
            self._trip_scorer.ranked_feature_contributions(contributions)
        )
        return scores.copy()

    def aggregate_drivers(
        self, trip_scores: pd.DataFrame | None = None
    ) -> pd.DataFrame:
        """Aggregate trip scores without mixing Grade A and Grade B semantics."""

        source = self.trip_scores_ if trip_scores is None else trip_scores
        self.driver_scores_ = self._driver_aggregator.aggregate(source)
        return self.driver_scores_.copy()

    def score(self, trip_features: pd.DataFrame) -> pd.DataFrame:
        """Convenience API: score trips first, then return vehicle-level scores."""

        trip_scores = self.score_trips(trip_features)
        return self.aggregate_drivers(trip_scores)


def list_scoring_methods() -> pd.DataFrame:
    """List the two implemented, label-free methods and their intended roles."""

    return pd.DataFrame.from_dict(SCORING_METHODS, orient="index").rename_axis(
        "method"
    ).reset_index()
