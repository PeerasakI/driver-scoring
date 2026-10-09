# Task 4 — Production System Design

The production design separates two workloads with different service-level objectives:

- **Operational path:** near-real-time ingestion, trip-state tracking, data-quality checks, and optional safety alerts.
- **Insurance path:** stable, reproducible trip and vehicle scores refreshed after trip completion or on a scheduled batch. A Driver Score should not fluctuate after every GPS point.

## Architecture

```text
 TMT CAN / 0x51 / 0x52       WDMT JSON          SCGJWD XLSX/CSV
           |                     |                    |
           +---------------------+--------------------+
                                 |
                                 v
                  Bronze immutable raw/object store
                 (encrypted, source/date partitions)
                                 |
                                 v
            Parser + schema validation + sanitization
            engine state + deduplication + H3 indexing
                                 |
                                 v
                   Silver unified event stream
                                 |
                    +------------+------------+
                    |                         |
                    v                         v
          Streaming trip state         Batch/backfill path
          and optional alerts          file ingestion/replay
                    |                         |
                    +------------+------------+
                                 |
                                 v
                    Gold trip feature store
                                 |
                                 v
                       Trip scoring strategy
              weighted_composite | robust_zscore
                                 |
                                 v
             Cold-start-adjusted vehicle/driver score
                                 |
                                 v
                  Versioned score registry/ledger
                                 |
                    +------------+------------+
                    |                         |
                    v                         v
             Driver Score API          Heatmap/zone APIs
                                              |
                                              v
                                  k-anonymity + PDPA policy
```

## 4a. Batch vs Streaming Boundary

### Streaming path

TMT supports a streaming path because CAN data can arrive at approximately one-second intervals, while `0x51` position packets arrive approximately every 60 seconds. The streaming path performs:

- schema validation, normalization, and quarantine of malformed messages;
- idempotent deduplication using source identifiers or a deterministic event key;
- event-time ordering, watermarks, and handling of late/out-of-order messages;
- per-vehicle engine-state tracking across both `0x51` and `0x52` packets;
- maintenance of the currently open trip;
- harsh-event detection when sampling frequency is sufficient; and
- optional operational alerts and provisional trip metrics.

The streaming system should not publish a new insurance score for every event. Point-level events may be late or corrected, and a rapidly changing score would be difficult for an underwriter to interpret.

### Batch or micro-batch path

The following operations are better suited to batch or micro-batch processing:

- WDMT and SCGJWD file ingestion;
- historical replay, backfill, and correction;
- timeout-based trip closure and reconciliation with late events;
- final trip-feature calculation;
- trip-to-driver aggregation and cold-start adjustment;
- data-quality reporting, score monitoring, and model/configuration evaluation; and
- compaction of small Parquet files.

### Refresh latency

| Output | Target latency |
|---|---:|
| TMT operational safety alert | 5–30 seconds |
| Provisional open-trip state | 1–5 minutes |
| Final trip score after trip closure | 5–15 minutes |
| Underwriting vehicle/driver score | Hourly or daily |

TMT can support the low-latency operational path because of its CAN frequency. SCGJWD is delivered as a file and is sampled at roughly 60 seconds, so it should be processed as micro-batch/batch data. A 60-second sample also cannot reliably capture short acceleration spikes; therefore SCGJWD should not claim the same harsh-event sensitivity as high-frequency TMT data. Both sources can still feed a stable daily insurance score.

## 4b. H3 Spatial Resolution

### Resolution by product

| Product | Proposed H3 resolution | Rationale |
|---|---:|---|
| Traffic heatmap API | 8–9 | Provides useful urban aggregation, adequate counts per cell, and lower re-identification risk. |
| Driver trip-path reconstruction | 11 | Better preserves route shape and separates nearby roads, subject to GPS quality. |
| Insurance zone-risk mapping | 7–8 | Produces stable territorial cohorts and supports privacy suppression. |

H3 resolution 10 has an average cell area of approximately `15,048 m²` and average edge length of approximately `75.9 m`. Resolution 11 has an average cell area of approximately `2,150 m²` and average edge length of approximately `28.7 m` ([H3 cell statistics](https://h3geo.org/docs/core-library/restable/)).

Resolution 11 can distinguish nearby roads more often, but it creates sparser groups, exposes more precise location, and is more sensitive to GPS jitter. Resolution 10 is more stable for aggregation but can combine parallel roads, frontage roads, or an elevated road with the road below. For lane-level or complex road-segment identification, H3 alone is insufficient; a road-network map-matching step is required.

### Ingestion-time versus query-time choice

When raw coordinates are permitted, the Bronze layer should retain protected lat/lon, and the Silver layer should calculate a canonical fine-grained index such as H3 resolution 11. Coarser parent cells can then be materialized or derived for individual products. A fine cell can be rolled up to a parent, while detail lost at ingestion cannot be recreated later.

For TMT data that is pre-aggregated before delivery, the supplied H3 resolution becomes part of the data contract. Mobility Insight cannot derive a finer cell than it receives. Each record should therefore carry `h3_index`, `h3_resolution`, and the H3 library/version used to create it.

## 4c. PDPA Constraint

If TMT supplies H3 rather than raw lat/lon, the effect on Task 2 depends on which non-location signals and temporal ordering remain available.

### Features that survive

- `trip_duration_min`, when ordered timestamps remain available;
- `avg_speed_kmh`, `max_speed_kmh`, and `speed_variance`;
- `idle_ratio`, when engine state and speed are retained;
- `time_of_day_risk`;
- `avg_fuel_rate_lh` and `avg_rpm`;
- `data_grade`;
- odometer-based distance; and
- harsh-event counts when high-frequency ordered speeds are shared or the events are calculated by TMT before delivery.

### Features or products that are lost or degraded

- exact haversine distance and exact start/end coordinates;
- precise route reconstruction and road/lane map matching;
- separation of nearby or vertically stacked roads;
- point-level GPS anomaly detection; and
- harsh-event derivation when only coarse cell/time aggregates are supplied without the original sequence.

### k-anonymity ownership

k-anonymity should be enforced at both boundaries as defence in depth:

1. **TMT before sharing:** mandatory because prohibited raw coordinates must never leave the data controller. TMT should generalize spatial/temporal buckets or suppress groups below `k`.
2. **Mobility Insight API output:** mandatory because a seemingly safe input can become identifiable after filters or joins. The API should suppress small groups or roll them up to a parent H3 cell and/or wider time window.

If only one boundary could enforce the rule, TMT's pre-sharing boundary is the non-negotiable one because it prevents the original disclosure. Output-layer enforcement remains necessary for query-based inference protection.

The trade-off is product precision. Stronger spatial/time aggregation improves privacy and cohort stability but reduces route fidelity and causes low-traffic areas to be suppressed. Product responses should expose suppression/generalization metadata rather than silently returning zero.

## 4d. The Engine State Bug

The following logic is incorrect:

```python
engine_on = 1 if event_type == 33 else 0
```

Event code `33` is an **engine-on transition**, not a row-level boolean. A heartbeat or position record following event `33` must inherit the previous ON state. Only the configured engine-off event (`34`) transitions the state to OFF. Treating every other event as OFF creates false trip boundaries, especially because engine transitions can be carried across both TMT packet types.

This bug corrupts at least:

- trip start/end and trip count;
- `trip_duration_min`;
- `distance_km`;
- `idle_ratio`;
- harsh-event counts per trip;
- trip-level average RPM and fuel rate; and
- downstream trip and driver scores.

The correct interpretation is a deterministic finite-state machine (DFA). Events `33` and `34` are transitions; event `30` is a heartbeat that preserves the current state:

```text
OFF --33--> ON
ON  --34--> OFF

OFF --30--> OFF
ON  --30--> ON
```

Repeated transition events are idempotent: `ON --33--> ON` and `OFF --34--> OFF`. Production logic also needs an `UNKNOWN` initial state because the first record may be a heartbeat and there may be no prior evidence of whether the engine is on or off.

| Current state | Event 30 (heartbeat) | Event 33 (engine on) | Event 34 (engine off) |
|---|---|---|---|
| `UNKNOWN` | `UNKNOWN` | `ON` | `OFF` |
| `OFF` | `OFF` | `ON` | `OFF` |
| `ON` | `ON` | `ON` | `OFF` |

```text
                       event 33
              +-------------------------->
              |                           |
          +---+---+                   +---+---+
          |  OFF  |                   |  ON   |
          +---+---+                   +---+---+
              |                           |
              <---------------------------+
                       event 34

          OFF --30/34--> OFF       ON --30/33--> ON

          UNKNOWN --30--> UNKNOWN
          UNKNOWN --33--> ON
          UNKNOWN --34--> OFF
```

Correct state-machine pseudocode:

```text
for each vehicle:
    state = null
    records = sort(all 0x51 and 0x52 records by recorded_at)

    for record in records:
        if record.event_code == 33:
            state = true
        else if record.event_code == 34:
            state = false
        else:
            state = state              # carry the previous known state

        record.engine_on = state
```

An initial record with no preceding transition remains `null`; it must not be guessed as OFF. The implementation must also define ordering for equal timestamps and handle late-arriving transition events through replay/reconciliation.

## 4e. Scale Estimate

Assuming every vehicle emits one `0x51` record per minute for 24 hours:

```text
1,400,000 vehicles × 1,440 minutes/day
= 2,016,000,000 rows/day

= 84,000,000 rows/hour
= approximately 23,333 rows/second on average
```

This is a theoretical upper bound before accounting for vehicles that are parked, offline, or inactive. Capacity planning should also allow for peak bursts, retries, and delayed devices reconnecting simultaneously.

### Storage strategy

A suitable physical layout is:

```text
source=TMT/event_date=YYYY-MM-DD/event_hour=HH/vehicle_bucket=NN/
```

- Partition high-volume CAN/time-series data by event date and hour.
- Hash-bucket or cluster by `vehicle_id`, then sort within files by `vehicle_id, recorded_at`.
- Do not create one partition per vehicle; 1.4 million vehicle partitions would create severe metadata and small-file problems.
- Run compaction to create efficient Parquet files and separate late-event reconciliation from the immutable raw landing area.
- Use event time rather than ingestion time for analytical partitions, while retaining ingestion metadata for audit and replay.

`raw_toyota_can_timeseries` grows faster because it contains periodic telemetry even when no business event occurs. `raw_toyota_b2b_event` is sparse and grows only when a transition or noteworthy event is emitted. Consequently, CAN data requires hourly partitions and aggressive compaction; daily partitions may be sufficient for sparse B2B events. Gold trip features are much smaller and can normally be partitioned by trip date plus source or data grade.

## 4f. Model Lifecycle

### Update policy

The two implemented scoring methods have different lifecycle semantics:

- **`weighted_composite`:** it is rule-based and is not “retrained.” Thresholds, weights, and priors are recalibrated quarterly or when monitoring triggers fire. Changes should be reviewed with domain/underwriting stakeholders and run in shadow mode before promotion.
- **`robust_zscore`:** cohort medians and MAD statistics are refitted on a controlled rolling reference window, for example weekly or monthly. The baseline must remain fixed within a released model version.

If reliable claims, collisions, or verified safety outcomes later become available, a supervised challenger can be trained. Until then, neither current score should be described as a calibrated accident probability.

### Drift detection without labels

Without ground-truth outcomes, monitoring can detect input and score drift, but not predictive accuracy. Monitor:

- PSI, KS, or Wasserstein distance for important feature distributions;
- score distribution by source, grade, vehicle type, region, and time period;
- missingness, validation-failure, and late-event rates;
- changes in Grade A/Grade B and source mix;
- rule firing rates and feature-contribution rankings;
- robust-z-score outlier rates and cohort sizes;
- cold-start prior usage; and
- score/rank churn for otherwise stable drivers.

Alerts should initiate investigation rather than automatic retraining. A change may be caused by seasonality, a new device firmware, a source-schema change, or a genuine behavior shift. When delayed claim outcomes become available, outcome-based calibration and discrimination metrics should be added.

### Reproducibility and versioning

Every issued score should be written to an immutable score ledger with:

- `scoring_method` and `model_version`;
- source-code commit/package version;
- configuration file hash and effective date;
- feature-schema version;
- input table/snapshot version and scoring timestamp;
- H3 version and resolution where relevant;
- cold-start prior and cohort/reference-window version; and
- per-feature contributions and evidence coverage.

The registry should retain immutable code artifacts, YAML configuration, feature definitions, and reference statistics. Reproducing a score six months later then means loading the recorded input snapshot and executing the exact code/configuration/model versions identified by the ledger, rather than using the current production defaults.
