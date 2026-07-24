"""Package-specific paths.

The data folder is distributed separately as a zip and placed at the repo
root (see README). Every path below derives from DATA_ROOT so the whole tree
can be relocated with one env var.
"""

from pathlib import Path

import environ

env = environ.FileAwareEnv()

_ROOT = Path(__file__).resolve().parents[2]

DATA_ROOT = Path(env("DATA_ROOT", default=str(_ROOT / "data")))
DATASETS_ROOT = DATA_ROOT / "datasets"
ARTIFACTS_ROOT = DATA_ROOT / "artifacts"
WEIGHTS_ROOT = DATA_ROOT / "weights"
