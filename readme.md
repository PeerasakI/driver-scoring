# Mobility Insight — ML Engineer Take-Home Assignment

### Driver Intelligence Pipeline: From Raw Telematics to Scoreable Features

**Confidential — Internal Use Only**
**Estimated effort: 4–6 hours**

> We do not expect all edge cases to be handled. A partial solution with clear
> reasoning outscores a complete solution with no rationale. Depth over completeness.

---

## 1. Context

Mobility Insight is building Thailand's first Mobility Intelligence Platform — a B2B
Data-as-a-Service business transforming raw vehicle telematics into commercial AI products.

Your primary deliverable this quarter is the **Driver Score API**: a per-vehicle risk
score (0–100) consumed by insurance underwriters and fleet operators.

You are joining as the ML engineer responsible for the full pipeline — from raw source
data to scoreable features to model output. This assignment reflects the actual problems
you will own on Day 1.

---

## 2. Data Sources

You are provided with data from **three real partners**. Each has a different format,
frequency, and signal richness. Part of your job is to understand what each source
can and cannot support — and to be honest about those limits.

---

### Source A — Toyota / TCAP (TMT)

Two JSON packet types emitted per vehicle session.

**`0x52` — Engine ON event** (top-level key: `"B2B event"`, singular)

```json
{
  "B2B event": {
    "Event type": "33",
    "Vehicle speed": "0",
    "Timestamp": "20250402005730",
    "GPS": {
      "Lat angle": "14.482778",
      "Lon angle": "100.131122",
      "Caputure": "0",
      "direction": "0",
      "Timestamp": "20250402005733"
    },
    "CAN List": [
      { "CAN": { "Engine Speed": "803.75" },          "Timestamp": "20250402005726" },
      { "CAN": { "Accel Position": "0" },             "Timestamp": "20250402005727" },
      { "CAN": { "Stop Light Switch": "0" },          "Timestamp": "20250402005727" },
      { "CAN": { "Fuel Input": "59" },                "Timestamp": "20250402005633" },
      { "CAN": { "Total Distance Traveled": "233653" },"Timestamp": "20250402005633" },
      { "WNG": { "+B Voltage Value": "13.9" },        "Timestamp": "20250402005638" }
    ],
    "Engine RPM by direct line": "0",
    "Licence card": {}
  },
  "Number of unsent message": "0",
  "Common header": {
    "GPS": { "Lat angle": "14.482778", "Lon angle": "100.131122", "Timestamp": "20250402005733" },
    "Major ver.": "1",
    "Minor ver.": "0",
    "Data size": "92",
    "Destination": "0"
  }
}
```

**`0x51` — Periodic heartbeat / Engine OFF** (top-level key: `"B2B Event List"`, array)

```json
{
  "B2B Event List": [
    {
      "Event type": "30",
      "Vehicle speed": "0",
      "Timestamp": "20250402005829",
      "GPS": { "Lat angle": "14.482773", "Lon angle": "100.131119", ... },
      "CAN List": [
        { "CAN": { "Vehicle Fuel Rate": "0.16" }, "Timestamp": "20250402005828" },
        { "CAN": { "Engine Speed": "797.5" },     "Timestamp": "20250402005826" },
        { "CAN": { "Accel Position": "0" },       "Timestamp": "20250402005827" },
        { "CAN": { "Stop Light Switch": "0" },    "Timestamp": "20250402005827" },
        { "CAN": { "Total Distance Traveled": "233653" }, "Timestamp": "20250402005733" },
        { "WNG": { "+B Voltage Value": "13.9" },  "Timestamp": "20250402005638" }
      ],
      "Engine RPM by direct line": "0"
    }
  ],
  "Contained data List": [
    {
      "CAN List": [
        { "CAN": { "Vehicle Fuel Rate": "0.22" }, "Timestamp": "20250402005730" },
        { "CAN": { "Engine Speed": "796" },        "Timestamp": "20250402005731" }
      ]
    }
    // ... ~60 per-second entries covering the minute before the event
  ],
  "Vehicle speed List":  [ { "Vehicle speed": "0" }, ... ],
  "Engine RPM List":     [ { "Engine RPM": "0" }, ... ],
  "Number of B2B event": "1",
  "Number of unsent message": "0",
  "RFID List": [],
  "Licence card": {}
}
```

**Known event codes:**

| Code | Meaning       | Source packet |
|------|---------------|---------------|
| `33` | Engine ON     | `0x52` only   |
| `30` | Heartbeat     | `0x51` only   |
| `34` | Engine OFF    | `0x51` only   |

> **Critical:** Engine ON events (`33`) exist exclusively in `0x52` files.
> Engine OFF (`34`) and heartbeat (`30`) exist exclusively in `0x51` files.
> These are structurally asymmetric — your parser must handle them differently.

---

### Source B — WDMT / True Leasing (Wonder Matics)

Interval-based polling JSON array. Ping interval is **fixed per vehicle but varies
across the fleet: 15–60 seconds** depending on the vehicle.

```json
[
  {
    "registration":   "4ขฌ-1160",
    "event_th":       "สถานะปกติ",
    "event_en":       "LOCATION",
    "location_th":    "อาคารอื้อจือเหลียง ถนนพระรามที่ 4 สีลม,บางรัก,กรุงเทพมหานคร",
    "location_en":    "U CHU LIANG BUILDING RAMA 4 RD. SI LOM,BANG RAK,BANGKOK",
    "local_timestamp":"2026-03-01 12:43:00",
    "lat":            11.42555,
    "lon":            99.53472,
    "speed":          75,
    "course":         76,
    "type_of_fix":    2,
    "no_of_satellite":10,
    "internal_power": 0,
    "external_power": 1360,
    "mileage":        115585.971,
    "Temp1": "-", "Temp2": "-", "Temp3": "-",
    "fuel_sensor":    0,
    "fuel_canbus":    0.000,
    "driver_name":    "อรรณพ วงศ์พรภักดี"
  }
]
```

No CAN signals. No engine RPM. No acceleration data. Speed and position only.

---

### Source C — SCGJWD Logistics Fleet

1-minute fixed-interval GPS export delivered as XLSX.

| Column          | Type    | Notes                            |
|-----------------|---------|----------------------------------|
| `IMEI`          | string  | 15-digit device identifier       |
| `TKNO`          | string  | Internal device callsign         |
| `LATITUDE`      | float   |                                  |
| `LONGITUDE`     | float   |                                  |
| `SPEED`         | float   | km/h                             |
| `DIRECTION`     | int     | bearing 0–359                    |
| `GPS_STATUS`    | char    | `Y` / `N`                        |
| `ENGINE_STATUS` | char    | `Y` / `N`                        |
| `GPS_TIME`      | string  | `YYYY-MM-DD HH:MM:SS`            |
| `ALTITUDE`      | int     | metres                           |

No CAN signals. No fuel data. Engine state is `Y`/`N` string only.

---

## 3. Data Grade Framework

Before writing any code, read this table. It governs what your model is
**allowed to claim** for each source.

| Grade | Source  | Interval  | Signals available                              | Scoring capability                        |
|-------|---------|-----------|------------------------------------------------|-------------------------------------------|
| A     | TMT     | ~1s CAN   | Speed, RPM, accel position, brake, fuel rate, odometer | Full harsh-event scoring               |
| B     | WDMT    | 15–60s    | Speed, position, odometer (some)               | Speed behavior index only                 |
| B     | SCGJWD  | 60s       | Speed, position, engine on/off                 | Speed behavior index only                 |

A Grade B score must be **honestly labelled as such**. It is not a Driver Score —
it is a Speed Behavior Index. This distinction matters for your insurance customers.

---

## 4. Tasks

Complete **all four tasks**. Task 4 carries equal weight to the coding tasks.

---

### Task 1 — Parse and Unify

Write a Python module that ingests all three source formats and produces a unified
GPS event stream as a list of normalized dicts.

**Target schema per record:**

```python
{
    "source":         str,          # "TMT" | "WDMT" | "SCGJWD"
    "vehicle_id":     str,          # source-native identifier — see note
    "recorded_at":    datetime,     # timezone-aware, UTC+7
    "lat":            float,
    "lon":            float,
    "speed_kmh":      float,
    "engine_on":      bool | None,
    "odometer_km":    float | None,
    "fuel_rate_lh":   float | None, # L/hr — TMT only
    "engine_rpm":     float | None, # TMT only
    "accel_position": float | None, # TMT only
    "brake_active":   bool | None,  # TMT only
}
```

**Requirements:**

- `vehicle_id` must be resolved from source data fields — **never hardcoded**
- All `recorded_at` must be timezone-aware and correctly aligned to UTC+7
- Engine ON/OFF state for TMT must be derived correctly from event codes across
  both packet types — **do not use the event_type field as a row-level binary flag**
- Handle `null`, `"-"`, `0.000`, and missing fields gracefully without crashing
- Output sorted by `(vehicle_id, recorded_at)`

**Deliverable:** `parser.py` with signature:

```python
def parse_all(
    tmt_files: list[str],   # paths to 0x51 and 0x52 JSON files
    wdmt_file: str,
    scgjwd_file: str,
) -> list[dict]:
```

---

### Task 2 — Trip Segmentation & Feature Engineering

Using your unified stream from Task 1, implement trip segmentation and extract
per-trip features.

**Trip segmentation rules:**

- A trip starts when `engine_on` transitions to `True`
- A trip ends when `engine_on` transitions to `False`, OR gap between consecutive
  records for the same vehicle exceeds **10 minutes**
- Minimum trip duration: 2 minutes — discard shorter segments as noise

**Per-trip features:**

| Feature              | Description                                                         |
|----------------------|---------------------------------------------------------------------|
| `trip_duration_min`  | End minus start timestamp                                           |
| `distance_km`        | Odometer delta if available; else haversine sum of GPS points       |
| `avg_speed_kmh`      | Mean of speed readings during trip                                  |
| `max_speed_kmh`      | Peak speed                                                          |
| `speed_variance`     | Variance of speed readings                                          |
| `harsh_brake_count`  | Sudden deceleration events — define and justify your threshold      |
| `harsh_accel_count`  | Sudden acceleration events — define and justify your threshold      |
| `idle_ratio`         | Fraction of trip time with engine on and speed = 0                  |
| `time_of_day_risk`   | `night` (22:00–05:00) / `peak` (07:00–09:00, 17:00–19:00) / `offpeak` |
| `avg_fuel_rate_lh`   | Mean fuel rate where CAN available; else `null`                     |
| `avg_rpm`            | Mean RPM where CAN available; else `null`                           |
| `data_grade`         | `"A"` if CAN signals present, `"B"` if GPS-only                    |

**Deliverable:** `features.py` with signature:

```python
def build_trip_features(unified_stream: list[dict]) -> pd.DataFrame:
```

---

### Task 3 — Driver Risk Model

Using your trip feature dataframe, build a driver risk scoring model.

**You have no labeled ground truth.** Design around this constraint explicitly —
do not fabricate synthetic labels without justifying why they are valid proxies.

#### 3a. Scoring Approach

- Define a driver risk score 0–100 (100 = safest)
- Choose your methodology: rule-based composite, unsupervised clustering,
  proxy-label construction, or hybrid
- Justify the choice — there is no single correct answer
- Compare at least **two approaches** and explain the trade-off:
  interpretability vs accuracy, simplicity vs flexibility, cold-start behavior
- Score must be explainable to a non-technical insurance underwriter

#### 3b. Data Grade Handling

- Your model must behave differently per `data_grade`
- Explicitly state what the score represents for Grade A vs Grade B
- State whether a Grade B output should be labelled "Driver Score" or something else
  — justify your answer

#### 3c. Feature Importance

- Produce a ranked feature contribution table
- Rule-based: show weight rationale
- ML-based: SHAP values or equivalent

#### 3d. Edge Cases

- How do you score a driver with only 1 trip in the dataset?
- How do you handle a vehicle where `engine_on` is always `null`?

**Deliverables:** `model.py` + `scoring_design.md` (max 1 page)

`scoring_design.md` must cover: methodology, grade-aware behavior, honest
limitations of the score given available data.

---

### Task 4 — Production System Design

Answer the following. Bullet points are fine — depth matters more than length.

#### 4a. Batch vs Streaming Boundary

- Which parts of this pipeline run in batch? Which in streaming?
- At what latency does the Driver Score API need to refresh?
- How does your answer change between TMT (CAN, ~1s) and SCGJWD (XLSX, 60s)?

#### 4b. H3 Spatial Resolution

You must assign each GPS point an H3 cell for downstream traffic products.

- What resolution for: (i) traffic heatmap API, (ii) driver trip path
  reconstruction, (iii) insurance zone risk mapping?
- What is the accuracy trade-off between res10 and res11 for road-segment
  matching — and when does it matter for your products?
- Should H3 resolution choice be made at ingestion time or at query time? Why?

#### 4c. The PDPA Constraint

TMT cannot share raw GPS lat/lon. They pre-aggregate to H3 before delivering data.

- Which features in your Task 2 output survive this constraint? Which are lost?
- Where does k-anonymity enforcement belong — at TMT's side before sharing,
  or at Mobility Insight's API output layer?
- What are the downstream product implications of each choice?

#### 4d. The Engine State Bug

A junior engineer writes:

```python
engine_on = 1 if event_type == 33 else 0
```

- Explain precisely what is wrong with this logic
- Name at least three downstream features this corrupts
- Write the correct approach in pseudocode

#### 4e. Scale Estimate

TMT fleet: ~1.4M vehicles. `0x51` fires every 60 seconds per vehicle.

- Estimate daily row volume hitting your ingestion layer from TMT alone
- What does this imply for your storage partitioning strategy?
- `raw_toyota_can_timeseries` grows faster than `raw_toyota_b2b_event` — why,
  and how does that affect your partition granularity decision?

#### 4f. Model Lifecycle

- When do you retrain the driver score model?
- How do you detect that scores have drifted — given you have no ground truth labels?
- How do you version models so a score issued today can be reproduced 6 months later?

**Deliverable:** `system_design.md` with structured answers to 4a–4f plus a simple
architecture diagram (ASCII or image) covering: raw data → ingestion → feature
store → model → API output.

---

## 5. Deliverables

```
submission/
├── parser.py           # Task 1
├── features.py         # Task 2
├── model.py            # Task 3
├── scoring_design.md   # Task 3 — methodology and honest limitations
├── system_design.md    # Task 4 — answers to 4a–4f + architecture diagram
└── README.md           # Setup, assumptions, known gaps, how to run
```

**README is required.** An empty or missing README is an automatic disqualifier
for a senior role.

---

## 6. Evaluation Criteria

| Dimension                        | Weight | What we look for                                                                 |
|----------------------------------|--------|----------------------------------------------------------------------------------|
| Parser correctness & robustness  | 25%    | Structural asymmetry handled; timezone correct; vehicle_id from source data      |
| Feature engineering quality      | 20%    | Defensible thresholds; honest null handling; data grade awareness                |
| Scoring methodology              | 20%    | Acknowledges no ground truth; grade-aware output; explainable to underwriter     |
| System design thinking           | 25%    | Batch/stream reasoning; H3 trade-offs; PDPA impact; scale math correct          |
| Code quality & README            | 10%    | Modular, readable, reproducible, documented assumptions                          |

### Automatic Disqualifiers

- Hardcoded vehicle identifiers anywhere in the code
- Timestamp handling that silently assumes UTC on TMT data
- `engine_on = 1 if event_type == 33 else 0` pattern left uncorrected
- Grade B data output labelled as a full Driver Score without qualification
- Missing or empty README

---

## 7. Notes

- This is a real-world problem, not an academic exercise
- There is no single correct solution — we evaluate reasoning, not answers
- Be explicit about your assumptions and where your solution breaks
- If you spot a data quality issue not mentioned here, call it out — that is signal
- **"I chose X over Y because..."** always outscores an unexplained choice


---

Additional Task — GPS Gap Estimation

Objective:
Develop a model to estimate the missing Latitude and Longitude coordinates for the 59-second gaps between two known GPS points (Point A and Point B).
Task 1: Simple Linear Regression (Baseline)
Concept: Assume the vehicle travels in a straight line at a constant speed between two pings.
Problem: Implement a Linear Regression model where Time is the independent variable (
) and Latitude and Longitude are the dependent variables (
).

Task 2: Predictive Pattern Modeling (Velocity & Heading)
Concept: In reality, vehicles change speed and direction. A simple straight line may be inaccurate.
Problem: Enhance the estimation by incorporating Speed and Heading (Bearing) data provided at the ping intervals.

Task 3: Model Evaluation
Problem: Using a "Ground Truth" dataset (actual 1-second GPS logs), evaluate your model's accuracy.
Metrics: Calculate the RMSE (Root Mean Square Error) for the coordinates and use the Haversine Formula to determine the average physical distance (in meters) between your estimated points and the actual locations.
