"""Package-specific paths.

The data folders are distributed separately as zips and placed at the repo
root (see README). Every path below derives from its root so each tree
can be relocated with one env var.

Align sets live under their OWN root, never under DATA_ROOT: each surface
lists datasets by scanning its root, so the split is what keeps an align
set out of the route pickers (and a route set out of /align/).
"""

from pathlib import Path

import environ

env = environ.FileAwareEnv()

_ROOT = Path(__file__).resolve().parents[2]

DATA_ROOT = Path(env("DATA_ROOT", default=str(_ROOT / "data")))
DATASETS_ROOT = DATA_ROOT / "datasets"
ARTIFACTS_ROOT = DATA_ROOT / "artifacts"
WEIGHTS_ROOT = DATA_ROOT / "weights"

ALIGN_DATA_ROOT = Path(
    env("ALIGN_DATA_ROOT", default=str(_ROOT / "align_data"))
)
ALIGN_DATASETS_ROOT = ALIGN_DATA_ROOT / "datasets"
ALIGN_ARTIFACTS_ROOT = ALIGN_DATA_ROOT / "artifacts"
