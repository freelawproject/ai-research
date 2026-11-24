"""Paths and fixed settings. Everything under data/ is git-ignored."""

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
ROOT = PACKAGE_DIR.parent
DATA_DIR = ROOT / "data"

SOURCES = {
    "original": DATA_DIR / "decennial.12th.1.pdf",
    "bitonal": DATA_DIR / "decennial.12th.1.bitonal.pdf",
}
OUTPUTS_DIR = DATA_DIR / "outputs"
EVAL_DIR = DATA_DIR / "eval"  # one sub-folder per prompt version: eval/v1, eval/v2
CACHE_DIR = DATA_DIR / "cache" / "pages"
PROMPTS_DIR = PACKAGE_DIR / "prompts"

DEFAULT_PROMPT = "table_of_cases_v3"
FIRST_LIST_PAGE = 20  # first PDF page of the table of cases


def prompt_version(engine: str) -> str:
    """The prompt version in an engine folder name (<model>__<version>__<scan>)."""
    parts = engine.split("__")
    return parts[1] if len(parts) > 1 else "v1"


def page_file(directory: Path, page: int) -> Path:
    return directory / f"page_{page:04d}.json"
