"""Engine-aware trip segmentation with an explicit GPS-only fallback."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from mobility.common.config import SegmentationConfig
from mobility.common.logger import log_event
from mobility.common.schemas import (
    DataSource,
    SegmentationMethod,
    TripEndReason,
)


@dataclass(slots=True)
class TripSegment:
    source: DataSource
    vehicle_id: str
    events: pd.DataFrame
    segmentation_method: SegmentationMethod
    end_reason: TripEndReason

    @property
    def duration_minutes(self) -> float:
        if self.events.empty:
            return 0.0
        elapsed = self.events["recorded_at"].iloc[-1] - self.events["recorded_at"].iloc[0]
        return float(elapsed.total_seconds() / 60.0)


class TripSegmenter:
    def __init__(self, rules: SegmentationConfig, logger: logging.Logger):
        self.rules = rules
        self.logger = logger

    def segment(self, vehicle_events: pd.DataFrame) -> list[TripSegment]:
        if vehicle_events.empty:
            return []
        ordered = vehicle_events.sort_values("recorded_at", kind="stable").reset_index(
            drop=True
        )
        source = DataSource(str(ordered["source"].iloc[0]))
        vehicle_id = str(ordered["vehicle_id"].iloc[0])

        if ordered["engine_on"].notna().any():
            candidates = self._engine_segments(ordered)
            method = SegmentationMethod.ENGINE_AND_GAP
        elif self.rules.enable_gps_fallback:
            candidates = self._gap_only_segments(ordered)
            method = SegmentationMethod.GPS_GAP_FALLBACK
        else:
            log_event(
                self.logger,
                logging.WARNING,
                "trip_stream_skipped",
                source=source.value,
                vehicle_id=vehicle_id,
                reason="engine_state_unavailable_and_gps_fallback_disabled",
            )
            return []

        valid: list[TripSegment] = []
        for events, end_reason in candidates:
            segment = TripSegment(source, vehicle_id, events, method, end_reason)
            if segment.duration_minutes < self.rules.minimum_duration_minutes:
                log_event(
                    self.logger,
                    logging.INFO,
                    "short_trip_discarded",
                    source=source.value,
                    vehicle_id=vehicle_id,
                    duration_minutes=segment.duration_minutes,
                )
                continue
            valid.append(segment)
        return valid

    def _engine_segments(
        self, events: pd.DataFrame
    ) -> list[tuple[pd.DataFrame, TripEndReason]]:
        candidates: list[tuple[pd.DataFrame, TripEndReason]] = []
        active_indices: list[int] = []
        previous_time = None
        gap_limit = pd.Timedelta(minutes=self.rules.gap_minutes)

        for index, row in events.iterrows():
            recorded_at = row["recorded_at"]
            gap_exceeded = (
                bool(active_indices)
                and previous_time is not None
                and recorded_at - previous_time > gap_limit
            )
            if gap_exceeded:
                candidates.append(
                    (events.loc[active_indices].copy(), TripEndReason.TIMEOUT)
                )
                active_indices = []

            raw_state = row["engine_on"]
            engine_state = None if pd.isna(raw_state) else bool(raw_state)

            if not active_indices:
                if engine_state is True:
                    active_indices = [index]
            else:
                active_indices.append(index)
                if engine_state is False:
                    candidates.append(
                        (events.loc[active_indices].copy(), TripEndReason.ENGINE_OFF)
                    )
                    active_indices = []
            previous_time = recorded_at

        if active_indices and self.rules.close_open_trip_at_stream_end:
            candidates.append(
                (events.loc[active_indices].copy(), TripEndReason.STREAM_END)
            )
        return candidates

    def _gap_only_segments(
        self, events: pd.DataFrame
    ) -> list[tuple[pd.DataFrame, TripEndReason]]:
        candidates: list[tuple[pd.DataFrame, TripEndReason]] = []
        gap_limit = pd.Timedelta(minutes=self.rules.gap_minutes)
        start = 0
        gaps = events["recorded_at"].diff()

        for index in range(1, len(events)):
            if gaps.iloc[index] > gap_limit:
                candidates.append(
                    (events.iloc[start:index].copy(), TripEndReason.TIMEOUT)
                )
                start = index
        if self.rules.close_open_trip_at_stream_end:
            candidates.append((events.iloc[start:].copy(), TripEndReason.STREAM_END))
        return candidates
