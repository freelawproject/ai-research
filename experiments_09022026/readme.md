# 0902 — Citator quality review and citation-seeding triage

Before the citator was run against real CourtListener opinions for a demo
site, three questions needed answering: how much experts actually agree with
each other on treatment labels, how close the production model (0529 Sonnet) gets
to that ceiling, and whether a cheap encoder could triage most opinions so an
expensive model only looks at the passages that matter. That review turned up
a bigger problem — eyecite's citation extraction and coreference have a real
error rate, and any model built on top of it inherits those errors — so most
of this experiment turned into building an LLM-seeded correction/training
pipeline for extraction and coreference, which feeds the encoder work in
`experiments_09092026`.

## What was tried

1. **Quality review** (`agreement.py`, `quote_locality.py`, `mention_count.py`,
   no LLM calls) — measured human agreement, scored 0529 Sonnet against it,
   and checked three empirical preconditions for a passage-level triage gate.
2. **Training-sample pipeline** — sampled ~1,600 opinions from CLReplica
   (`triage/sampling/`), re-extracted them from court PDFs with centralia to
   check for text-quality issues, and built a second annotator-viewer instance
   over the sample (`triage/annotator/`).
3. **LLM citation-seeding (pass 1)** — iteratively prompted, then batch-ran,
   several models to correct eyecite's citation extraction + coreference on
   every opinion before anything is trained on it (`triage/lib/citation_seed.py`,
   `triage/runners/`, prompts v1→v8g). This is most of the file below because
   it's where the real findings are.
4. **Passage-level treatment seeding (pass 2)** — had Opus 5 label individual
   passages (not whole opinions) as the candidate encoder-gate signal
   (`triage/runners/seed_passages.py`).

Process detail (exact commands, run-by-run logs, prompt diffs) lives in
`docs/checklist.md`, `docs/citation_seed_design.md`, `docs/seed_loop_protocol.md`,
and `model_comparison_dev.md`. This file is the narrative and the conclusions.

## Findings & conclusions

### 1. Human agreement is not 100%

Leave-one-out (each expert scored against the *other* experts' majority, not
the final label, which would be circular): exact-label agreement 95.1%,
severity-tier 96.6%, macro F1 (6 classes with ≥20 votes) **0.66**. Any model
target has to be read relative to 0.66, not 1.0 — and a model that scores
materially above it is a label-leakage flag, not a win.

The disagreement lives almost entirely at the gate (does this pair carry a
treatment at all), not in picking the exact label once experts agree
something is there (93–95% exact-label agreement conditional on agreeing
there's a treatment). "As recognized by" turned out not to be annotatable as
defined — experts agree on it only 4% of the time — so it was dropped from
every headline number going forward.

### 2. 0529 Sonnet is at the human ceiling on most classes; the gap is two specific treatments

Scored apples-to-apples against the same votes as the human LOO number:
macro F1 0.54 vs the human 0.66. But that gap is not spread evenly — Sonnet
matches or beats the human ceiling on Cited by, Distinguished, Affirmed,
Overruled, and Citing Reference overall. The whole macro-F1 gap is
concentrated in **Criticized by** (model recall 0.07 vs human 0.90) and
**Reversed by**.

The more user-visible problem: 4.7% of pairs experts call plain "Cited by"
get a non-neutral label from the model (mostly Distinguished, Cert. denied,
Affirmed, Overruled) — false-treatment flags, not missed ones. Separately,
citation extraction itself misses 7.1% of finalized pairs entirely (no
prediction at all), so any authority list built from model output alone is
incomplete regardless of label quality. That extraction gap is what led to
the citation-seeding work below.

### 3. A passage-level triage gate is feasible for citing-reference treatments, not for direct history

Three checks against gold data: (a) citing-reference evidence sits within 500
characters of a mention of the target case 92% of the time — local, so a
mention-centered window works; direct-history evidence is global (the mention
is in the opening posture, the evidence is the closing disposition) — a
passage-level gate structurally can't see it. (b) Multiple cited cases
routinely share a passage (median 3 within ±500 chars of a treatment quote),
so the gate has to be told *which* case it's judging, not just "is there a
treatment nearby". (c) Mention count correlates with treatment likelihood but
too weakly to be a gate on its own (Spearman ρ=0.22) — it's a feature, not a
filter.

**Conclusion:** route direct history structurally (it's a docket/party fact —
the appellate-chain linker already answers "is this the case below" — then
read the disposition from the caption/closing, not from a passage classifier).
Build the passage-level gate only for citing-reference treatments. Triage
does not fix the 7% extraction gap either way — that has to be solved
upstream, by the same encoder that does extraction (`experiments_09092026`).

### 4. Eyecite's extraction/coreference errors are real enough to need correcting before training on them

Any encoder trained on eyecite-labeled text inherits eyecite's mistakes as
silver labels. The specific failure mode: eyecite frequently drops the
pin cite and leading name from a span, splits parallel citations of the same
case into separate unlinked groups, and fails to resolve `Id.`/`Ibid.` after a
citation sitting inside a signal parenthetical ("citing/quoting X, ...") —
filed upstream as eyecite issues #348 and #349. On a 422-opinion sample, this
last pattern hits 1–2% of `Id.` citations, roughly one in ten opinions; more
broadly, eyecite leaves 38% of `Id.` citations unresolved for any reason.

So an LLM correction pass was built (`citation_seed.py`) that reads eyecite's
tagged text plus an inventory of the cases it found, and emits an edit list
(remove/regroup/add/rename) rather than re-deriving structure from scratch —
this keeps the edit auditable against eyecite's baseline and cheap to apply.

### 5. Model choice for the correction pass: only Opus 5 clears the bar; GPT-5.6 Luna gets close with the right scaffolding

After 4 rounds of prompt hardening (v1→v5, driven by two full rounds of
reviewing every LLM/gold disagreement) and a controlled comparison of
5 models on the same 49 dev opinions, same prompt, same gold:

| model | mention F1 | coref F1 | edit precision | cost (49 opinions) |
|---|---|---|---|---|
| eyecite baseline (no LLM) | 0.857 | 0.975 | – | – |
| **Opus 5** | **0.993** | 0.993 | 0.974 | ≈ $8 (batch) |
| Sonnet 4.6 | 0.932 | 0.992 | 0.889 | ≈ $5 |
| GPT-5.6 Luna (`gpt-5.6-luna`), v5 prompt | 0.920 | 0.990 | 0.855 | $0.31 |
| Kimi K2.5 | 0.858 | 0.984 | 0.573 | ≈ $1 |

Coreference is essentially solved by every model (0.98–0.99) — the
differentiator is mention *recall*: finding name-only case references
("*Pennhurst*'s rule", "the *Grable* Court") that never get a citation of
their own. Only Opus found nearly all of them (455/472 gold adds); Sonnet and
GPT-5.6 Luna found under 40%; Kimi matched the eyecite floor with a flood of wrong
edits and is not usable for this task.

GPT-5.6 Luna was worth pushing on because it's ~25× cheaper than Opus. Diagnosing
*why* it missed names (it wasn't ignoring its own conventions — 84 of 94
missed names never appeared in its own scratch enumeration; it wasn't finding
the candidates) led to pulling that search out of the model entirely:
`triage/lib/candidates.py` mechanically derives every case's short names from
the full citation it's attached to, finds every occurrence in the untagged
text, and hands GPT-5.6 Luna a `<candidates>` block to accept/reject rather than
discover from scratch. Combined with high reasoning effort, that closed most
of the gap:

| GPT-5.6 Luna variant | mention F1 | edit precision | notes |
|---|---|---|---|
| v5 prompt, medium effort (original) | 0.920 | 0.855 | baseline |
| v5 prompt, high effort | 0.952 | 0.902 | effort alone is most of the gain |
| v8g prompt + candidates, high effort | **0.972** | **0.934** | production choice for round-2 seeding |

**Decision:** Opus 5 batch is the reference/gold-quality seeder (used for the
train opinions in this experiment's own annotator). GPT-5.6 Luna v8g + candidates
is the cost-viable seeder at scale (used for the 10K-opinion round in
`experiments_09092026` — 9,994/10,000 seeded for $79.94, see that readme for
the run record). At 1M opinions in batch that gap becomes stark: **≈ $7,500–8,000
for GPT-5.6 Luna v8g vs ≈ $200,000+ for Opus 5** (Opus's exact rate depends on
an unverified Bedrock pricing tier — could be 3× higher; full breakdown in
`model_comparison_dev.md` §5), for 97% of Opus's mention F1.

### 6. Held-out test set, scored once

Prompt v4 was frozen before the test set was touched and never adjusted on it;
the LLM-vs-gold review that raised the *dev* gold applies only partially to test
(one-sided auto-decisions, 41 items open). On 2026-09-16 the test set was
scored once for every system on the same current gold — GPT-5.6 Luna run for
this on Bedrock Converse (50/50, ≈ $1), Opus from the frozen v4 outputs:

| system (test, 50 opinions, 3,854 gold mentions) | mention P | mention R | mention F1 | coref P | coref R | coref F1 |
|---|---|---|---|---|---|---|
| eyecite (floor) | 0.895 | 0.827 | 0.860 | 0.996 | 0.935 | 0.964 |
| encoder r2 warm (lwin 64) | 0.956 | 0.963 | 0.960 | 0.947 | 0.937 | 0.942 |
| encoder r4 (lwin 128, no rule) | 0.956 | 0.963 | 0.960 | 0.958 | 0.928 | 0.943 |
| **encoder r4 + `Id.`→nearest rule** | 0.956 | 0.963 | **0.960** | 0.966 | 0.952 | **0.959** |
| GPT-5.6 Luna v8g + candidates, high (Bedrock Converse) | 0.968 | 0.940 | 0.954 | 0.995 | 0.993 | **0.994** |
| Opus 5 v4 (frozen 2026-09-10 outputs, re-scored) | 0.993 | 0.973 | **0.983** | 0.988 | 0.981 | 0.984 |

Encoder rows are `experiments_09092026`'s; that readme carries the isolated
linking numbers and the final recommendation. The read: the encoder is the
extraction winner on held-out data (+10 over eyecite, ahead of the GPT pass it
was trained from); the GPT pass is the coreference winner (0.994, inheriting
eyecite's grouping); Opus is the only system above 0.98 on extraction.

### 7. One end-to-end comparison, all systems, one scorer

`model_comparison_dev.md` has the full write-up (exact specs, prices,
caveats). Headline, all scored the same way on the same 49 dev opinions
against the current gold:

| system | mention F1 | coref F1 |
|---|---|---|
| eyecite (no LLM) | 0.857 | 0.975 |
| round-1 silver encoder (`experiments_09092026`, no gold mentions) | 0.914 | 0.933 |
| Kimi K2.5 | 0.858 | 0.984 |
| GPT-5.6 Luna, v5 | 0.920 | 0.990 |
| Sonnet 4.6, v5 | 0.932 | 0.992 |
| round-2 cold encoder (GPT-seeded train, cold start) | 0.969 | 0.963 |
| GPT-5.6 Luna, v8g + candidates | 0.972 | 0.991 |
| round-2 warm encoder (GPT-seeded train, warm-started from round 1) | 0.973 | 0.961 |
| round-4 encoder (lwin 128) + `Id.`→nearest decode rule | 0.973 | 0.985 |
| Opus 5, v5 | 0.993 | 0.993 |

The trained encoder started as a reasonable floor (round 1: beats eyecite and
Kimi cold, without ever seeing gold mention spans at inference) and, once
trained on GPT-seeded labels (round 2), essentially **closes the gap to
GPT-5.6 Luna's mention F1**; round 4 plus a decode-time `Id.`→nearest rule
then took coref from 0.961 to **0.985** — ahead of eyecite (0.975), within
0.006 of GPT-5.6 Luna. Full results, the negative round-3 result and the
`Id.` error analysis behind the rule are in `experiments_09092026`.

**Viewer:** `triage/dev_compare_viewer/` (`uv run uvicorn app:app --port 8240`)
shows the current systems — eyecite as stored in CourtListener, eyecite re-run
on plain text, GPT-5.6 v8g, Opus 5, the round-4 encoder — layered over one copy
of each dev opinion with the gold as the reference, one row per mention locus with each system's verdict (missed /
false positive / wrong group / ok), and this table recomputed live from the
files on disk. `compare.py` in that directory is the same comparison as a CLI.

**The eyecite floor is CourtListener's stored markup, not the library alone.**
`State(cid, seed_state=True)` reads `html_with_citations` and groups spans by
the opinion cluster each one resolves to, so parallel citations of the same case
land in one group. Running eyecite on the same plain text and grouping with
`resolve_citations` instead gives a separate resource per reporter:

| eyecite, as measured | mention F1 | coref F1 | miss | fp | grouping |
|---|---|---|---|---|---|
| stored CL markup, dev | 0.857 | 0.975 | 467 | 364 | 243 |
| re-run on plain text, dev | 0.817 | 0.545 | 516 | 579 | 1,348 |
| stored CL markup, test | 0.860 | 0.964 | 667 | 372 | 375 |
| re-run on plain text, test | 0.789 | 0.643 | 868 | 728 | 1,425 |

97% of the plain-text row's grouping errors are groups that are too small. The
extraction drop is two offsetting effects: it finds short-form, `Id.` and `supra`
spans the stored markup never marked (195 of the stored row's 467 dev misses),
and it adds statute and journal spans that the case-only gold does not want.
So the database lookup, not the text-level resolver, is what makes the measured
floor's coreference precision 0.996.

### 8. Passage-level treatment seeding (stage 2): promising recall, not enough precision yet

Had Opus 5 label 4,122 individual passages (dev + test) as
treatment/cited-by/posture/not-about-target, then scored pair-level against
the benchmark's expert-majority finals:

| split | precision | recall | F1 |
|---|---|---|---|
| dev | 0.41 | 0.75 | 0.53 |
| test | 0.24 | 0.91 | 0.38 |

Against the target gate (recall ≥0.80, precision ≥0.55, ≤15% flagged): test
clears recall but precision is far below bar; dev misses recall too. For
reference, the human leave-one-out binary F1 on the same kind of call is
0.64 — so Opus's recall is at or above one human's, but its precision isn't
close. **This gate is not cleared.** The seeds are unreviewed (the pair gold
itself is a noisy majority label, not verified per-passage), so the next step
is to review the seeded passages by hand and rescore against that instead of
the noisy pair finals — not yet done.

## Current status

- Citation-seeding pass 1 (extraction/coreference correction): done for this
  experiment's own training sample; the production run at scale (10K
  opinions, GPT-5.6 Luna v8g) lives in `experiments_09092026`, which also has
  the trained encoder — on held-out test it beats GPT-5.6 Luna on extraction (0.960 vs 0.954) and trails it on coreference (0.959 vs 0.994); see §6.
- Passage-level treatment seeding (pass 2): seeded, not reviewed. Gate not
  cleared on the noisy pair-level baseline.
- Six clusters failed GPT-5.6 Luna seeding at scale (output-length/content-filter) and
  were dropped rather than retried — see `experiments_09092026` readme.

## Next steps

1. Review the seeded passages (start with dev `treatment` seeds) and rescore
   the gate against reviewed labels instead of the noisy pair majority.
2. Decide whether to retry the 6 failed clusters from the round-2 GPT-5.6 Luna run.
3. Improve the round-2 encoder's coreference — it's now the weaker half
   relative to GPT-5.6 Luna, not extraction. Prepared 2026-09-18 (see
   `experiments_09092026/readme.md` §Round 5 preparation): eyecite's grouping as
   a linker input channel, the reporter-key decode rule, and disagreement-targeted
   Opus adjudication of the round-2 labels (`triage/analysis/disagreement_*.py`).
4. Re-run the original 0410 single-stage benchmark on a current frontier
   model before investing further GPU time on the two-stage architecture —
   the existing benchmark prompt hasn't been tested on a model newer than
   Sonnet 4.6.

## Layout

```
triage/
  lib/        shared modules: tagged_text, build_windows, candidates,
              passage_prompt, seed_prompt, citation_seed (extraction+coref
              correction engine — prepare/apply/score/report/pick)
  sampling/   CLReplica sampling + dev/test split construction
  runners/    batch/real-time runners (Bedrock + OpenAI) and their .sh wrappers
  analysis/   score_encoder.py, id_paren_prevalence.py; decode_rules_dev.py (merge rules on the
              encoder's clusters), disagreement_dev.py (GPT vs encoder vs gold on dev),
              disagreement_select.py (renders Opus adjudication inputs for a root; results
              and the plan live in experiments_09092026/readme.md)
  dev_compare_viewer/  FastAPI viewer (:8240): all four systems vs gold on the 49 dev
              opinions — five layers over one text, per-locus verdicts, pooled table;
              compare.py is the same logic as a CLI (see its README)
  annotator/  second annotator-viewer instance over this experiment's sample
  centralia/  PDF re-extraction pass (centralia) over the sample
  docs/       checklist, design notes, seed-loop protocol, eyecite issue drafts
  data/, inputs/, prompts/   gitignored data / tracked cluster-id lists / prompt drafts
              (triage_citation_fixer_v5d = v5 + a <disagreements> block with a mandatory
              per-line <adjudicate> verdict; bedrock_batch export / citation_seed apply take
              --inputs-dir for those inputs)
```

Companion docs: `model_comparison_dev.md` (full model comparison), `triage_plan.md`
(original scope-and-file plan, historical).
