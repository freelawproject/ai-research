"""Mistral OCR over a staged set — LOCAL runner (API, no pod).

Mistral is the one engine with no GPU kit: it is a hosted API, so it runs
from here against the same canonical 1700x2200 page images the pod kits
consume, and writes the same per-page JSON shape.

    out/<stem>.json = {"markdown": "...", "blocks": [{type,bbox,text}]}
                      (bbox in 1700x2200 space — the canonical render)

TWO MODES, same output:
  realtime  synchronous /v1/ocr per page across a small thread pool. Costs
            more per page and is rate-limited, so it is for PROVING THE CODE
            on a handful of pages before committing a whole set.
  batch     one batch job per chunk of pages. The scalable path: each page is
            uploaded once as an `ocr` file and referenced by id, so the
            manifest stays small no matter how many pages there are.

    python run_mistral.py --set newvols --mode realtime --limit 5
    python run_mistral.py --set newvols --mode batch

Resume-safe: pages with an existing out/<stem>.json are skipped, so the
batch run after a realtime smoke test does not redo those pages, and an
interrupted run picks up where it stopped. Pages that ERROR are not written,
so re-running the same command retries exactly the failures.

Images are the pipeline's own canonical renders (a dataset's page_png/,
staged into the set) — so Mistral's block bboxes land in the same
1700x2200 space as every other engine's.

Work is chunked (--chunk) rather than done in one pass: a whole volume set
is thousands of images, and holding them all in memory to hand to a single
batch job is both slow to start and easy to lose. Each chunk writes its
results before the next one begins.

Needs MISTRAL_API_KEY (from .env next to this file, or the environment).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from mistral_ocr import run_ocr_batch, run_ocr_realtime

HERE = Path(__file__).resolve().parent
SETS = HERE.parent / "data" / "sets"


def _load_env() -> None:
    """Read KEY=VALUE from .env without adding a dependency; the environment
    wins so an exported key can override the file."""
    env = HERE / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def _images_dir(set_name: str) -> Path:
    """The set's rendered pages. Mistral never touches a PDF — it reads
    the pipeline's canonical renders so its coordinates match every
    other engine's."""
    d = SETS / set_name / "images"
    if not d.exists() or not any(d.glob("*.png")):
        sys.exit(
            f"!! no page images for set '{set_name}' ({d})\n"
            "   stage the pipeline's renders there, e.g.\n"
            f"     cp data/datasets/<name>/page_png/*.png {d}/\n"
            "   (rendering belongs to the pipeline's render stage — "
            "this never renders)"
        )
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True, help="a set under data/sets/")
    ap.add_argument("--mode", choices=("realtime", "batch"), required=True)
    ap.add_argument(
        "--limit",
        type=int,
        default=0,
        help="only the first N pages still to do (0 = all)",
    )
    ap.add_argument(
        "--chunk",
        type=int,
        default=500,
        help="pages per batch job / per realtime wave",
    )
    ap.add_argument(
        "--concurrency",
        type=int,
        default=8,
        help="realtime only: threads against /v1/ocr",
    )
    ap.add_argument(
        "--out-dir", default="", help="default: data/sets/<set>/out/mistral"
    )
    args = ap.parse_args()

    _load_env()
    if not os.environ.get("MISTRAL_API_KEY"):
        sys.exit(
            "!! MISTRAL_API_KEY not set — put it in mistral/.env "
            "(see .env.example) or export it"
        )

    images_dir = _images_dir(args.set)
    default_out = SETS / args.set / "out" / "mistral"
    out = Path(args.out_dir) if args.out_dir else default_out
    out.mkdir(parents=True, exist_ok=True)

    stems = sorted(
        p.stem for p in images_dir.glob("*.png") if not p.name.startswith("._")
    )
    todo = [s for s in stems if not (out / f"{s}.json").exists()]
    if args.limit:
        todo = todo[: args.limit]
    chunk = max(1, args.chunk)
    print(
        f"[{args.mode}] set={args.set}: {len(stems)} pages, "
        f"{len(todo)} to do, chunk {chunk} -> {out}",
        flush=True,
    )
    if not todo:
        return

    ok = err = 0
    for start in range(0, len(todo), chunk):
        part = todo[start : start + chunk]
        n = start // chunk + 1
        total = (len(todo) + chunk - 1) // chunk
        print(f"-- chunk {n}/{total}: loading {len(part)} images", flush=True)
        items = [(s, (images_dir / f"{s}.png").read_bytes()) for s in part]

        if args.mode == "realtime":
            results = run_ocr_realtime(items, concurrency=args.concurrency)
        else:
            results = run_ocr_batch(items)

        for stem in part:
            res = results.get(stem)
            if res is None or res.get("error"):
                err += 1
                reason = "no result returned" if res is None else res["error"]
                print(f"  !! {stem}: {reason}", flush=True)
                continue  # unwritten => retried on re-run
            # Atomic: resume reads "file exists" as "page done", so an
            # interrupted run must never leave a truncated JSON behind.
            tmp = out / f"{stem}.json.part"
            tmp.write_text(
                json.dumps(res, ensure_ascii=False), encoding="utf-8"
            )
            os.replace(tmp, out / f"{stem}.json")
            ok += 1
        print(
            f"-- chunk {n}/{total} done: {ok} ok, {err} failed so far",
            flush=True,
        )

    print(f"done: {ok} pages written, {err} failed -> {out}", flush=True)
    if err:
        print(
            "   re-run the same command to retry only the failures", flush=True
        )


if __name__ == "__main__":
    main()
