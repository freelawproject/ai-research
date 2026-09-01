"""Stage 2 — run the block tagger over canonical docs.

Reads the docs preprocess.py wrote, windows each one at block
boundaries under the token budget, runs the tagger (Hugging Face
token-classification), and decodes BIO predictions into RAW character
spans (doc-global). Markup/whitespace-only tokens neither extend nor
break a span; all boundary cleanup belongs to postprocess.py.

Output: ``<out>/<range>.json`` = ``{range, spans: [{start, end,
label}]}`` — the faithful model output, before normalization.

    uv run python infer.py                       # work/docs → work/raw
    uv run python infer.py --model /path/to/local/checkpoint
"""

import argparse
import collections
import json
import re
from pathlib import Path

import torch
from transformers import AutoModelForTokenClassification, AutoTokenizer

HERE = Path(__file__).resolve().parent
DEFAULT_MODEL = "freelawproject/caselaw-block-tagger"
MARKUP_RE = re.compile(r"</?(?:p|blockquote|footnotes|em|sup)>")
# leave headroom under the 8,192 context for specials + BPE variance
BUDGET = 8000


def block_ranges(text: str) -> list[tuple[int, int]]:
    """Char range of every block line, including its trailing newline."""
    out = []
    start = 0
    for m in re.finditer(r"\n", text):
        out.append((start, m.end()))
        start = m.end()
    if start < len(text):
        out.append((start, len(text)))
    return out


def make_windows(text: str, count_tokens) -> list[tuple[int, int]]:
    """Greedy block packing under the budget — whole blocks only, no
    overlap."""
    blocks = block_ranges(text)
    counts = [count_tokens(text[a:b]) for a, b in blocks]
    out = []
    i = 0
    while i < len(blocks):
        j = i
        total = counts[i]
        while j + 1 < len(blocks) and total + counts[j + 1] <= BUDGET:
            j += 1
            total += counts[j]
        out.append((blocks[i][0], blocks[j][1]))
        i = j + 1
    return out


def content_mask(text: str, offsets: list[tuple[int, int]]) -> list[bool]:
    """True per token = carries at least one content char (not markup,
    not whitespace). Specials (empty offsets) are False. Mirrors the
    label materialization mask used in training/eval."""
    is_content = bytearray(len(text))
    for i, ch in enumerate(text):
        is_content[i] = ch not in " \n"
    for m in MARKUP_RE.finditer(text):
        for i in range(m.start(), m.end()):
            is_content[i] = 0
    return [
        te > ts and any(is_content[c] for c in range(ts, min(te, len(text))))
        for ts, te in offsets
    ]


def decode_spans(pred_ids: list[int], keep: list[bool],
                 offsets: list[tuple[int, int]],
                 names: list[str]) -> list[dict]:
    """Predicted BIO ids → RAW char spans (token edges as-is). Masked
    tokens (markup/whitespace/specials) neither extend nor break a
    span."""
    spans: list[dict] = []
    cur: dict | None = None
    for pid, ok, (ts, te) in zip(pred_ids, keep, offsets):
        if not ok:
            continue
        tag = names[pid]
        if tag == "O":
            cur = None
            continue
        prefix, label = tag.split("-", 1)
        if prefix == "B" or cur is None or cur["label"] != label:
            cur = {"label": label, "start": ts, "end": te}
            spans.append(cur)
        else:
            cur["end"] = te
    return spans


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help="HF model id or local checkpoint path")
    ap.add_argument("--docs", type=Path, default=HERE / "work" / "docs")
    ap.add_argument("--out", type=Path, default=HERE / "work" / "raw")
    ap.add_argument("--ranges", nargs="*", default=[])
    args = ap.parse_args()

    docs = sorted(args.docs.glob("*.json"))
    if args.ranges:
        docs = [p for p in docs if p.stem in args.ranges]
    if not docs:
        print(f"no docs under {args.docs} — run preprocess.py first")
        return 1

    device = ("cuda" if torch.cuda.is_available()
              else "mps" if torch.backends.mps.is_available() else "cpu")
    tok = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForTokenClassification.from_pretrained(args.model)
    model.to(device).eval()
    id2label = {int(k): v for k, v in model.config.id2label.items()}
    names = [id2label[i] for i in range(len(id2label))]
    print(f"model {args.model} on {device} ({len(names)} labels)")

    def count_tokens(s: str) -> int:
        return len(tok(s, add_special_tokens=False)["input_ids"])

    args.out.mkdir(parents=True, exist_ok=True)
    for path in docs:
        doc = json.loads(path.read_text())
        text = doc["text"]
        spans: list[dict] = []
        for a, b in make_windows(text, count_tokens):
            wtext = text[a:b]
            enc = tok(wtext, add_special_tokens=True, truncation=True,
                      max_length=8192, return_offsets_mapping=True,
                      return_tensors="pt")
            offsets = [tuple(o) for o in enc.pop("offset_mapping")[0].tolist()]
            with torch.no_grad():
                logits = model(
                    **{k: v.to(device) for k, v in enc.items()}
                ).logits[0]
            pred = logits.argmax(-1).tolist()
            keep = content_mask(wtext, offsets)
            for s in decode_spans(pred, keep, offsets, names):
                spans.append({"start": a + s["start"], "end": a + s["end"],
                              "label": s["label"]})
        (args.out / path.name).write_text(
            json.dumps({"range": doc["range"], "spans": spans},
                       ensure_ascii=False)
        )
        by = collections.Counter(s["label"] for s in spans)
        print(f"{path.stem}: {len(spans)} raw spans "
              f"{dict(by.most_common())}")
    print(f"→ {args.out} (next: postprocess.py)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
