"""Run the word-level block tagger over the eval sets and score it.

    uv run python score.py                          # data/ → out/
    uv run python score.py --revision <commit>      # pin the HF commit
    uv run python score.py --model /path/to/best    # a local checkpoint

For every ``data/<dataset>.jsonl`` (windows: ``doc``, ``case``,
``window``, ``text``, ``block_spans``; the bundle ships ``portal_eval``,
``--datasets`` narrows the list) it writes ``out/<dataset>/``:

- ``pred.jsonl``   one row per window with ``block_spans_pred``, the
  spans the model returns after the word-level decode and the
  disposition heal (``tagger/wordlevel.py``, ``tagger/heal.py``);
- ``heal_log.jsonl`` every block the heal rewrote;
- ``eval_spans.json`` span-set precision / recall / F1, overall and per
  class, under two edge rules (``tagger/spannorm.py``): ``strict`` =
  content anchoring only, ``normalized`` = edge punctuation trimmed
  too. The gold is merged first the way the model sees it
  (back-to-back same-label spans → one, ``wordlevel.merge_adjacent``).

The viewer (``app.py``) reads these files. ``score_windows`` is the
scoring alone, importable, so saved predictions can be rescored.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from tagger import spannorm  # noqa: E402
from tagger.heal import heal_disposition  # noqa: E402
from tagger.wordlevel import decode_word_spans, merge_adjacent  # noqa: E402

DEFAULT_MODEL = "freelawproject/caselaw-block-tagger-wordlevel"
LABELS = ["party", "separator", "docketnumber", "court", "attorneys", "judges",
          "datefiled", "otherdate", "history", "disposition", "author", "heading"]
_WOFF = 100_000_000  # window offset so spans of different windows never collide


def load_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.open()]


def pick_device(name: str) -> str:
    import torch
    if name != "auto":
        return name
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def load_model(model: str, revision: str | None, device: str):
    import torch
    from transformers import AutoModelForTokenClassification, AutoTokenizer
    kw = {"revision": revision} if revision else {}
    tok = AutoTokenizer.from_pretrained(model, **kw)
    attn = {"attn_implementation": "sdpa"} if device in ("mps", "cpu") else {}
    m = AutoModelForTokenClassification.from_pretrained(model, **kw, **attn)
    m.to(device).eval()
    names = [m.config.id2label[i] for i in range(len(m.config.id2label))]
    dtype = torch.bfloat16 if device == "cuda" else torch.float32
    return tok, m, names, dtype


def predict_rows(rows: list[dict], tok, model, names: list[str], device: str,
                 max_length: int = 8192, heal: bool = True) -> tuple[list[dict], list[dict]]:
    """Model → decoded (and healed) spans per window, plus the heal log."""
    import torch
    preds, log = [], []
    for r in rows:
        enc = tok(r["text"], truncation=True, max_length=max_length, return_offsets_mapping=True)
        batch = {"input_ids": torch.tensor([enc["input_ids"]], device=device),
                 "attention_mask": torch.tensor([enc["attention_mask"]], device=device)}
        with torch.no_grad():
            if device == "cuda":
                with torch.autocast("cuda", dtype=torch.bfloat16):
                    logits = model(**batch).logits
            else:
                logits = model(**batch).logits
        ids = logits.argmax(-1)[0].tolist()
        spans = decode_word_spans(r["text"], enc["offset_mapping"], ids, names)
        if heal:
            spans, rows_log = heal_disposition(r["text"], spans)
            log += [{"doc": r["doc"], "case": r.get("case", 0), "window": r["window"], **x} for x in rows_log]
        preds.append({"doc": r["doc"], "case": r.get("case", 0), "window": r["window"],
                      "text_md5": hashlib.md5(r["text"].encode()).hexdigest(),
                      "block_spans_pred": spans})
    return preds, log


def score_windows(rows: list[dict], preds: list[dict]) -> dict:
    """Span-set metrics of ``preds`` against the gold of ``rows`` (same
    order), strict and normalized, overall and per class."""
    out = {}
    for name, punct in (("strict", False), ("normalized", True)):
        gall, pall = [], []
        for w, (r, p) in enumerate(zip(rows, preds, strict=True)):
            t = r["text"]
            mask = spannorm.edge_mask(t, punct)
            gold = merge_adjacent(t, r["block_spans"])
            gall += [{**s, "start": s["start"] + w * _WOFF, "end": s["end"] + w * _WOFF}
                     for s in spannorm.normalize(t, gold, punct, mask)]
            pall += [{**s, "start": s["start"] + w * _WOFF, "end": s["end"] + w * _WOFF}
                     for s in spannorm.normalize(t, p["block_spans_pred"], punct, mask)]
        out[name] = spannorm.span_prf(gall, pall)
    return {"block": out}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=HERE / "data")
    ap.add_argument("--out", type=Path, default=HERE / "out")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="HF repo id or a local checkpoint folder")
    ap.add_argument("--revision", default=None, help="HF commit / branch / tag to pin")
    ap.add_argument("--device", default="auto", help="auto | cuda | mps | cpu")
    ap.add_argument("--datasets", nargs="+", default=None,
                    help="dataset names (data/<name>.jsonl); default: every file in data/")
    ap.add_argument("--max-length", type=int, default=8192)
    ap.add_argument("--no-heal", action="store_true", help="skip the disposition heal")
    args = ap.parse_args()
    if args.datasets is None:
        args.datasets = sorted(f.stem for f in args.data.glob("*.jsonl"))
        if not args.datasets:
            sys.exit(f"no data/*.jsonl under {args.data} — unpack the eval-data bundle first")
    for ds in args.datasets:
        if "test" in ds:
            sys.exit(f"{ds}: test sets are not scored here")
    device = pick_device(args.device)
    t0 = time.monotonic()
    tok, model, names, _ = load_model(args.model, args.revision, device)
    print(f"loaded {args.model}{' @ ' + args.revision if args.revision else ''} on {device} "
          f"({len(names)} labels) in {time.monotonic() - t0:.0f}s")
    print("\n| dataset | windows | strict F1 | normalized F1 | strict P | strict R | healed blocks |")
    print("|---|---|---|---|---|---|---|")
    for ds in args.datasets:
        src = args.data / f"{ds}.jsonl"
        if not src.exists():
            print(f"| {ds} | missing: {src} | | | | | |")
            continue
        rows = load_rows(src)
        t1 = time.monotonic()
        preds, log = predict_rows(rows, tok, model, names, device, args.max_length, heal=not args.no_heal)
        metrics = score_windows(rows, preds)
        dst = args.out / ds
        dst.mkdir(parents=True, exist_ok=True)
        with (dst / "pred.jsonl").open("w") as f:
            for p in preds:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")
        with (dst / "heal_log.jsonl").open("w") as f:
            for x in log:
                f.write(json.dumps(x, ensure_ascii=False) + "\n")
        (dst / "eval_spans.json").write_text(json.dumps(metrics, indent=2))
        s, n = metrics["block"]["strict"], metrics["block"]["normalized"]
        print(f"| {ds} | {len(rows)} | {s['f1']:.3f} | {n['f1']:.3f} | {s['precision']:.3f} | {s['recall']:.3f} "
              f"| {len(log)} |   ({time.monotonic() - t1:.0f}s)")
    print(f"\noutputs in {args.out}")


if __name__ == "__main__":
    main()
