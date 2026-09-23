# dev_compare_viewer — four citation systems against gold, on the dev set

Side-by-side view of every system that has been run on the 49 triage dev
opinions, scored with the one scorer and laid over one copy of the text.

| layer | what it is | source |
|---|---|---|
| gold | the annotator's verified state (the reference) | `../data/annotator/` overrides |
| eyecite, stored CL markup | the seed state everything else edits: CourtListener's stored `html_with_citations` spans, grouped by the opinion cluster each span resolves to | `State(cid, seed_state=True)` |
| eyecite re-run on plain text | the same library pointed at the same text today, grouped by its own `resolve_citations` | `EyeciteFreshState` in `compare.py` |
| GPT-5.6 v8g | prompt v8g + candidate generator, reasoning effort high | `../data/citation_seed/dev_gpt_v8g_high/` |
| Opus 5 v5 | prompt v5 | `../data/citation_seed/dev_opus_v5/` |
| encoder r4 | round-4 extraction + linking + the `Id.`→nearest decode rule | `../../experiments_09092026/data/output/pred_gold_dev_r4_rule.json` |

Nothing is written. The pooled table on the index page is computed from the
files on disk at boot and reproduces `../../model_comparison_dev.md`
(eyecite 0.857 / 0.975 · GPT 0.972 / 0.991 · Opus 0.993 / 0.993 ·
encoder 0.973 / 0.985).

**The two eyecite rows measure different things.** The `eyecite, stored CL markup`
row is the floor the other systems edit, and its grouping comes from
CourtListener resolving each span to an opinion cluster id, not from the
library: parallel citations of one case share a cluster and so share a group.
The `eyecite re-run on plain text` row removes that database lookup and uses
`resolve_citations` instead, which makes a separate resource per reporter. On
dev that moves coreference F1 from 0.975 to 0.545, with 97% of its grouping
errors being groups that are too small. Extraction moves 0.857 to 0.817: it
recovers short-form, `Id.` and `supra` spans the stored markup never marked, and
adds statute and journal spans the case-only gold does not want. The row is
labeled with the eyecite version the venv resolved, so the number stays
attributable; 2.7.6 and 2.7.8 give the same aggregate.

## Run

```bash
cd experiments_09022026/triage/dev_compare_viewer
uv run uvicorn app:app --port 8240        # http://127.0.0.1:8240/
```

Boot takes ~20 s: every opinion is compared once and cached, so the pages are
instant afterwards. Restart to pick up new outputs on disk.

Environment overrides:

- `DEVCMP_PRED` — a different encoder prediction file (e.g. `pred_gold_dev_r4_norule.json`
  to see what the `Id.` rule buys, or `pred_gold_dev_warm.json` for round 2).
- `DEVCMP_IDS` — a different id list (default `../data/citation_seed/dev_ids.txt`).
- `DEVCMP_EXTRA=sonnet,kimi,gpt_v5` — add the other rows measured in
  `model_comparison_dev.md` as further layers and columns.

## What the pages show

**Index** — the pooled scores, then one row per opinion with each system's
`miss` / `fp` / `grp` counts. Sort by most disagreement to start where the
systems differ most.

**Opinion** — the text with gold as background shading and one 2px stripe per
system underneath:

- indigo shading = gold tagged it and every system agrees
- amber shading = gold tagged it and at least one system missed it
- red shading = gold does **not** have it and some system tagged it
- a stripe is present when that system tagged the span

The right panel lists **loci**. A locus is a gold mention, or a run of
overlapping mentions that no system matched to gold, and each system gets a
verdict at it:

| verdict | meaning |
|---|---|
| ok | tagged it, and its coreference group matches gold |
| wrong group | tagged it, but grouped it differently than gold (shows its group id and mate counts) |
| missed | gold tagged it, this system did not |
| false pos. | this system tagged it, gold did not |
| — | neither gold nor this system tagged it (only on false-positive loci) |

Filter by verdict kind, and by which system has to be the wrong one. Click a
span or a locus to see, per system, the group it was put in and that group's
other mentions (click one to jump to it).

Grouping counts read high next to the coref F1 because one wrong group flags
every mention inside it, while the F1 is pairwise.

Keys: `0` gold shading · `1`–`4` each system's stripe · `d` differences only ·
`j`/`k` loci · `n`/`p` opinions. Filters and toggles persist in `localStorage`.

## CLI

`compare.py` holds the comparison logic and runs standalone:

```bash
python3 compare.py                 # the pooled table over all 49 dev opinions
python3 compare.py --cid 101625    # one opinion, locus by locus
python3 compare.py --cid 101625 --json
```
