# Exploration notebooks

Run notebooks from the repository root after `uv sync`. They import production code from
`src/`; parsing and business rules must not be reimplemented in notebooks.

Use `00_end_to_end_eda.ipynb` as the single in-memory smoke test for Task 1 → Task 2 → Task 3.
It reads Bronze data but never calls a persistence function or writes into `data/`. Task 2 and
Task 3 displays per-trip scores, cold-start-adjusted vehicle scores, and ranked feature
contributions through `submission.model.DriverRiskModel`.

Task 1 order:

1. `01_tmt_exploration.ipynb`
2. `02_wdmt_exploration.ipynb`
3. `03_scgjwd_exploration.ipynb`

Task 2:

1. `task2/01_trip_segmentation_experiments.ipynb` — compare diagnostic boundary
   strategies and benchmark multiple gap thresholds with the production `TripSegmenter`;
   all results remain in memory.
2. `task2/02_trip_features_eda.ipynb`

Task 3 reuses `00_end_to_end_eda.ipynb`; it does not introduce another notebook or persistence
path.
