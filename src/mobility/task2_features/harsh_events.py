"""Sampling-aware harsh acceleration and braking detection."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from mobility.common.config import HarshEventConfig


@dataclass(frozen=True, slots=True)
class HarshEventSummary:
    harsh_accel_count: int | None
    harsh_brake_count: int | None


def detect_harsh_events(
    trip: pd.DataFrame,
    rules: HarshEventConfig,
    *,
    eligible: bool,
) -> HarshEventSummary:
    """Count threshold crossings using actual positive sample intervals."""

    if rules.grade_a_only and not eligible:
        return HarshEventSummary(None, None)

    ordered = trip.sort_values("recorded_at", kind="stable")
    speed_ms = pd.to_numeric(ordered["speed_kmh"], errors="coerce") / 3.6
    delta_seconds = ordered["recorded_at"].diff().dt.total_seconds()
    acceleration = speed_ms.diff() / delta_seconds
    valid_interval = (delta_seconds > 0) & (
        delta_seconds <= rules.maximum_sample_interval_seconds
    )
    acceleration = acceleration.where(valid_interval)
    return HarshEventSummary(
        harsh_accel_count=int(
            (acceleration >= rules.acceleration_threshold_mps2).sum()
        ),
        harsh_brake_count=int(
            (acceleration <= rules.braking_threshold_mps2).sum()
        ),
    )
