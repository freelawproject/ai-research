---
license: agpl-3.0
base_model: juliozhao/DocLayout-YOLO-DocStructBench
tags:
  - object-detection
  - document-layout-analysis
  - legal
  - yolov10
library_name: doclayout-yolo
pipeline_tag: object-detection
---

# container-yolo

Page-container detector for scanned US case-law reporters. Given a rendered
page image it locates the structural containers a downstream OCR pipeline
needs in order to read the page in the right order — columns, footnote
blocks, headings, page numbers, and so on.

This is a fine-tune of DocLayout-YOLO (YOLOv10, 19.5M parameters) from the
DocStructBench checkpoint, produced in two rounds: a supervised round on
hand-reviewed pages from bound reporter volumes, then a self-distillation
round on 20k pseudo-labeled pages. See Training below.

## Classes

| id | class | what it marks |
|---|---|---|
| 0 | `column` | a body text column |
| 1 | `footnote_block` | the footnote region at the foot of a page |
| 2 | `caption` | a figure or table caption |
| 3 | `page_number` | the printed page number |
| 4 | `image` | a photograph or figure |
| 5 | `table` | a tabular region |
| 6 | `heading` | a running head or section heading |
| 7 | `blockquote` | an indented quotation |

## Intended use

Structural pre-processing for OCR of scanned case law: the containers drive
reading order (page number → body by column → footnotes) and let a pipeline
tell body text from marginal text. It detects *containers*, not words — it
produces no text and is not an OCR model.

> **Input must be REDACTED page renders.** Every page this model was trained
> and evaluated on had redaction burned in at render time, and it does not
> transfer to unredacted scans — see Limitations for the measurement. Redact
> first, then render, then detect.

Out of scope: unredacted scans, born-digital PDFs (use the embedded text),
non-legal document layouts, and any use where the class set above does not
describe the page.

## Input

Pages rendered to **1700×2200 RGB**, inference at `imgsz=1024`. Detections
come back in 1700×2200 coordinate space. Feeding it a different render size
will work but has not been evaluated.

## Training

Two rounds. Round 1 is a supervised fine-tune on hand-reviewed pages.
Round 2 is self-distillation: round 1 labels a much larger unlabeled corpus,
and training continues from round-1 weights on those labels. Round 2 is the
published checkpoint.

### Round 1 — supervised fine-tune on reviewed pages

| | |
|---|---|
| base | `doclayout_yolo_docstructbench_imgsz1024.pt` |
| data | 632 hand-reviewed pages — **507 train / 43 val / 82 test** |
| split | by volume, so no volume appears in two splits |
| schedule | 100 epochs max, patience 20 — **stopped at 74** |
| imgsz / batch | 1024 / 6 |
| optimizer | auto, `lr0=0.01` |
| date | 2026-07-09 |

### Round 2 — pseudo-label self-distillation

Round 1 was run as a teacher over **22,000 pages** sampled from a
96,131-page corpus of scanned reporters. Its raw predictions were cleaned by
four deterministic rules before becoming labels:

1. de-duplicate overlapping boxes of the same class
2. keep at most one `page_number` per page
3. clip `column` boxes at the top of the footnote block, dropping any
   remnant under 40px
4. widen `footnote_block` to the column x-extent

| | |
|---|---|
| base | round-1 weights (continued fine-tune) |
| data | **19,790 train / 2,210 val** pseudo-labeled pages |
| schedule | **12 epochs, patience 4** — deliberately short, so the student does not overfit the teacher's mistakes |
| imgsz / batch | 1024 / 6 |
| optimizer | auto, `lr0=0.01` |
| date | 2026-07-10 |

Neither training corpus is published with these weights.

## Results

### Validation, per round

| | round 1 | round 2 |
|---|---|---|
| precision | 0.892 | 0.959 |
| recall | 0.947 | 0.974 |
| mAP50 | 0.954 | 0.987 |
| mAP50-95 | 0.825 | 0.906 |

**These two columns are not comparable.** Round 1 validates against 43
human-labeled pages; round 2 validates against 2,210 *pseudo-labeled* pages,
so its numbers partly measure agreement with the teacher that produced them.
Use them to read each round's own convergence, not to rank the rounds.

### Held-out test set

The ranking that counts is a **30-page set, independently reviewed and never
used in training or validation by either round**. Macro-F1 over the 7 classes
that occur in it (`table` has no instances):

| model | macro-F1 |
|---|---|
| **round 2 (this checkpoint)** | **0.893** |
| round 1 | 0.873 |
| reporter-YOLO (separate silver-label baseline) | 0.547 |
| DocLayout-YOLO DocStructBench, un-finetuned | 0.190 |

Per class, round 2 against round 1:

| class | round 2 | round 1 |
|---|---|---|
| column | 0.963 | 0.953 |
| footnote_block | **0.966** | 0.897 |
| caption | **0.944** | 0.857 |
| page_number | 0.947 | **0.983** |
| image | 0.800 | 0.800 |
| heading | 0.851 | 0.857 |
| blockquote | 0.782 | 0.764 |

Round 2 buys large gains on `footnote_block` and `caption` and gives some
back on `page_number` (three misses on 30 pages). `image` rests on two
instances, so treat it as noise rather than signal.

## Usage

```python
from doclayout_yolo import YOLOv10

model = YOLOv10("container_round2.pt")
result = model.predict(img, imgsz=1024, conf=0.2, device=0)[0]
dets = [
    {
        "label": model.names[int(b.cls[0])],
        "bbox": [round(float(v), 1) for v in b.xyxy[0].tolist()],
        "confidence": round(float(b.conf[0]), 3),
    }
    for b in result.boxes
]
```

Two upstream quirks you will hit loading this checkpoint:

- torch ≥ 2.6 defaults `weights_only=True`, which rejects it — load with
  `weights_only=False`.
- `DilatedBlock.dilated_conv` reads `self.dcv.bn`, which `fuse()` deletes;
  patch it to fall back to the fused conv's own bias.

## Limitations

**Unredacted pages break it.** This is the sharp edge, and it is measured:
run over 3,541 pages of unredacted reporter scans, **89.3% (3,161 pages)
produced no `column` at all**, and the model emitted 3,319 whole-page `image`
boxes against just 601 columns. Twenty-two pages produced nothing. On the
redacted held-out set the same checkpoint scores 0.893 macro-F1. Since
`column` is what drives reading order, output from unredacted input is not
merely degraded — it is unusable. The failure is quiet: pages come back with
plausible-looking detections rather than an error.

Trained on one genre — bound, scanned US case-law reporters, largely
two-column with footnotes. Pages far from that (single-column slip
opinions, forms, handwriting, non-English typography) are out of
distribution. Bleed-through from the facing page can produce spurious
detections; the pipeline that uses this model filters them downstream
rather than relying on the detector.

## Licensing

**AGPL-3.0**, inherited from [DocLayout-YOLO](https://github.com/opendatalab/DocLayout-YOLO),
which these weights are a fine-tune of. The checkpoint carries
`license: AGPL-3.0` in its own metadata for the same reason.

Anything built on this model inherits those terms. Refer to the
[licence text](https://www.gnu.org/licenses/agpl-3.0.html) itself.
