# Mobility Insight Assignment Submission

## Task 1: Parse and Unify

Install and test from the repository root:

```powershell
uv sync
uv run pytest tests/test_task1_parsers.py
```

Required public API:

```python
from submission.parser import parse_all

records = parse_all(tmt_files, wdmt_file, scgjwd_file)
```

An unavailable source is represented by `[]` for TMT or `""` for WDMT/SCGJWD. The default
`missing_source_policy: warn` processes the remaining sources and records a structured warning.

To parse the sample Bronze files and persist Silver Parquet:

```powershell
uv run python scripts/run_task1.py
```

Persistence is separate from `parse_all`. `overwrite` is the assignment default; `append`
merges with the existing Parquet file and deduplicates exact canonical records. Select the mode
in `config/task1_ingestion.yaml`.

## Task 1 assumptions

- Source timestamps represent Thailand local time and are localized to `Asia/Bangkok` (UTC+7).
- TMT code 33 turns engine state on, code 34 turns it off, and heartbeat code 30 preserves the
  previous state. A heartbeat is never interpreted as engine-off.
- The supplied TMT payloads do not contain a usable source-native vehicle identifier. The
  parser therefore derives a provisional ID from the filename and writes a warning. Production
  ingestion should require an immutable partner identifier.
- WDMT `registration` and SCGJWD `IMEI` are used as source-native vehicle identifiers.
- Missing optional values become `None`; numeric zero remains zero. Records missing required
  fields are rejected individually and logged without stopping other valid records.
- SCGJWD supports the supplied CSV and the XLSX format described in the assignment.
- TMT's dense `Contained data List` is not represented as fresh GPS observations because those
  CAN samples do not carry a new coordinate. It can be preserved separately in
  `data/silver/tmt_can_timeseries` in a later extension.

## Task 2: Trip Segmentation and Feature Engineering

Required public API (in-memory only):

```python
from submission.features import build_trip_features

trip_features = build_trip_features(unified_stream)
```

`build_trip_features` is a thin assignment wrapper. It creates a configured
`TripFeatureExtractor` and delegates to its `transform` method, which is the single feature
implementation reused by notebooks and scripts.

To read Task 1 Silver Parquet and persist the Gold trip-feature table:

```powershell
uv run python scripts/run_task2.py
```

Segmentation thresholds, harsh-event thresholds, time-of-day windows, invalid-record handling,
and Gold `overwrite`/`append` behavior are centralized in `config/task2_features.yaml`. Append
mode deduplicates deterministic `trip_id` values, so rerunning the same input is idempotent.

## Task 2 assumptions

- For streams with engine state, a trip opens on an `engine_on=True` transition and closes on
  `engine_on=False`, a gap greater than 10 minutes, or the end of the stream.
- GPS-only Grade B vehicles cannot provide the required engine transition. When
  `enable_gps_fallback` is true, consecutive GPS observations separated by no more than the gap
  threshold form a candidate trip. The output records `segmentation_method=gps_gap_fallback` so
  this inference remains visible to downstream users.
- Candidate segments shorter than 2 minutes are discarded.
- Distance uses a valid non-negative odometer delta where available; otherwise it uses the sum
  of Haversine distances between GPS points.
- Harsh acceleration is greater than 3.0 m/s² and harsh braking is less than -3.5 m/s². The
  calculation uses actual timestamp intervals no longer than 5 seconds. By default these counts
  are reported only for Grade A trips and remain null for Grade B trips.
- Idle ratio is time-weighted and requires a known engine-on state; it remains null when that
  state is unavailable.
- A trip is Grade A when at least one CAN signal is present; otherwise it is Grade B.

Run Task 1 and Task 2 tests together:

```powershell
uv run pytest tests/test_task1_parsers.py tests/test_task2_features.py
```

The exploratory notebooks call the public wrappers and operate in memory. They do not persist
Silver or Gold datasets.

## Task 3: Driver Risk Model

Task 3 deliberately scores each trip before aggregating evidence per vehicle and grade:

```python
from submission.model import DriverRiskModel

model = DriverRiskModel()
trip_scores = model.score_trips(trip_features)
driver_scores = model.aggregate_drivers(trip_scores)
ranked_contributions = model.feature_contributions_
```

The implemented challenger uses the same API without adding another core file:

```python
challenger = DriverRiskModel(method="robust_zscore")
cohort_relative_scores = challenger.score(trip_features)
```

`weighted_composite` is the primary rule-based method. `robust_zscore` is an experimental,
label-free median/MAD comparison within `(source, data_grade)` and returns a neutral score when
the cohort is too small. An unusual cohort-relative trip is not asserted to be unsafe.

`model.score(trip_features)` is the convenience API that runs both scoring stages and returns
vehicle-level results. It does not persist data. Grade A is labelled `Driver Risk Score`; Grade B
is labelled `Speed Behavior Index`, because sparse GPS cannot support full harsh-event claims.
Aggregation keeps grades separate and applies configurable cold-start shrinkage. The current
entity is `vehicle_id`, which is only a driver proxy until a true driver identifier is supplied.

Weights, thresholds, rationales, reference tags, score version, and prior settings are in
`config/task3_scoring.yaml`. Feature selection is literature-informed; exact numerical weights
are versioned expert priors rather than fitted accident probabilities. See `scoring_design.md`
for the required one-page methodology, trade-off, edge-case, and limitation statement.

Run the complete test suite:

```powershell
uv run pytest
```
