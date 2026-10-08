# Task 3 — Driver Risk Model

## 3a. Scoring Approach

### Method and meaning of the score

There are no claim, crash, or verified near-miss labels, so the model does **not** invent synthetic labels or claim accident probability. The primary `weighted_composite` is an explainable 0–100 behavior score (100 = safest). Each feature is scaled from `safe` (zero penalty) to `severe` (full penalty); weighted penalties are deducted from 100. Missing-signal weights are redistributed only across available signals, while `evidence_coverage` shows the reduced evidence. The contribution ledger records values, weights, penalties, rationale, and version.

This lets an underwriter explain that a score fell because of specific behaviors. A 45-study review found speed, acceleration, and braking among the most common telematics variables [1]; insurance research supports speed/time context and finds additive scores easier to communicate [2]. Feature **directions** are literature-informed, but numerical thresholds and weights are versioned expert priors, not fitted risk coefficients.

### Two implemented approaches

| Approach | Strength | Trade-off and cold start |
|----------|----------|--------------------------|
| `weighted_composite` (primary) | Stable, reproducible, and explainable; modular adjustable rules are supported by prior scoring work [3]. | Cannot learn interactions; accuracy is unmeasurable without labels. One trip is allowed, then shrunk toward 70. |
| `robust_zscore` (challenger) | Median/MAD within `(source, data_grade)` finds unusual high-risk features without labels; cohort modelling has literature precedent [4]. | Relative, cohort-dependent, and less explainable; unusual is not necessarily unsafe. Cohorts below 3 trips return 70/`insufficient_cohort`. |

The challenger supports comparison and drift discovery, not validated risk prediction. Study [4] used accidents/citations as partial labels; we have neither. Future credible outcomes should be used for out-of-sample evaluation and calibration.

## 3b. Data Grade Handling & 3c. Feature Importance

| Rank | Grade A: Driver Risk Score | Weight | Grade B: Speed Behavior Index | Weight | Rationale |
|---:|---|---:|---|---:|---|
| 1 | `max_speed_kmh` | 30% | `max_speed_kmh` | 50% | Peak-speed severity exposure. |
| 2 | `avg_speed_kmh` | 20% | `avg_speed_kmh` | 30% | Sustained-speed exposure. |
| 3 | `harsh_brake_rate_per_hour` | 20% | `speed_std_kmh` | 20% | Late-response / GPS smoothness proxy. |
| 4 | `harsh_accel_rate_per_hour` | 12% | — | — | Aggression proxy. |
| 5 | `night_exposure` | 10% | — | — | Exposure context, not unsafe control. |
| 6 | `speed_std_kmh` | 8% | — | — | Driving smoothness proxy. |

Grade A combines speed, CAN harsh-event rates, smoothness, and night exposure and is labelled **Driver Risk Score**. Grade B has no reliable high-frequency CAN behavior and is labelled **Speed Behavior Index**, never Driver Score. Grades are not blended, and `vehicle_id` remains a driver proxy. Actual importance is ranked by penalty contribution, not weight alone: a safe observed value contributes zero.

## 3d. Edge Cases

For one trip, the vehicle score uses credibility-style shrinkage toward the portfolio prior [5]:

`final = (n × observed + 5 × 70) / (n + 5)`.

Thus one trip scoring 40 becomes 65 with `confidence=low`; both values remain visible. Confidence is low for 1–4 trips, medium for 5–19, and high from 20.

If `engine_on` is always `null`, Task 2 uses GPS-gap segmentation and Task 3 issues only the Grade B **Speed Behavior Index**; engine-dependent features remain null.

The score is not crash probability: 64 means “more configured penalties than 85,” not “36% accident risk.” Missing speed limits, traffic/weather, driver identity, vehicle normalization, sparse sampling, and source differences limit interpretation. Thresholds, priors, and weights require sensitivity tests and later outcome calibration.

**References:**

[1] Boylan et al. (2024), [systematic review](../articles/a-systematic-review-of-the-use-of-in-vehicle-telematics-in-monitoring-driving-behaviours.pdf), doi:10.1016/j.aap.2024.107519.

[2] Guillen et al. (2024), [insurance pricing](../articles/pricing-weekly-motor-insurance-drivers-with-behavioral-and-contextual-telematics-data.pdf), doi:10.1016/j.heliyon.2024.e36501.

[3] Medarević et al. (2024), [driver scoring](../articles/simulation-based-driver-scoring-and-profiling-system.pdf), doi:10.1016/j.heliyon.2024.e40310.

[4] Moosavi & Ramnath (2023), [risk cohorts](../articles/context-aware-driver-risk-prediction-with-telematics-data.pdf), doi:10.1016/j.aap.2023.107269.

[5] Bühlmann (1965), [credibility](../articles/experience-rating-and-credibility.pdf), doi:10.1017/S0515036100008989.