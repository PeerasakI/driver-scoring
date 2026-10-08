"""Run Task 1 against files in data/bronze and persist the Silver dataset."""

from __future__ import annotations

import argparse
from pathlib import Path

from mobility.task1_ingestion.pipeline import IngestionPipeline


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", default="config")
    parser.add_argument("--bronze-dir", default="data/bronze")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    bronze = root / args.bronze_dir
    pipeline = IngestionPipeline.from_yaml(root / args.config_dir)
    records = pipeline.parse_all(
        tmt_files=[str(path) for path in sorted(bronze.glob("tm*.json"))],
        wdmt_file=str(bronze / "wdmt.json") if (bronze / "wdmt.json").is_file() else "",
        scgjwd_file=str(bronze / "scgjwd.csv")
        if (bronze / "scgjwd.csv").is_file()
        else "",
    )
    output = pipeline.write_silver(records)
    print(f"Wrote {len(records):,} records to {output}")
    if pipeline.last_issues:
        print(f"Rejected {len(pipeline.last_issues):,} records/files; see the JSONL log")


if __name__ == "__main__":
    main()
