"""Required Task 1 public API."""

from mobility.task1_ingestion.pipeline import IngestionPipeline


def parse_all(
    tmt_files: list[str],
    wdmt_file: str,
    scgjwd_file: str,
) -> list[dict]:
    """Parse available partner files into the canonical, deterministically sorted schema."""

    return IngestionPipeline.from_yaml().parse_all(
        tmt_files=tmt_files,
        wdmt_file=wdmt_file,
        scgjwd_file=scgjwd_file,
    )
