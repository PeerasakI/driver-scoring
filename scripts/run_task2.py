"""Read Task 1 Silver events, build trip features, and persist Task 2 Gold."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from mobility.task2_features.feature_extractor import TripFeatureExtractor


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", default="config")
    parser.add_argument(
        "--input",
        default="data/silver/unified_events/unified_events.parquet",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    input_path = root / args.input
    if not input_path.is_file():
        raise FileNotFoundError(
            f"Silver input not found: {input_path}. Run scripts/run_task1.py first."
        )

    frame = pd.read_parquet(input_path)
    records = frame.astype(object).where(frame.notna(), None).to_dict(orient="records")
    extractor = TripFeatureExtractor.from_yaml(root / args.config_dir)
    features = extractor.transform(records)
    output = extractor.write_gold(features)
    print(f"Wrote {len(features):,} trip features to {output}")
    if extractor.last_issues:
        print(f"Rejected {len(extractor.last_issues):,} input records; see the JSONL log")


if __name__ == "__main__":
    main()
