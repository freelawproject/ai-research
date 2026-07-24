"""Artifact contract (v1) — the interface between pipeline and viewer.

The pipeline writes one JSON document per page x route at
``data/artifacts/<dataset>/<route>/<page>.json``. The viewer only renders
these documents; it holds no state of its own. The full schema is documented
in docs/pipeline.md and evolves with SCHEMA_VERSION.
"""

from pathlib import Path

SCHEMA_VERSION = 1


def artifact_path(
    artifacts_root: Path, dataset: str, route: str, page: str
) -> Path:
    return artifacts_root / dataset / route / f"{page}.json"
