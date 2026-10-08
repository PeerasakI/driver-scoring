"""Required Task 2 public API."""

from __future__ import annotations

import pandas as pd

from mobility.task2_features.feature_extractor import TripFeatureExtractor


def build_trip_features(unified_stream: list[dict]) -> pd.DataFrame:
    """Build one validated feature row per valid trip without writing output."""

    return TripFeatureExtractor.from_yaml().transform(unified_stream)
