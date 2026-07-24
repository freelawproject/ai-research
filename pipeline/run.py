"""CLI: run a dataset through a route, writing per-page artifacts.

Usage: uv run python -m pipeline.run --dataset golden30 --route mistral

Stages land milestone by milestone; this entry point currently validates
arguments only.
"""

import argparse

from pipeline.routes import ROUTES


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--route", required=True, choices=sorted(ROUTES))
    parser.parse_args()
    raise SystemExit("pipeline stages are not implemented yet")


if __name__ == "__main__":
    main()
