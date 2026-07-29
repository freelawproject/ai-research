"""Surya OCR 2 (datalab-to/surya-ocr-2) OCR — POD-SIDE client.

Reads the canonical 1700x2200 page images in `images/` and runs Surya
through the `surya` package, which talks to the vLLM server that run.sh
started (SURYA_INFERENCE_BACKEND=vllm + SURYA_INFERENCE_URL).

Input is images, never PDFs — pack.sh stages the pipeline's own canonical
renders, so every engine sees byte-identical pixels.

TWO VARIANTS (--mode), both write the SAME JSON shape (one "region" per unit):
  • block (default) — one whole-page VLM call, `full_page=True`. Regions are
    LAYOUT BLOCKS (paragraph-level), each with a block bbox + html.
  • line — Surya's DetectionPredictor finds text LINES (EfficientViT), then the
    recognition VLM OCRs each line region (`full_page=False`, lines fed as
    `layout_results`). Regions are LINES, each with a LINE bbox + text.

    out/<stem>.json = {
        "stem": "...", "mode": "block|line",
        "text":    "<plaintext, reading order>",
        "regions": [{order,label,raw_label,bbox,confidence,html,text}, ...],
        "raw":     <full PageOCRResult.model_dump()>,
    }

Resume-safe (skips existing out/<stem>.json).

    python run_infer_surya.py --images-dir images --out-dir out --mode block
    python run_infer_surya.py --images-dir images --out-dir out_line --mode line
        [--batch-size 8 --limit N --stems a3d.340.1__p0,sct.145.2291__p2]
"""

from __future__ import annotations

import argparse
import html as _html
import json
import os
import re
import time
from pathlib import Path

from PIL import Image

_TAG = re.compile(r"<[^>]+>")


def _html_to_text(s: str) -> str:
    """Block html → plaintext (strip tags, unescape entities, squeeze ws)."""
    if not s:
        return ""
    return re.sub(r"[ \t]+", " ", _html.unescape(_TAG.sub("", s))).strip()


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _raw_dump(res):
    for meth in ("model_dump", "dict"):
        fn = getattr(res, meth, None)
        if callable(fn):
            try:
                return fn(mode="json") if meth == "model_dump" else fn()
            except TypeError:
                try:
                    return fn()
                except Exception:  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001
                pass
    return res if isinstance(res, (dict, list)) else None


def _write_json(path: Path, payload) -> None:
    """Write beside the target then rename.

    Resume treats "the file exists" as "this page is done", so a run killed
    mid-write (Ctrl-C, pod teardown, OOM) must never leave a truncated JSON —
    it would be skipped forever and silently corrupt that page. `.part` files
    are invisible to the *.json resume glob and excluded by pack.sh.
    """
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(payload, ensure_ascii=False))
    os.replace(tmp, path)


def _serialize(res, stem: str, mode: str) -> dict:
    raw = _raw_dump(res)
    blocks = _get(res, "blocks")
    if blocks is None and isinstance(raw, dict):
        blocks = raw.get("blocks")
    regions, texts = [], []
    for b in blocks or []:
        html = _get(b, "html") or _get(b, "text") or ""
        text = _html_to_text(html) if "<" in html else (html or "").strip()
        regions.append(
            {
                "order": _get(b, "reading_order"),
                "label": _get(b, "label"),
                "raw_label": _get(b, "raw_label"),
                "bbox": _get(b, "bbox"),
                "confidence": _get(b, "confidence"),
                "html": html,
                "text": text,
            }
        )
        if text:
            texts.append(text)
    return {
        "stem": stem,
        "mode": mode,
        "text": "\n".join(texts),
        "regions": regions,
        "raw": raw,
    }


def _load_predictors(mode: str):
    """Build the predictor(s). Recognition talks to the vLLM server (env set by
    run.sh); detection (line mode) is a small torch model surya runs locally.
    Imports deferred so `--help` needs no surya."""
    from surya.inference import SuryaInferenceManager
    from surya.recognition import RecognitionPredictor

    rec = RecognitionPredictor(SuryaInferenceManager())
    if mode == "line":
        from surya.detection import DetectionPredictor

        return rec, DetectionPredictor()
    return rec, None


def _lines_to_layout(det_result):
    """Detected text lines → a LayoutResult so recognition OCRs each line as a
    'block' (full_page=False). Each line becomes one LayoutBox (label 'Text')."""
    from surya.layout.schema import LayoutBox, LayoutResult

    boxes = [
        LayoutBox(
            polygon=b.polygon, label="Text", raw_label="Text", position=i
        )
        for i, b in enumerate(det_result.bboxes)
    ]
    return LayoutResult(bboxes=boxes, image_bbox=det_result.image_bbox)


def _ocr(rec, det, mode: str, imgs: list) -> list:
    """OCR a batch of images; returns a list of per-image result objects."""
    if mode == "line":
        dets = det(imgs)
        layouts = [_lines_to_layout(d) for d in dets]
        return rec(imgs, layout_results=layouts, full_page=False)
    return rec(imgs, full_page=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images-dir", default="images")
    ap.add_argument("--out-dir", default="out")
    ap.add_argument(
        "--mode",
        choices=("block", "line"),
        default="block",
        help="block = full-page (layout blocks); line = detection "
        "lines OCR'd individually",
    )
    ap.add_argument(
        "--batch-size",
        type=int,
        default=0,
        help="images per predictor call (0=auto: block 8, line 1 — "
        "line fans out many per-line requests, so batching "
        "pages floods the server with 500s)",
    )
    ap.add_argument(
        "--limit", type=int, default=0, help="run only the first N (0=all)"
    )
    ap.add_argument(
        "--stems", default="", help="comma-separated stems to run (subset)"
    )
    ap.add_argument(
        "--shard",
        default="0/1",
        help="run every n-th page (i/n) for multi-GPU pods",
    )
    args = ap.parse_args()

    images_dir, out = Path(args.images_dir), Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    want = {s.strip() for s in args.stems.split(",") if s.strip()}
    stems = sorted(
        p.stem for p in images_dir.glob("*.png") if not p.name.startswith("._")
    )
    if want:
        stems = [s for s in stems if s in want]
    shard_i, shard_n = (int(v) for v in args.shard.split("/"))
    stems = stems[shard_i::shard_n]
    if args.limit:
        stems = stems[: args.limit]
    todo = [s for s in stems if not (out / f"{s}.json").exists()]
    bs = args.batch_size or (1 if args.mode == "line" else 8)
    print(
        f"[{args.mode}] {len(stems)} images, {len(todo)} to do (batch {bs})",
        flush=True,
    )
    if not todo:
        return

    rec, det = _load_predictors(args.mode)
    t0, done, fail, empty = time.time(), 0, 0, 0
    for i in range(0, len(todo), bs):
        chunk = todo[i : i + bs]
        imgs = [
            Image.open(images_dir / f"{s}.png").convert("RGB") for s in chunk
        ]
        try:
            results = _ocr(rec, det, args.mode, imgs)
        except Exception as e:  # noqa: BLE001 — retry this chunk one-by-one
            print(
                f"  !! batch failed ({type(e).__name__}: {e}); per-image",
                flush=True,
            )
            results = []
            for img in imgs:
                try:
                    results.append(_ocr(rec, det, args.mode, [img])[0])
                except Exception as e2:  # noqa: BLE001
                    print(
                        f"     !! page errored: {type(e2).__name__}: {e2}",
                        flush=True,
                    )
                    results.append(None)
        for stem, res in zip(chunk, results):
            done += 1
            if res is None:
                fail += 1
                continue
            payload = _serialize(res, stem, args.mode)
            # An EMPTY result is a failure, not a blank page. A server can answer
            # a whole batch with blocks:[] and no exception; writing that would
            # look like success, and resume ("the file exists") would never retry
            # it — silently losing real pages. Observed for real: one chunk of 16
            # consecutive pages came back empty while another engine read
            # 1,200-3,800 chars from each of them.
            if not payload["regions"] and not payload["text"].strip():
                try:
                    retry = _ocr(
                        rec,
                        det,
                        args.mode,
                        [
                            Image.open(images_dir / f"{stem}.png").convert(
                                "RGB"
                            )
                        ],
                    )[0]
                    payload = _serialize(retry, stem, args.mode)
                except Exception as e:  # noqa: BLE001
                    print(
                        f"     !! {stem}: empty, and retry errored "
                        f"({type(e).__name__}: {e})",
                        flush=True,
                    )
                    fail += 1
                    continue
                if not payload["regions"] and not payload["text"].strip():
                    # Twice empty: write it so we converge instead of looping,
                    # but say so loudly — a truly blank page is rare, and even a
                    # near-blank one normally yields its page number.
                    empty += 1
                    print(
                        f"     !! {stem}: EMPTY after retry — wrote it, but "
                        f"verify this page by hand",
                        flush=True,
                    )
                else:
                    print(
                        f"     .. {stem}: empty on first pass, retry "
                        f"recovered it",
                        flush=True,
                    )
            _write_json(out / f"{stem}.json", payload)
        # Rate over pages actually WRITTEN, not attempted. A failing server
        # answers in milliseconds, so counting failures here would inflate the
        # rate into something that looks like a speedup — an all-errors run can
        # report tens of pg/s while producing nothing.
        ok = done - fail
        rate = ok / (time.time() - t0) if ok else 0.0
        line = f"  {ok}/{len(todo)} written  {rate:.2f} pg/s"
        if fail:
            line += f"  !! {fail} FAILED — not written; a re-run retries them"
        if empty:
            line += f"  !! {empty} EMPTY after retry — verify by hand"
        print(line, flush=True)
        # Bail instead of burning the whole set against a broken server.
        if done >= 16 and fail == done:
            raise SystemExit(
                "!! every attempted page has failed — check serve.log. If those "
                "are 500s, lower --batch-size / SURYA_INFERENCE_PARALLEL."
            )

    print(
        f"done: {done - fail}/{len(todo)} pages → {out} ({fail} failed"
        + (f", {empty} EMPTY" if empty else "")
        + ")",
        flush=True,
    )


if __name__ == "__main__":
    main()
