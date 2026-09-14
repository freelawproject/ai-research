# Experiment plan — citation-treatment triage with CaseLawModernBERT-large

**Question.** Given a passage from a citing opinion with one cited case marked,
does the passage imply that the citing opinion applies any treatment other than
plain "Cited by" to that case? A small encoder answers this for every citation
mention; only flagged pairs go on to a larger model for the fine-grained label.
This plan covers the triage model only. The treatment classifier that consumes
its output is a later experiment.

**Why this shape.** On the current benchmark, expert disagreement sits almost
entirely at this gate (Cited by vs anything), and citing-reference evidence
sits within 500 characters of a mention of the target case 92% of the time
(see `readme.md`). The gate is where the human ceiling is lowest, where
Sonnet's false flags come from, and where an encoder can read every mention of
every case in the corpus at negligible cost.

## Model

`ai-law-society-lab/CaseLawModernBERT-large` (0.4B, 8,192-token context,
Apache 2.0): ModernBERT continued-pretrained on 8.3M US court opinions. This is
the base the caselaw block tagger was fine-tuned from, so the RunPod training
kit, `--base large` plumbing, and MPS smoke-test path carry over. Head: single
sequence-classification head (positive = any treatment). No in-house continued
pretraining.

## Unit of prediction and input format

- **Unit** = one window of the citing opinion with one target case marked. A
  (citing, cited) pair has one window per mention cluster of the target.
- **Window** = mention-centered, paragraph-aligned, 2,048 tokens by default
  (ablate 1,024 / 4,096). Mentions of the same target closer than half a window
  share one window. 48% of benchmark opinions fit whole in 8,192 tokens; for
  those the window is the opinion.
- **Markers.** Every mention of the target case in the window (full cite, short
  form, `id.`, `supra`, per eyecite coreference) is wrapped `[T] … [/T]`. Other
  citations stay as text; ablate replacing them with a single `[CITE]` token.
  Median 3 cited cases share the evidence passage, so the marker is what tells
  the model which case is being judged.
- **Header** prepended to every window: citing court and year, target case
  name and reporter cite. Cheap posture signal for direct-history cases.
- **Pair score** = max over the pair's windows. Evaluation is at pair level so
  it lines up with the expert labels; window-level metrics are diagnostic.

## Data

Three disjoint sets. Benchmark citing clusters (384) never appear in training.

**Train (silver).** A fresh CLReplica sample, labeled by the seeder model.
Not random: the sampler keeps opinions where treatment-indicative language
(overrule, abrogate, disapprove, distinguish, decline to follow, limit to its
facts, criticize, call into question, and similar) occurs within 300
characters of a citation span, plus a control quota of opinions with none of
it near a citation so the encoder cannot key on the mere presence of those
words (direct-history language is not filtered; every opinion has a
disposition). Population:
published clusters with ≥1 cited opinion, filed 1950 onward, from SCOTUS, the
13 federal circuits, and the state courts of last resort. Stratified with
equal quotas:

| Axis | Bins |
|---|---|
| Court group | SCOTUS · federal circuits · state high courts |
| Decade filed | ≤1970s · 1980s · 1990s · 2000s · 2010s · 2020s |
| Length (all sub-opinions) | short <20K chars · medium 20–60K · long >60K |

Pilot 5 keyword + 1 control per cell (~324 opinions), then 25 + 5 (~1,620).
The sampler (`triage/sample_clreplica.py`, run in the cl-django container)
exports raw `html_with_citations` per sub-opinion in the citator-benchmark
`opinion_html` JSON shape. A human annotator then corrects citation extraction
and coreference in the benchmark viewer's Grouping annotator (eyecite-seeded
spans are the starting point, not the truth) before any window is built.
Step-by-step in `triage/checklist.md`.

**Dev and test (gold).** Split the 384 benchmark citing clusters by cluster,
stratified by court group and by whether the cluster has any positive pair:

| | Clusters | Positive pairs (approx.) | Use |
|---|---|---|---|
| Dev | 150 | ~230 | seeder iteration, model selection, threshold |
| Test | 234 | ~330 | touched once, at the end |

Only pairs with a final expert treatment count. Pairs whose only non-neutral
label is an "as recognized by" form are excluded from both sets (4% human
agreement; not a learnable target yet). Positives are 89% SCOTUS (472 of 513
pairs), so per-group slices for FED and STATE are reported but not gated on.

**Seeder.** Chosen by a bake-off on the dev set among the incumbent, the
strongest current frontier model, and the cheapest model that handles full
opinions (checklist § D): recall first, then precision, then cost at the
1,620-opinion scale. Prompted with the existing `citator` prompt extended to
return, per cited case, the treatment plus the quote it relied on. Quotes locate the positive window.
Windows of a positive pair that do not overlap the quote are masked out of
training rather than labeled negative.

## Steps and gates

1. **Seed the dev set and iterate the seeder** until its pair-level
   any-treatment quality on dev is at or above the human ceiling on the same
   pairs. Human leave-one-out on the current benchmark, excluding "as
   recognized by": F1 0.64, precision 0.57, recall 0.74. 0529 Sonnet today:
   F1 0.53, precision 0.43, recall 0.70. Gate: seeder recall ≥ 0.80 and
   precision ≥ 0.55 on dev, and every dev positive's quote resolves to a
   window. Each seeder version is a run id; disagreements with the experts
   are reviewed in the viewer to decide whether the fix is prompt-side or a
   label problem.
2. **Seed the training sample** with the accepted seeder version. Pilot 300
   opinions first; check the positive rate and stratum balance in the viewer
   before scaling to 1,500.
3. **Train CaseLawModernBERT-large** on the silver windows. Negatives
   downsampled to ~5:1, weighted loss, early stop on dev pair-level PR-AUC.
   Iterate on: window size, `[CITE]` masking, header on/off, hard negatives
   (windows the seeder flagged but experts did not), seed size (300 vs 1,500).
   Threshold chosen on dev for a recall target of 0.90, then 0.95.
4. **Final test evaluation, once.** Model vs seeder vs human leave-one-out on
   the same pairs. Report recall, precision, flagged fraction (the downstream
   cost lever), and slices by category (citing reference vs direct history),
   court group, decade, length bin, and mention count.

**Success** = at the chosen threshold the encoder recovers ≥ 0.90 of gold
positives on test while flagging ≤ 15% of pairs (Sonnet flags 7.9% today with
recall 0.70). Direct-history recall is reported as its own slice and is
expected to lag; a structural route for direct history (appellate-chain
linking plus disposition) is a separate follow-up.

## Viewer

FastAPI app in the FLP house style (`/html-builder`), port 8195, full-width
with height-aligned panels:

- **Left: the window.** Target mentions highlighted, other citations subdued,
  header shown above. Prev/next across windows of a pair and pairs of an
  opinion. Toggle to the full opinion with the window outlined.
- **Right: labels.** Expert votes and final (dev/test), seeder treatment with
  quote and rationale, model probability and threshold decision, agreement
  badges (seeder vs expert, model vs expert, model vs seeder).
- **Review marks** per window: agree / disagree with the seeder, agree /
  disagree with the model, free-text note. Written to a review JSON keyed by
  (run id, window id), never overwritten by rebuilds.
- **Queues.** Seeder-vs-expert disagreements on dev; model false negatives and
  false positives on dev; random sample of silver positives and negatives for
  spot-checks.
- **Dataset page.** Stratum counts, positive rate per stratum, split sizes,
  and the per-run metrics table.

## Infrastructure and cost

| Item | Estimate |
|---|---|
| CLReplica sampling + text export | cl-django container, ~1 h |
| Seeder on dev (150 clusters) per iteration | ~$15 at Sonnet 4.6 batch rates; more on a larger model |
| Seeder on 1,500 training opinions | ~$140 at Sonnet 4.6 rates |
| Training run, A100 80 GB | ~1–2 h per run, well under $10 |
| Local smoke test | MPS, 200 windows |

## Layout

```
experiments_09022026/
├── readme.md                # findings this plan rests on
├── triage_plan.md           # this document
└── triage/
    ├── checklist.md         # data selection + seeder selection, step by step
    ├── inputs/              # exclusion list (tracked)
    ├── sample_clreplica.py  # runs in cl-django; keyword-targeted stratified sample
    ├── build_splits.py      # dev/test cluster split → inputs/splits.csv
    ├── make_annotator_data.py  # sampler output → benchmark-viewer root for the annotator
    ├── run_annotator.sh     # second benchmark-viewer instance on :8125
    ├── run_centralia.py     # centralia over every opinion (runs in flp/centralia env); auto-flags review
    ├── feed_centralia.py    # valid centralia readings → eyecite-tagged annotator payloads
    ├── passage_prompt.py    # PASSAGE-LEVEL seeder prompt (primary): labels exactly the encoder's input
    ├── prepare_passage_inputs.py  # one batch record per (passage, target)
    ├── seed_prompt.py       # whole-opinion compact seeder (optional pre-filter)
    ├── prompts/             # rendered prompt drafts (triage_seeder_v1.md)
    ├── tagged_text.py       # shared: revised_html / html_with_citations → tagged text + inventory
    ├── prepare_seed_inputs.py  # tagged text + inventory + paginated batch records
    ├── build_splits.py      # dev/test cluster split from the benchmark
    ├── build_windows.py     # mention windows + markers for gold and silver
    ├── seed_run.py          # seeder batch submit/collect (TODO)
    ├── train.py             # CaseLawModernBERT-large sequence classifier
    ├── eval.py              # pair-level metrics, slices, human-ceiling comparison
    ├── viewer/              # FastAPI review app (:8195)
    └── data/                # gitignored
```

## Schedule

| Week | Work |
|---|---|
| 1 | Sample export; annotator correction pass starts; dev/test split; seeder bake-off on dev |
| 1–2 | Window builder over gold groups; viewer v1; seeder prompt iteration to gate |
| 2 | Seeder iteration to gate; pilot seed (300); first training run; dev evaluation in the viewer |
| 3 | Seed to 1,500; model iteration and ablations; threshold selection |
| 4 | Single test evaluation; write-up; status-site update |

## Risks

- **Silver cannot exceed the seeder.** The gate on step 1 is what makes step 3
  meaningful; if the seeder cannot reach human-level recall on dev, fix that
  before training anything.
- **Extraction misses.** 7% of gold pairs have no eyecite-tagged mention and
  therefore no window. Tracked as a separate recall term; the citation
  extraction encoder is the fix.
- **Direct history is non-local.** Only 34% of DH evidence is within 500
  characters of a mention. The header helps; the structural route is the
  answer.
- **Positive scarcity outside SCOTUS.** FED and STATE gold positives are 17
  and 24 pairs; those slices are indicative only.
