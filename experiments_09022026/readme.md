# 0902 — Citator quality review: human ceiling, model gap, triage feasibility

Fresh-eyes review of the citator before running demo-site inference. Three
questions: how well do experts agree with each other, how close is the 0529
Sonnet single-stage model to that ceiling, and is a passage-level triage model
(small encoder gate → large model for flagged pairs) feasible on this data.

All numbers come from the `citator-benchmark` ledger as of 2026-07-30 (365
citing clusters, 10,863 pairs with a final treatment, 22,562 expert votes) and
the 0529 Sonnet predictions stored in that ledger. Reproduce with the three
scripts below; no LLM calls, no GPU.

| Script | Produces |
|---|---|
| `agreement.py` | human leave-one-out ceiling, model vs final, model vs human on the same votes, extraction misses |
| `quote_locality.py` | distance from expert evidence quote to nearest mention of the target case; cited-case density around the quote; opinion length vs 8K tokens |
| `mention_count.py` | within-opinion mention count vs treatment (gold coreference groups, 74 reviewed opinions) |

## 1. Human agreement (the ceiling)

Method: leave-one-out. Each expert vote is scored against the majority of the
*other* experts on the same pair. Scoring against the final would be biased
because each expert helps set the final. "As recognized by" labels are folded
onto their base form ("Affirmed as recognized by" → "Affirmed by") everywhere
except the category table.

| Human LOO | Value |
|---|---|
| Exact-label agreement | 95.1% |
| Severity-tier agreement | 96.6% |
| Agreement when the others say anything but Cited by | 56.6% (n=938) |
| Macro F1, 6 classes with ≥20 votes | 0.66 |

Per class (F1 / support): Cited by 0.98 / 19,803 · Reversed by 0.81 / 98 ·
Criticized by 0.67 / 49 · Distinguished by 0.54 / 450 · Overruled by 0.51 / 43
· Affirmed by 0.44 / 223.

By category (the held-out expert vs the others' majority):

| Category | Votes | Same category | Exact label | Exact given same category |
|---|---|---|---|---|
| Cited by | 19,803 | 97% | 97% | 100% |
| Direct History | 223 | 91% | 85% | 93% |
| Citing Reference | 545 | 65% | 62% | 95% |
| Related Reference | 170 | 4% | 4% | 100% |

Findings:

- **Disagreement lives at the gate.** Once experts agree a pair carries any
  treatment, they agree on the exact label 93–95% of the time. The confusions
  are Cited by ↔ Citing Reference (387 + 190 votes) and Cited by ↔ Related
  Reference (172 + 153). Direct History ↔ Citing Reference confusion is 8 votes
  in total.
- **Related Reference is not annotatable as defined.** When one expert marks
  "as recognized by", the others do so 4% of the time. Exclude it from
  headline metrics until the guidance is revised.
- **Macro F1 ceiling is ~0.66, not 1.0.** Any model target should be stated
  relative to this, and beating it materially is a label-leakage flag.

## 2. 0529 Sonnet vs the ceiling

Model vs final treatment, all 10,092 predicted pairs that have a final:

| Model vs final | Value |
|---|---|
| Exact agreement | 93.4% |
| Severity-tier agreement | 95.4% |
| Macro F1, 6 classes with ≥20 pairs | 0.54 |
| Agreement on unanimous pairs / majority pairs / adjudicated | 96.0% / 64.8% / 58.3% |

Apples-to-apples on the same 19,290 votes, both scored against the others'
majority:

| | Human LOO | 0529 Sonnet |
|---|---|---|
| Exact agreement | 0.952 | 0.945 |
| Macro F1 (6 classes) | 0.664 | 0.542 |
| Negative-tail agreement | 0.577 | 0.575 |
| Cited by / Distinguished / Affirmed / Overruled | 0.98 / 0.54 / 0.40 / 0.53 | 0.97 / 0.61 / 0.41 / 0.48 |
| Reversed / Criticized | 0.85 / 0.68 | 0.68 / 0.10 |

By category (F1): Direct History human 0.86 vs model 0.84; Citing Reference
0.54 vs 0.58; Related Reference 0.03 vs 0.20.

Findings:

- **The model is at the human ceiling on Cited by, Distinguished, Affirmed,
  Overruled, and on the Citing Reference category overall.** The macro F1 gap
  is almost entirely Criticized by (recall 0.07 vs human 0.90) and Reversed by.
- **False negative flags are the user-visible problem.** 4.7% of pairs experts
  call Cited by get a non-neutral label from the model: Distinguished 126,
  Cert. denied 82, Affirmed 75, Overruled 73, Modified 21, Reversed 19.
  Precision: Overruled 0.27, Affirmed 0.37, Cert. denied 0.06.
- **Direct History weakness is precision, not recall** (0.74 vs 0.96): the
  model treats cases that are not the decision under review as the case below.
- **Extraction misses 7.1%.** 771 finalized pairs have no prediction although
  the opinion was run; 733 of them are Cited by. Authorities lists built from
  model output alone are incomplete.
- **No model newer than Sonnet 4.6 has been evaluated.** The 0617 ablations
  varied input text, not the model.

## 3. Triage feasibility (encoder gate on passages)

Proposal under test: a ModernBERT-class model answers, per citation mention,
"does this passage negatively treat the marked case?"; only flagged pairs go
to a large model for the fine label. Three empirical checks on gold.

**Is the evidence near a mention of the target case?** For the 317 gold
non-Cited-by pairs with an expert quote, distance from the quote to the nearest
mention of the cited case (reporter cite or short party name) in the opinion
text:

| Category | Pairs | Within 500 chars | Beyond 8,000 chars |
|---|---|---|---|
| Citing Reference | 197 | 92% | 2% |
| Direct History | 62 | 34% | 27% |

(15% of quotes had no target mention my heuristic could find; 3.5% of quotes
were not locatable in the text.) Citing-reference treatments are local to the
mention. Direct history is global: the mention is in the opening posture, the
evidence is the disposition at the end. This matches the 0407 two-stage
result, where section-level classification held on citing references but
dropped Direct History F1 from 0.80 to 0.33.

**How many cited cases share the passage?** Within ±500 chars of a negative
quote: median 3 `<citedCase>` tags, 79% have ≥2, 44% have ≥4. Multiple cited
cases per passage is the norm, so the gate must be told which case it is
judging (entity markers around every mention of the target, other citations
left as text or replaced by a generic token).

**Does the opinion fit the context window?** 48% of the 383 benchmark opinions
are under 8K tokens whole; 25% under 4K. Mention-centered windows of 2–4K
tokens cover the 500-char locality of citing-reference evidence many times
over.

**Mention count vs treatment** (gold coreference groups, 74 reviewed opinions,
1,304 pairs, applied treatment):

| Mentions of the cited case | Pairs | P(any treatment) |
|---|---|---|
| 1 | 748 | 4.9% |
| 2 | 246 | 8.1% |
| 3–5 | 193 | 19.2% |
| 6–10 | 56 | 23.2% |
| 11+ | 61 | 26.2% |

Spearman ρ = 0.22. Median mentions: Cited by 1, Direct History 2, Citing
Reference 3. A "≥2 mentions" rule would drop 57% of pairs but lose 30% of
treatments, so mention count is a feature for the gate, not a gate. It is also
diagnostic of the model's errors: 67% of the model's false negative flags are
on single-mention cases, vs 31% of its true flags.

## 4. Implications

1. **Metric target.** Report macro F1 on classes with ≥20 examples against the
   human LOO value (0.66) on the same pairs, plus negative-flag precision and
   extraction recall. Drop Related Reference from the headline until the label
   is redefined.
2. **Direct History should be routed structurally, not classified from
   passages.** Whether the cited case is the decision under review is a
   docket/party fact (the appellate-chain linker already exists); the
   treatment is then the disposition, read from the caption and closing
   paragraphs. This removes the model's largest false-flag sources (Affirmed,
   Reversed on non-DH cases) and the one treatment family whose evidence is
   not local.
3. **Triage is feasible for citing-reference treatments.** Unit = (mention
   window, marked target case). Training pairs come from gold quotes (317) and
   Sonnet silver quotes at scale; every unflagged mention is a Cited-by
   example. Gold volume (579 non-Cited-by finals, 262 unanimous) is an
   evaluation set, not a 22-class training set, so the gate is binary or
   severity-tier and the fine label stays with a larger model.
4. **Triage does not fix extraction.** The 7% of pairs with no tagged mention
   never get a window. The extraction/coreference encoder is the same model
   family and should share the backbone.
5. **Cheapest quality lever first:** re-run the 0410 benchmark on a current
   frontier model before investing GPU time; the prompt has not been re-tested
   on a newer model since May.
6. **Demo-site inference should wait** for the configuration that comes out of
   the above; the inference run itself is the cheapest step.

## 5. Seeder model inventory (checked 2026-09-02)

Sources read today: the Claude models overview and pricing pages, the AWS
"supported Regions and models for batch inference" page, and the Bedrock
integration page. Bedrock's own pricing page did not render the current Claude
models, so Bedrock prices below are the Claude list prices, which Bedrock has
matched for Claude to date; the bake-off measures actual spend regardless.

| Model | List $/M in / out | Batch $/M in / out | Bedrock batch inference | Notes |
|---|---|---|---|---|
| Claude Fable 5.1 | 10 / 50 | 5 / 25 | not listed | rejects forced tool_choice; too costly for seeding |
| Claude Opus 5 | 5 / 25 | 2.50 / 12.50 | **yes** (us. profile, us-west-2) | strongest batch-eligible model on Bedrock; thinking on by default |
| Claude Sonnet 5 | 2 / 10 | 1 / 5 | not listed | cheaper and newer than 4.6, but first-party Batch API only |
| Claude Sonnet 4.6 | 3 / 15 | 1.50 / 7.50 | yes | incumbent; measured $0.093/case on the benchmark |
| Claude Opus 4.6 | 5 / 25 | 2.50 / 12.50 | yes | superseded by Opus 5 at the same price |
| Claude Haiku 4.5 | 1 / 5 | 0.50 / 2.50 | yes | 200K context; weakest on the March bake-off |
| Kimi K2.5 | ~0.6 / 3 (on-demand) | none listed | yes (single region) | current Stage 2 model; no batch price listed |

Facts that change the bake-off design:

- **Opus 5 and Sonnet 5 use the newer tokenizer** (introduced with Opus 4.7):
  about 30% more tokens for the same text. Opinion-token cost estimates built
  on Sonnet 4.6 counts need a 1.3x factor.
- **Sampling parameters are rejected** on Opus 5 and Sonnet 5 (400). The batch
  record builder sets `temperature` on every record; it must be omitted for
  those models. Forced `tool_choice` is accepted on both (only Fable 5.1
  rejects it). `anthropic_version: bedrock-2023-05-31` bodies are served by the
  Messages-API infrastructure for Opus 5 on Bedrock; confirm with a 100-record
  smoke batch before the dev run.
- **Opus 5 thinks by default.** Adaptive thinking adds output tokens; set
  `output_config.effort` to `low` or `medium` for a first pass, or disable
  thinking (allowed at effort `high` or below), and compare.
- **Sonnet 5 is not on Bedrock's batch list.** Using it means the first-party
  Batch API with an Anthropic API key, outside the existing S3 and IAM setup.
  Worth it only if Opus 5 on Bedrock does not clear the gate.

Shortlist for the dev-set bake-off (150 clusters, ~3,900 pairs):

| Candidate | Role | Est. per dev run | Est. for 1,620 training opinions |
|---|---|---|---|
| Sonnet 4.6 | incumbent | ~$14 | ~$150 |
| Opus 5, effort low or medium | strongest on Bedrock | ~$30–50 | ~$350–550 |
| Haiku 4.5 | cheapest full-opinion on Bedrock | ~$5 | ~$50 |

Estimates use the 0529 run's token profile (16.7K opinion tokens and ~7.4K
output tokens per record) with the 1.3x tokenizer factor on Opus 5. Sonnet 5
would land near ~$10 per dev run and ~$100 at scale if the first-party route is
taken.

Recommendation: run Sonnet 4.6 and Opus 5 on dev first, Haiku 4.5 only if cost
at scale turns out to matter more than recall. Decide on Sonnet 5 after seeing
whether Opus 5 clears the gate.

### Open-weight candidates first (added 2026-09-02, per Rachel)

Before any Anthropic model, try the open-weight models on Bedrock. Specs from
the AWS model cards and pricing page today:

| Model | Bedrock id | Context | Max output | Batch on Bedrock | On-demand $/M in / out |
|---|---|---|---|---|---|
| Kimi K2.5 | `moonshotai.kimi-k2.5` | 256K | **16K** | yes (us-west-2, in-region) | 0.60 / 3.00 (Flex tier 50% off) |
| Kimi K2 Thinking | `moonshot.kimi-k2-thinking` | — | — | yes | 0.60 / 2.50 |
| GLM 4.7 | `zai.glm-4.7` | 203K | **4K** | yes (us-west-2) | 0.60 / 2.20 |
| GLM 4.7 Flash | `zai.glm-4.7-flash` | — | — | yes | 0.07 / 0.40 |
| GLM 5 | `zai.glm-5` | 200K | 128K | **no** (on-demand only) | 1.00 / 3.20 |

What this means for a single-stage seeder run over whole opinions:

- **Kimi K2.5 is the natural first candidate.** It was the runner-up in the
  March bake-off and had the best treatment F1 in the April one; the pipeline
  already builds Kimi batch records (OpenAI-style messages, schema inlined in
  the prompt, JSON parsed from text). The 16K output cap is the constraint:
  Sonnet at a 16,384 cap silently truncated 7% of benchmark clusters, so Kimi
  pages must be smaller than Sonnet's, or the output must be compacted.
- **GLM 4.7's 4K output cap rules out the current output shape** (one entry
  per cited case with quote and rationale; 0529 averaged ~7.4K output tokens
  per record). It is only viable with a compact output: emit entries only for
  cases with a treatment beyond Cited by, everything unlisted is Cited by. That
  is the triage question itself, so it is worth one run in that shape.
- **GLM 5 has the headroom but no Bedrock batch.** It runs on-demand through
  the Converse path the pipeline already has, at roughly 1.5x Kimi's price.
- **Tool use.** None of these take the `tool_choice` path; all use the
  inlined-schema pattern. GLM records need a `--model` switch on the Kimi
  builder, nothing more; the body shape is the same.

Per dev run (150 clusters) the open-weight models land at about $3 to $6 on
demand, and roughly $50 to $70 for the 1,620-opinion seed. Order: Kimi K2.5
(paginated to fit 16K), GLM 5 on-demand, GLM 4.7 compact-output; Anthropic
models only if none clears the gate.

## 6. Training sample — run record (2026-09-02/03)

`triage/sample_clreplica.py` in the cl-django container, seed 20260902,
5 keyword + 1 control per cell, 54 cells, near-citation window 300 chars,
candidate cap 800 per (group, decade), 1,030 exclusions (benchmark + April
pool). Elapsed 2h38m. All 54 cells filled; no cell needed more than 650
candidates (SCOTUS 2020s), most filled within 100–250.

| | Value |
|---|---|
| Opinions selected | 323 unique (324 rows; one SCOTUS/2020s/short pick repeated, so that cell has 4 keyword picks) |
| Keyword / control | 269 / 54 |
| Authority pairs (CLReplica OpinionsCited) | 13,791 |
| Text | 22.0M chars; median 38K, p90 161K, max 783K |
| Cited clusters per opinion | median 24, p90 70, max 2,986 |
| Years | 1951–2026 |

Strong-term coverage over keyword picks: distinguish 131 (49%), overrule 90,
undermine 57, do-not-follow 45, decline-to-follow 32, abrogate 27, disapprove
24, superseded 20, repudiate 18, not-persuasive 16, criticize 16, cast-doubt
12, inapposite 11, call-into-question 10, then single digits. No term above
the 60% cap.

Spot check of 10 random keyword picks: in all 10 the strong term sits within
170 characters of a citation and refers to the treatment of *another* case
(abrogated on other grounds, declining to adopt, distinguishable from X,
criticized the trial court's use of Y, inapposite, distinguished the case
from Z). None were the opinion's own disposition.

Run-1 artefacts: the first run (control filter still requiring no
direct-history language) exported 307 opinion JSONs before it was stopped;
they carry no metadata row and are set aside in
`triage/data/triage_sample/opinion_html_run1_orphans/`. Recoverable with the
orphan-recovery path in `sample_clreplica_next.py` if more keyword opinions
are wanted.

Annotator instance: `triage/data/annotator/` (323 clusters, 13,791 authority
rows), served on :8125. Pilot seeder inputs prepared from eyecite grouping
as a placeholder: 323 clusters → 326 Kimi records (~6.6M input tokens);
regenerate after the annotation pass so gold grouping replaces eyecite.

## 7. Centralia pass over the sample (2026-09-03)

Every sampled opinion was re-extracted from its court PDF with
[centralia](https://github.com/freelawproject/centralia) (v0.0.6). PDF
source order: CourtListener storage (`local_path`), Harvard scan, court URL.
Where centralia's reading is `valid`, the annotator shows centralia's text
tagged by eyecite (case citations only, no cluster-id resolution); otherwise
the CourtListener `html_with_citations` stays.

| Status | Opinions | Meaning |
|---|---|---|
| valid | 95 | clean reading; fed into the annotator |
| review | 139 | readable but untrusted geometry (almost all Harvard scans with an OCR layer) |
| scanned | 54 | raster only |
| failed | 35 | 31 no PDF reachable, 2 courts unknown to centralia (okla, colo), 2 centralia crashes |

Every valid reading came from a born-digital court PDF; no Harvard scan
produced one. By decade: 0 valid before 1990, 4 in the 1990s, 9 in the
2000s, 34 of 54 in the 2010s, 48 of 53 in the 2020s. By court group: circuit
38 / 108, SCOTUS 30 / 107, state high 27 / 108.

eyecite finds fewer citation spans on centralia text than CL's html carries
because court PDFs do not print the parallel reporters (S. Ct., L. Ed. 2d)
that CL's Harvard-derived text includes; short-form and id. counts match.

Two centralia crashes worth reporting upstream: `render_hm_items:
Blockquote` in an attorneys block (cluster 4892150) and a PDF with no
`/Root` (pdfminer). The runner records both as failed and continues.

Files: `triage/data/centralia/{runs.csv, fed_back.csv, out/{cid}.json,
out/{cid}.review.html, pdf/}`; overrides carry `centralia: {status, …,
fed_back}` and `needs_centralia = status != valid` (228 flagged for review).

## 8. How common is the `Id.`-through-parenthetical bug in the sample? (2026-09-08)

`triage/id_paren_prevalence.py` (run from the `citation-tagger` env, eyecite 2.7.8) runs
eyecite over the 422 annotator opinions (323 train / 49 dev / 50 test) and flags every
`Id.`/`Ibid.` whose immediately preceding citation sits inside a signal parenthetical
that closes before the `Id.` (the pattern in eyecite issues #348/#349).

| | Id. cites | pattern hits | eyecite → inner cite | eyecite drops it | opinions touched |
|---|---|---|---|---|---|
| broad signals (citing, quoting, see, cf., accord, …) | 5,618 | 187 (3.3%) | 124 | 58 | 78 / 422 |
| `--tight` (citing/quoting only, outer antecedent is a case cite) | 5,618 | 101 (1.8%) | 73 | 26 | 56 / 422 |

The heuristic over-counts: spot-checking ten hits, about half are real mis-resolutions
(*Miranda*, *Roper*/*Graham*, *Sandstrom*, *Adarand* examples) and the rest are `Id.`
after a record cite or `Ante` (#349's pattern) or an `Id.` that genuinely means the inner
case. Realistic estimate: 1–2% of `Id.` cites, roughly one in ten opinions. The larger
number in the same run: 2,120 of 5,618 `Id.` cites (38%) are left unresolved by eyecite
for any reason. Per-opinion counts in `triage/data/id_paren_prevalence.csv`.

`--write-flags` merges the hits into each cluster's grouping override (`id_paren`), which
the annotator shows as an orange `N Id.` badge, a Records filter ("Id. flag" → flagged /
flagged, not reviewed) and a "Suspect Id. resolutions" panel on the opinion page with a
**find** button per hit and a "Mark Id. review done" toggle. Re-run after re-feeding text.

## 9. LLM pass 1 pilot: citation correction by Claude Opus subagents (2026-09-09)

12 opinions (10 train + dev 725046, 9621752), one Claude Opus 5 subagent each, prompt
`triage/prompts/triage_citation_fixer_v1.md`, harness `triage/citation_seed.py`
(`prepare` → numbered `<c id g>` inputs; `apply [--write]` → override fields + `by: llm`
rows in `citation_changes.csv`; `score` → vs the gold overrides). Dev inputs were built
with `--seed-state` (eyecite + script statute strips only).

| dev opinion | eyecite mention F1 | LLM mention F1 | eyecite coref F1 | LLM coref F1 | gold edits recovered | LLM edits gold disagrees with |
|---|---|---|---|---|---|---|
| 725046 (ca2 1996, CL html) | 0.88 | 0.98 | 0.76 | 1.00 | 13/13 removes, 15/16 regroups, 4/5 adds | 2 of 27 (an `Id.` after a Senate-report quote untagged; name-only "Lopez" tagged in a heading) |
| 9621752 (casd 2023, centralia text) | 0.81 | 1.00 | 1.00 | 1.00 | 17/17 removes, 1/1 add | 0 of 18 |

Names matched the gold on all 16 groups that had a gold name (trailing-period
differences only). Across the 12 opinions: 60 removes, 60 merges, 70 adds, 1 regroup,
345 names, 2 roles; every edit anchored (0 skipped after a fix for opinions with two
writings of the same type). All ten train opinions are written into the annotator's
overrides (backups in `triage/data/citation_seed/backups/`), so they render corrected on
:8125 and can be spot-checked.

**Wave 2 (2026-09-09, same day):** 50 more train opinions (`triage/data/citation_seed/wave2_ids.txt`,
stratified by text source × length, 3.5K–88K chars), prompt 09-09b (keep over-wide eyecite
spans). All 50 completed; edits: see the tally in the memory/checklist entry. Every edit
anchored (0 skipped). 60 train opinions now carry LLM-corrected grouping; the viewer shows
them under Records → Seeded. The Agent tool caps concurrent subagents at 20, so 7 of the
second wave had to be relaunched.

**eyecite vs seeded on the 60 train opinions** (`citation_seed.py report`, no gold;
per-opinion table `triage/data/citation_seed/eval/seed_delta.csv`):

| | eyecite | LLM-seeded | Δ |
|---|---|---|---|
| case mentions | 4,279 | 4,533 | +254 (502 added, 248 removed) |
| groups (distinct cases) | 2,271 | 1,760 | −511 (207 merges of split parallel cites) |
| singleton groups | 1,473 | 815 | −658 |
| Id./Ibid./supra mentions | 288 | 343 | +55 |
| Id.-like mentions alone in their group (unresolved) | 73 | 0 | −73 |
| groups with a case name | 0 | 1,760 | all |

Centralia-fed text (20 opinions) needed 20 structural edits per opinion, CL html (40) 14:
the Centralia side is dominated by merges (eyecite splits every parallel cite when it
cannot resolve to a cluster) and by unresolved `Id.` singletons (69 of the 73); the CL side
by removals (statutes eyecite tagged as cases in older opinions) and name-only adds.

**Verified-seeded check (2026-09-09, 19 train opinions Rachel verified after seeding):**
LLM edits vs her final state — mention F1 0.995 (eyecite 0.929), coref F1 1.00 (eyecite 0.966),
edit precision 0.994 (324 ok / 2 wrong); of her own further changes the LLM had made 71/108
removes, 170/170 regroups, 156/207 adds. Coref is inflated: she reviewed the LLM's grouping,
not eyecite's. The 90 disagreements (`triage/data/citation_seed/outputs/eval/diff.md`) fall
into five patterns, all folded into `prompts/triage_citation_fixer_v2.md`:

1. **Pins belong inside short-form / `Id.` / `supra` spans** (`Id., at 389`, `512 U.S. at 486-487`,
   `Williams, supra, at 536–538`); eyecite's truncated spans (`170 Idaho at `, `Id.,`) must be
   removed as `wrong_span` and re-added whole — 37 of the 37 missed removes + ~35 of the 51
   missed adds were exactly this pair.
2. **Blank-page slip cites are mentions** (`580 U. S. ----`, `549 U. S. ___ (2006)`, incl. the
   citing court's own cert grant) — agents had left them untagged on purpose.
3. **Names inside quotes / brackets / testimony count** (`[H. S. Cramer & Co.]`, `“his Miranda rights”`).
4. **First word of a full case name is not a mention** (`In Cox Broadcasting Corp. v. Cohn, 420 U.S.`) — 1 wrong add.
5. Gold is itself inconsistent on doctrine-like uses (`Miranda warnings` untagged vs `Miranda rights` tagged) — left as is.

**Dev iteration 1 (2026-09-09 14:56–15:15, prompt v2, all 49 dev opinions, `dev_iter1/`):**
pooled mention F1 0.968 (eyecite 0.875), coref F1 0.986 (eyecite 0.981), edit precision
0.843 (774 ok / 144 wrong, respan pairs credited); gold edits recovered 343/349 removes,
69/86 regroups, 328/382 adds. Gate (0.99/0.99) not met. Anchor fallbacks: 4 of 476 adds, so
not a harness artifact. The 144 disagreements: 103 adds the gold lacks (50 name-only, 23
Id./Ibid., 19 short forms — mostly untagged `301 U. S., at 587`-style short cites the dev gold
never added, 8 blank slip cites), 33 removes the gold keeps (Ibid./Id. after non-case quotes
that eyecite chained to a case, the opinion's own caption cite, Louisiana public-domain cites,
partial-name tags, two law reviews), 8 regroups. Gold-convention conflicts surfaced: the dev
gold keeps eyecite's truncated `Id.,` spans and does not tag `573 U.S. ----` slip cites
(the opposite of the 19 recently verified train opinions), and tags `Ante, at 1928` refs in a
dissent as mentions. Prompt v3 tightens the name-only rule in both directions, keeps caption
/ public-domain / partial-name tags, and narrows the non-case `Id.` exception.

**Dev iteration 2 (2026-09-09 17:37–18:30, prompt v3, `dev_iter2/`):** mention F1 0.964,
coref F1 0.986, edit precision 0.849 (732 ok / 130 wrong); gold edits recovered 340/349
removes, 71/86 regroups, 297/382 adds. Flat vs iteration 1: v3's tighter name-only rule cut
wrong adds (103→96) but cost 31 correct name-only adds (missed adds 54→85: `In Cenco, where
…`, `The Court acknowledged in Jackson Transit that …`, `Grable involved …` are all tagged
in the gold even when the case is cited by reporter nearby). Prompt v4 restores the v2
reading — only a name directly attached to its own citation is exempt — and excludes party
uses. New gold inconsistency: the opinion's own caption cite is kept in two dev opinions
and removed in two others. Iteration 3 (the last) runs at the 19:37 tick.

**Gold review (2026-09-09 evening):** Rachel decided all 149 LLM-vs-gold disagreements from
iterations 1–2 in the annotator's seed-diff flow — 73 "LLM is right" (applied to the gold),
68 "gold is right", 8 skipped — and tagged/untagged further spans while reviewing. Regroup
disagreements are now compared by co-membership rather than group id (the id compare had
produced phantom items). Re-scored on the corrected gold:

| iteration (prompt) | mention P / R / F1 | coref F1 | edit precision | gold edits recovered |
|---|---|---|---|---|
| eyecite floor | 0.873 / 0.849 / 0.861 | 0.975 | — | — |
| 1 (v2) | 0.993 / 0.985 / 0.989 | 0.994 | 0.966 (31 wrong) | 365/369 rm, 288/403 regroup, 412/448 add |
| 2 (v3) | 0.997 / 0.980 / 0.988 | 0.994 | 0.988 (10 wrong) | 364/369 rm, 292/403 regroup, 387/448 add |

Coref clears the 0.99 gate; mention F1 sits 0.001–0.002 under it, limited by recall (v3's
conservative name-only rule). v4 (v3 + v2's name-only recall) is queued for iteration 3.

Observations from the agents' notes: (1) eyecite spans routinely swallow pin cites and
leading names (`934 F.2d 440, 450`, `McMillan, 477 U.S. at 93`); the agents left them
alone, which matches the gold — the prompt should say so explicitly instead of the
"reporter token only" rule reading as a request to re-span; (2) reporter tokens split by a
page-break marker cannot be added verbatim; (3) merges of parallel citations were the
dominant fix on centralia-fed opinions, removals of record-cite `Id.`s on district-court
opinions. Cost is not tracked for subagents; ~70–90K tokens per opinion.

## Test-set run (prompt v4, one-time) — 50/50, 2026-09-10

Pooled over all 50 test opinions vs their (unreviewed) gold (`data/citation_seed/test_run/eval/`):

| | mention P | mention R | mention F1 | coref F1 | edit precision |
|---|---|---|---|---|---|
| eyecite baseline (test) | 0.895 | 0.869 | 0.882 | 0.971 | – |
| LLM v4 (test, 50) | 0.940 | 0.968 | 0.954 | 0.979 | 0.80 (956 ok / 240 wrong) |
| LLM v4 (dev, 49, gold after Rachel's review) | 0.997 | 0.992 | 0.994 | 0.994 | 0.989 (913 / 10) |

Gold edits recovered on test: removes 405/430, regroups 335/512, adds 425/538.
The 240 "wrong" edits: 135 name-only adds, 34 short-form/cite adds, 29 `Id.`
adds, 25 `Id.` regroups, 6 `Id.` removes, 3 supra adds. Missed gold edits: 53
odd-reporter/parallel cite adds (mostly the English reporters in 4630088 —
Keb., Str., Leach, Mod., Eng. Rep. — which eyecite never tagged and the model
declined to add), 47 name-only adds, 15 `Id.` removes, 12 `Id.` adds. Worst
opinions by wrong edits: 10345366 (48), 1722 (40), 118420 (19), 9622223 (14),
121162 (14). (At 45/50 the pooled numbers were 0.967 / 0.971 / 0.79; the last
five — 1722, 4630088, 121162, 145660, 1198927 — are long SCOTUS/state opinions
with many name-only references and pulled mention F1 down to 0.954.)

**Read this gap carefully.** Dev gold was reconciled with the LLM through the
seed-vs-gold review (Rachel decided 149 items from iters 1–2 and 65 from iter 3;
across all three rounds she accepted the LLM on ~80% of name-only mention adds).
Test gold has had no such pass, so most of the 135 "wrong" name-only adds on test
are the same gold-convention gap dev had before review, not a model regression.
The honest comparison for test is eyecite 0.882 → LLM 0.954 mention F1 (coref
0.971 → 0.979) on the same untouched gold. To get a reviewed test number, run the same seed-vs-gold
review on test (gold correction only — the prompt must not be tuned on it).

**Test seed-vs-gold review items written (2026-09-10):** `score … --write-review
--review-prompt triage_citation_fixer_v4` on the test run → 380 items across the
50 test opinions (wrong_add 201, missed_add 113, wrong_regroup 26, missed_remove 25, wrong_remove 11, missed_regroup 2, wrong_merge 2),
visible in the viewer under filter split=test + Seed diff, same step-through
flow as dev. Deciding "LLM is right" edits the test gold, so the 0.954 above is
the pre-review number; re-score after the review for the reviewed-gold number
and keep prompt v4/v5 changes independent of anything seen here.

**One-sided items auto-decided (Rachel's rule, 2026-09-10):** when only one side
tagged a span, the miss is almost always a recall failure, so `wrong_add` items
(LLM tagged, gold did not) were decided "LLM is right" (195, applied to gold)
and `missed_add` items (gold tagged, LLM did not) "gold is right" (107), via the
viewer's own `/api/seed_review/{cid}` endpoint, marked `auto` in the review
files. Remaining for manual review: 22 wrong regroups, 16 missed removes, 1
wrong remove, 1 wrong merge, 1 missed regroup. Re-scored vs the updated test
gold: mention P/R/F1 0.993/0.973/0.983, coref 0.984, edit precision 0.972
(eyecite 0.860 / 0.964). Caveat: the pre-auto state of the test
`grouping_overrides` was not backed up (my backup copied the wrong directory);
`seed_reviews` and `revised_html` pre-states are in
`data/citation_seed/backups/pre_auto_seed_decisions_20260910-152135/`.
Bug found by the auto run: the viewer's seed-review endpoint wrote the
server-side revised HTML as a list (`TypeError` → HTTP 500 after the decision
and overrides were already saved), so every "LLM is right" so far left the
saved `revised_html/{cid}.html` stale until the page re-exported it. Fixed in
`citator-benchmark/app.py` (shared `_revised_html_body`), and all 50 test
`revised_html` files were regenerated server-side (3,850 `<cite>` tags).

### Pattern in Rachel's iteration-3 decisions (65 of 66 decided: 26 LLM / 32 gold / 7 skip)

- **Name-only mentions: she wants them tagged.** 23 of 29 LLM name-only adds
  accepted ("In *New Mexico*, we considered", "The *Holley* court", "*Pennhurst*'s
  rule", "*Dole*'s limitations", "*Steward Machine*, found", "as the Court in
  *Grable* observed, 545 U. S., at 312", "*Bush v. Palm Beach*, ante, p. 70").
  Rejected name adds were (a) a case name used as a generic label for a
  procedure — "*Fatico* materials"; (b) a partial span carved out of a full
  citation — "*Gore*" in "Gore v. Harris, 772 So. 2d 1243"; (c) duplicate items
  for the same passage. And 9 of 10 `missed_add` name items went to gold, i.e.
  the LLM still under-recalls: possessives ("*Pennhurst*" ×3, "*Barnard*'s"),
  "*Drexel Furniture*" ×3, "(quoting *Steward Machine*, supra, at 590)",
  "*McCulloch*, supra, at 413", "*Boyle*, supra, at 508", "*Aro II*", OCR'd
  "*Ene*" (= Erie).
- **Short-form / odd-reporter cites the LLM leaves untagged**: public-domain
  "1998 ME 1", English reporters "10 Clark & Fin. 200" / "8 Eng.Rep. 718",
  OCR-damaged "365 TJ. S. 167", a parallel cite split by a page marker
  ("116 S.Ct. *65 938"), and short forms "772 So. 2d, at 1262" after a
  same-case `Id.` chain — all gold.
- **Removes**: gold kept "176 L. Ed. 2d, at 718" (LLM called it internal_ref)
  and removed "Railroad Commission"/"Clinic, supra," partial-name tags.

Prompt v5 candidates (for train seeding and any further dev iteration; v4 stays
frozen for the test number): (1) possessive and adjectival uses of a case name
("*Pennhurst*'s rule", "the *Drexel Furniture* tax") ARE mentions; only fixed
procedure labels are not (*Fatico*/*Daubert* hearing, *Miranda* warnings,
*Terry* stop, *Batson* challenge, *Rooker-Feldman* doctrine); (2) names inside
`(quoting X, supra, at N)` / `(citing X)` parentheticals are mentions; (3) tag
public-domain, English/early-American and OCR-mangled reporter cites when the
volume–reporter–page shape is recognizable; (4) never tag the first word of a
name that introduces its own full citation (the *Gore* error); (5) an `Id.`
chain is not broken by an intervening footnote marker or a record cite inside
a parenthetical (10345366 regroups).

### Model comparison — final (dev, 49 opinions, prompt v5, same inputs)

| model | mention P | mention R | mention F1 | coref F1 | edit precision | gold adds recovered | cost for the 49 dev |
|---|---|---|---|---|---|---|---|
| eyecite baseline | 0.872 | 0.842 | 0.857 | 0.975 | – | – | – |
| **Opus 5** (Bedrock batch) | 0.991 | 0.995 | **0.993** | 0.993 | 0.974 | 455/472 | ≈ $8 (at $5/$25 tier, batch) |
| Sonnet 4.6 (Bedrock batch) | 0.967 | 0.900 | 0.932 | 0.992 | 0.889 | 177/472 | ≈ $5 |
| GPT-5.6 Luna (OpenAI real-time) | 0.955 | 0.888 | 0.920 | 0.990 | 0.855 | 178/472 | $0.31 |
| Kimi K2.5 (Bedrock batch) | 0.883 | 0.834 | 0.858 | 0.984 | 0.573 | 118/468 | ≈ $1 |

Reading: coreference is essentially solved by every model (0.98–0.99) — the
differentiator is mention recall, i.e. finding the name-only references the
prompt's name sweep asks for. Only Opus does it (455 of 472 gold adds);
Sonnet and GPT find under 40% of them and Kimi is at the eyecite baseline with
a flood of wrong edits. GPT-5.6 is ~25× cheaper than Opus and lands 1.2 points
of mention F1 below Sonnet; neither is a substitute for Opus on this task at
the 0.99 bar. Per-model scores: `data/citation_seed/dev_iter4/score_v5_*.json`;
outputs in `data/citation_seed/{dev_opus_v5,dev_sonnet46,dev_gpt,dev_kimi}/`.

### GPT-5.6 prompt hardening — round 1 (2026-09-10 evening)

Goal: close the gap to Opus on dev with a cheaper model. Diff of the GPT v5 run
against gold, next to Opus on the same 49 opinions (MISSED = gold edit the
model did not make, WRONG = model edit gold rejects):

| class | GPT | Opus | what it is |
|---|---|---|---|
| MISSED add name | 120 | 9 | name-only references not tagged |
| MISSED add cite | 81 | 6 | untagged `volume reporter, at page` short forms, OCR/old-style full cites |
| MISSED add Id. | 73 | 1 | untagged `Id.`/`Ibid.` after case cites |
| WRONG remove cite | 24 | 0 | real reporter citations removed (`930 F.2d 63, 67-69`, `504 U.S. 555`) |
| WRONG remove Id. | 13 | 0 | `Ibid.`/`Id., at 507` after case cites removed |
| MISSED remove Id. | 24 | 0 | `Id.` after record cites (`ECF No. 17-1`) left tagged |
| WRONG add name | 33 | 8 | party names (`Giuseppe Pugliese`), first word of a name introducing its own cite (`Erie` in `Erie R. Co. v. Tompkins, 304 U.S.`) |
| WRONG add supra | 13 | 0 | `Name, supra` added when the following short form is already tagged |
| MISSED add supra | 20 | 1 | |

Mechanics behind it: GPT's visible output was ~1K tokens (Opus ~10K); it wrote
`<scan>`/`<name_sweep>` as a prose summary (median 9 lines), quoted 329
occurrences but emitted only 286 adds (Opus 515), and never walked the text
for `Id.`/pin tokens. **v6g** (`prompts/triage_citation_fixer_v6g.md`) keeps
v5's conventions and replaces the output procedure with: `<existing>` — one
line per existing tag (keep/remove/regroup/wrong_span, with a never-remove list
for reporter citations and post-case `Id.`); `<untagged>` — one line per
`Id.`/`Ibid.`, per `supra`, per `volume reporter at page` token and per case
short-name occurrence, each quoted with its preceding context and a verdict,
with a look-ahead check before every name add (introduces its own citation /
party-as-party / add) and the `Name, supra` + tagged short form rule; `<edits>`
derived one-for-one from those lines. Two arms launched real-time to separate
prompt from effort: `dev_gpt_v6g` (v6g, medium) and `dev_gpt_v5high` (v5, high).

### GPT-5.6 prompt hardening — results (dev 49, gold adds = 472)

| arm | prompt | effort | mention P | R | F1 | coref F1 | edit prec. | adds recovered | notes |
|---|---|---|---|---|---|---|---|---|---|
| baseline | v5 | medium | 0.955 | 0.888 | 0.920 | 0.990 | 0.855 | 178 | 49/49, 190 s, $0.31 |
| round 1 | v6g | medium | 0.927 | 0.902 | 0.914 | 0.963 | 0.798 | 210 | 47/49 (2 without an edits block); enumeration worked (339 adds) but GPT kept statutes/law reviews (never-remove overshoot: 44 missed removes), added name fragments/party uses (59 wrong name adds), 18 unanchored spans |
| effort only | v5 | **high** | 0.968 | 0.938 | **0.952** | 0.994 | 0.902 | 304 | 49/49; the largest single gain — GPT's misses are effort-bound |
| round 2 | v7g + post-filter | medium | 0.957 | 0.894 | 0.924 | 0.986 | 0.858 | 181 | 49/49, 223 s, $0.40; filter dropped 12 fragment/duplicate adds; still 135 missed name adds and 52 wrong ones — at medium effort the model skips most of what it enumerates, prompt wording moves F1 by <0.01 |
| round 3 | v7g + post-filter | **high** | 0.975 | 0.945 | **0.960** | 0.989 | **0.927** | 313 | 49/49, 696 s, ≈$0.81; wrong supra adds 9→0, wrong cite removes 10→3, missed supra 16→5 vs v5-high; regroups recovered 156 vs 208 (coref −0.005); name misses (92) and wrong name adds (35) unchanged — the residual class |
| round 4 | v8g + candidates + post-filter | **high** | 0.975 | 0.968 | **0.972** | 0.991 | **0.934** | **382** | 49/49, 564 s, 0.81M in / 0.47M out ≈ $0.73; missed names 92→43, missed Id. 23→11, wrong cite adds 9→1; residual = 43 missed names, 32 wrong name adds, 32 missed odd cites, 20 missed record-`Id.` removes |
| reference | v5 | Opus 5 batch | 0.991 | 0.995 | 0.993 | 0.993 | 0.974 | 455 | |

**Why the prompt alone stalls, and round 4.** Of the 94 name-only references
v7g-high missed, 84 never appeared in its own `<untagged>` enumeration — GPT
applies the conventions when it sees a candidate but does not *find* them
("As this Court held in Pugliese", "the LiMandri factors", "Garcia's Lopez
claim"). So round 4 moves the search out of the model: `triage/candidates.py`
derives each cited case's short names from the case-name phrase preceding its
tagged full citation (`United States v. Pugliese, <c>805 F.2d 1117</c>` →
`Pugliese`; handles `In re …`, `X's case`, a blank `— U.S. -` between name and
tag, sentence/signal-word boundaries, `&` in party names, hyphen-prefixed uses
like `post-Clearfield`; drops fragments that introduce their own citation;
stop-list of entity/geographic words), finds every occurrence in the untagged
prose, and adds every untagged `Id.`/`Ibid.`, `volume reporter, at page` and
`Name, supra` token with the nearest preceding group as a hint. Output: a
`<candidates>` block appended to the user message (`openai_batch.sh run
--candidates`); prompt **v8g** requires a verdict per candidate id
(`add → gX` / `skip: reason`) before the free sweep, and adds two gold
conventions both models were violating (a blank `— U.S. -` next to a tagged
parallel cite, and an untagged public-domain cite next to a tagged reporter
cite of the same decision, are not separate mentions). Dev: 1,090 candidates
(688 name / 221 Id. / 119 supra / 62 short form; median 4, max 235 per
opinion); the generator covers ≥64 of the 94 names v7g-high missed (the rest
are labels like `Harris I`, names whose citation eyecite never tagged such as
`M'Naghten`, or full-name references to `ante` companions).

v7g = v6g with the never-remove list restricted to court decisions (statutes,
codes, `Stat.`, regulations, law reviews explicitly mandatory removes), a
mechanical two-test name rule (fragment test incl. the last-word case
`United States v. Juarez-Ortega, 866 F.2d 747`; party test with a verb list),
blank slip cites added once, and exact-copy anchoring. Post-filter
(`openai_batch.py postfilter_adds`, on by default for `run`): drops an added
name that is immediately followed by a reporter cite / `<c` tag / docket / public-domain
cite, and dedupes identical adds. Runner bookkeeping made race-free (concurrent
runs used to clobber each other's `bedrock_jobs.json` entries).

### All systems on one scorer (dev 49, current gold, 2026-09-11)

Full write-up (specs, prices, caveats, next round): `model_comparison_dev.md`.

Adds the experiments_09092026 silver encoder (eyecite-labelled
CaseLawModernBERT-large, extraction → linking end to end) scored with this
harness via `score_encoder.py` (predicted spans re-anchored into the annotator
text; see the 09092026 readme for the pipeline).

| system | mention P | mention R | mention F1 | coref P | coref R | coref F1 |
|---|---|---|---|---|---|---|
| eyecite (as tagged in the annotator) | 0.872 | 0.842 | 0.857 | 0.996 | 0.955 | 0.975 |
| **silver encoder** (CaseLawModernBERT-large: extraction → linking, end to end, no gold mentions) | 0.967 | 0.866 | **0.914** | 0.970 | 0.898 | **0.933** |
| Kimi K2.5, prompt v5 | 0.883 | 0.834 | 0.858 | 0.985 | 0.983 | 0.984 |
| GPT-5.6 Luna, v5, medium effort | 0.955 | 0.888 | 0.920 | 0.991 | 0.990 | 0.990 |
| Sonnet 4.6, v5 | 0.967 | 0.900 | 0.932 | 0.992 | 0.993 | 0.992 |
| GPT-5.6 Luna, v8g + candidates, high effort | 0.975 | 0.968 | 0.972 | 0.987 | 0.994 | 0.991 |
| Opus 5, v5 | 0.991 | 0.995 | 0.993 | 0.988 | 0.997 | 0.993 |

### Cost: Bedrock batch with Opus for the next iteration and train seeding

Per opinion, one-shot (prompt v4 ≈ 3.2K tokens + opinion; measured over the 94
dev+test inputs/outputs): input avg ≈ 12.6K (max 113K), output avg ≈ 1.7K
(max 6.6K). The Claude Code subagents spent ~50–150K tokens per opinion because
of harness overhead; a direct batch call is ~8× cheaper on tokens.

| | opinions | input tok | output tok (+8K thinking) | cost @ Opus 4.5-tier batch ($2.50/$12.50 per M) | @ Opus 4.1-tier batch ($7.50/$37.50) |
|---|---|---|---|---|---|
| dev iteration | 49 | 0.63M | 0.08M (+0.39M) | $2 – $8 | $6 – $23 |
| remaining train | 263 | 3.3M | 0.45M (+2.1M) | $14 – $40 | $42 – $120 |
| full train (323) | 323 | 4.1M | 0.55M (+2.6M) | $17 – $50 | $50 – $145 |

Batch pricing is 50% of on-demand. Verify the current Opus list price on the
Bedrock pricing page before relying on the tier. Bedrock batch jobs require a
minimum of 100 records, so a 49-opinion dev iteration must be combined with a
train batch (or padded); jobs complete within 24h and read/write JSONL on S3.
### Bedrock batch runner (`triage/bedrock_batch.py`, wrapper `bedrock_batch.sh`) — 2026-09-10

Reuses the pipeline's batch plumbing (`citator-pipeline/utils/batch_utils.py`:
dev-env session, `CITATOR_S3_BUCKET` + `CITATOR_BATCH_ROLE_ARN`,
`submit_batch_job`, `check_job_status`, S3 helpers, per-run `manifest.json`)
exactly as `experiments_06172026/run_combined_batch.py` does; it only adds the
per-opinion record builder (system = fixer prompt, user = `inputs/{cid}.txt`)
and the writer for `{cid}.response.md` / `.edits.json`. Same env as the earlier
batch runs: `aws sso login --profile dev-env`, export the two `CITATOR_*` vars,
deps via `uv run --no-project --with boto3 --with json-repair --with pandas`.
S3 layout: `Citator/runs/{run_id}/citseed/{input.jsonl, raw/}` + manifest.

```bash
cd ai-research/experiments_09022026/triage
aws sso login --profile dev-env
export CITATOR_S3_BUCKET=… CITATOR_BATCH_ROLE_ARN=…          # as for run_batch.py
bash bedrock_batch.sh models                                  # Opus ids in us-west-2
export CITSEED_MODEL_ID=us.anthropic.<opus id>                # or pass --model to submit
python3 citation_seed.py pick --n 120                         # -> data/citation_seed/batch_<stamp>_ids.txt
python3 citation_seed.py prepare --ids $(cat data/citation_seed/batch_<stamp>_ids.txt)
bash bedrock_batch.sh export --name train_a --ids $(cat data/citation_seed/batch_<stamp>_ids.txt)   # prompt v5 default
bash bedrock_batch.sh submit --name train_a                   # upload + create job (>=100 records)
bash bedrock_batch.sh status                                  # or: wait --name train_a
bash bedrock_batch.sh fetch --name train_a                    # -> data/citation_seed/train_a/{cid}.response.md + .edits.json
python3 citation_seed.py apply --write --ids $(cat …ids.txt) --out-dir data/citation_seed/train_a   # train
python3 citation_seed.py score --ids $(cat data/citation_seed/dev_ids.txt) --out-dir data/citation_seed/dev_iter4   # dev
```

Records: `recordId` = cluster id; thinking `adaptive` for Opus 5 / 4.7+ /
Sonnet 5, `enabled` 8K budget otherwise, `--thinking none` → temperature 0;
`max_tokens` 16000. Local bookkeeping in `data/citation_seed/bedrock_jobs.json`
(ids, prompt sha, run_id, job ARN, status, per-record usage); raw output under
`data/citation_seed/batches/<name>.out/`; failed/unparseable records in
`batches/<name>.errors.json`, listed by `fetch` for re-export. A dev iteration
(49) must share a job with a train batch to clear the 100-record floor: export
both id lists under one name (use `--out-dir` and score/apply per id list).

**Runs so far (2026-09-10).** `train_a`: 120 train opinions, Opus 5
(`us.anthropic.claude-opus-5`, first use needed ~2 min of Bedrock model-access
setup), prompt v5, adaptive thinking, `max_tokens` 16000 → 80 ok / 40 failed,
all 40 with `stop_reason=max_tokens` (outputs ran 7.5K median, 15.8K max; the
scan + name sweep on long opinions overflowed). 1.54M in / 0.66M out tokens for
the job. The 80 were applied (`apply --write`, 0 skipped edits, backup
20260910-152041). `train_b`: 170 records = 47 remaining eligible train + 34 long
(>90K chars, `pick --max-chars 400000`) + the 40 `train_a` retries + the 49 dev
opinions (= dev iteration 4 on v5), `max_tokens` 32000, run_id
e11ea55a-f0c6-45fa-8a3f-e1dfbb4cf811. After fetch: `apply --write` the train
ids, `score` the dev ids (`--out-dir data/citation_seed/train_b`). That
exhausts the train pool (323 = 122 seeded/verified before + 120 + 47 + 34).
Fixes along the way: `fetch` skips S3 folder placeholders; `pick` skips ids
already in `bedrock_jobs.json`.

`train_b` fetched 2026-09-10: 135 ok / 35 failed, all 35 `stop_reason=max_tokens`
at 32000 — these are the giants (120K–164K input tokens; successful outputs ran
10.6K median / 31.6K max). Dev 48/49 (809122 failed), train 87/121 applied
(`apply --write`, backup 20260910-155046; a few skipped edits on 4 clusters).
3.63M in / 1.72M out tokens. Still needing inference: 34 long train + 809122,
which need a 64K output cap (or a terser output format for very long opinions).

**Dev iteration 4 = Opus 5 batch, prompt v5, same 48 dev opinions as the
subagent comparison** (`data/citation_seed/dev_iter4/`): mention P/R/F1
0.989/0.995/0.992, coref 0.992, edit precision 0.969 (741 ok / 24 wrong) vs the
subagent v4 run on the same 48: 0.996/0.994/0.995, coref 0.993, precision 0.987.
Batch mode matches the subagent runs within noise on mention/coref F1; v5's
name sweep trades ~14 extra wrong adds for +1 recall point. Batch is the
runner going forward.

### Model comparison on dev (prompt v5) — submitted 2026-09-10

Goal: same prompt, cheaper models, scored on the same 49 dev opinions as the
Opus 5 batch run (dev iteration 4). Batch minimums researched: **Bedrock**
enforces a platform-wide minimum of 100 records per job (the "Minimum number of
records per batch inference job" quota; 1 GB per file, 5 GB per job; many jobs
may run concurrently) — it is not per-model. **OpenAI Batch API** has no minimum
(50,000 requests / 200 MB max, 50% discount, 24h window, `/v1/chat/completions`
and `/v1/responses` supported). Padding for Bedrock jobs: the 49 dev ids plus
the 60 pilot/wave-2 train opinions in their pre-seed inputs
(`data/citation_seed/pad_v1seeded_train_ids.txt`) = 109 records; the pad
outputs are kept (they double as a v5 re-seed candidate set) but only the dev
ids are scored.

| batch | model | status |
|---|---|---|
| Opus 5 (train_b dev part + 809122 from opus_retry, assembled in `data/citation_seed/dev_opus_v5/`) | `us.anthropic.claude-opus-5` | **done 49/49**: mention P/R/F1 **0.991 / 0.995 / 0.993**, coref 0.993, edit precision 0.974 (903 ok / 24 wrong); gold adds recovered 455/472 |
| dev_kimi | `moonshotai.kimi-k2.5` (chat format, prompt prepended to the user turn, temperature 0, max_tokens 128,000) | **done** 108/109 (1 record — 127912 — degenerated into a repetition loop until the 128K cap, no edits block): dev mention P/R/F1 **0.883 / 0.834 / 0.858**, coref 0.984, edit precision 0.573 (433 ok / 323 wrong); gold adds recovered 118/468, removes 223/364, regroups 118/415. That is no better than eyecite itself (0.857) with a large volume of wrong edits — Kimi is not usable for this task. 1.44M in / 1.07M out tokens. |
| dev_sonnet46 | `us.anthropic.claude-sonnet-4-6` (thinking 8K budget, max_tokens 128,000) | **done** 109/109 (2 malformed-JSON responses salvaged with `json_repair`, now the parser fallback): dev mention P/R/F1 **0.967 / 0.900 / 0.932**, coref 0.992, edit precision 0.889 (569 ok / 71 wrong); gold adds recovered 177/472 vs Opus 399/414 — the gap is recall on name-only mentions, exactly the class v5 targets. 1.59M in / 0.72M out tokens. |
| opus_retry | `us.anthropic.claude-opus-5`, max_tokens 128,000; 35 train_b failures + 60 pad + 5 smallest dev = 100 records | **done** 100/100, 3.67M in / 2.63M out tokens. The giants' outputs ran 54K median / 103K max tokens, so nothing below the 128K ceiling would have worked. 34 train giants applied (backup 20260910-164015; 1–5 skipped edits on 4 clusters). Dev 809122 now scored (with the 5 repeats: 0.998 mention F1, precision 1.0). The 60 pad outputs are on disk in `data/citation_seed/opus_retry/` and **not applied** (v5 re-seed decision pending). |
| dev_gpt | `gpt-5.6-luna` via OpenAI, **real-time** (`openai_batch.sh run`, 8 concurrent, reasoning_effort medium, max_completion_tokens 128,000; the batch submission sat at 0/49 for 2.5 h and was cancelled) | **done** 49/49 in 190 s, 0.66M in / 0.15M out tokens (82K reasoning), **$0.31**: dev mention P/R/F1 **0.955 / 0.888 / 0.920**, coref 0.990, edit precision 0.855 (597 ok / 101 wrong); gold adds recovered 178/472, regroups 110/415 |

Sonnet 5 is **not** batch-enabled on Bedrock in us-west-2 (rejected under
`us.`, plain and `global.` ids with "Batch inference is not supported for the
requested model"), so the Sonnet arm is 4.6. `openai_batch.py` mirrors
`bedrock_batch.py` (export/submit/status/wait/fetch, same output layout and
`bedrock_jobs.json` bookkeeping, `provider: openai`), uses
`reasoning_effort` (default medium) and `max_completion_tokens` 32000; client
pattern from `experiments_04012026/utils/gpt_utils.py`. Scoring after fetch:
`python3 citation_seed.py score --ids $(cat data/citation_seed/dev_ids.txt) --out-dir data/citation_seed/<batch>`.

**Output cap policy (2026-09-10).** `max_tokens` is a required field in the
Anthropic Messages body (and Bedrock's Kimi body), so it cannot be omitted;
the exporter now defaults it to the model's hard ceiling so truncation cannot
recur. Ceilings probed with an oversized `InvokeModel` request (the validation
error names the limit): Opus 5 128,000; Sonnet 4.6 128,000; Kimi K2.5 262,144
(`MODEL_MAX_TOKENS` in `bedrock_batch.py`; `--max-tokens` still overrides).
Re-exporting an existing batch name now clears its previous run_id/job so the
resubmission gets a fresh S3 prefix and `fetch` cannot mix in a stopped job's
partial output.

**Cost so far and on-demand comparison** (215 successful Opus records:
5.17M input / 2.37M output tokens; ~19–27K in and 8–13K out per opinion;
the 35 giants average 60K input each). At the Opus 4.5 price tier
($5 / $25 per M on-demand, half that in batch): spent ≈ $43 in batch (would be
$85 on-demand), i.e. $0.20 / $0.40 per opinion; the 35 giants ≈ $33 batch /
$65 on-demand; a 49-opinion dev pass ≈ $8 / $15. If Opus 5 is priced at the
Opus 4.1 tier ($15 / $75) multiply by three. Real-time inference is exactly 2×
batch on every platform and has no record minimum, but needs its own runner
(the pipeline's `converse_completion` is tool-use based); batch turnaround here
has been 15–30 min, so real-time buys little.

**What else needs inference (2026-09-10 16:30):** nothing beyond the jobs in
the table — the 35 giants are in `opus_retry`; the 60 pilot/wave-2 opinions get
their Opus v5 re-seed from the same job's pad; GPT-5.6 on dev is blocked on
`$OPENAI_KEY` + model id; test stays on the frozen v4 run. The passage-level
treatment pass (stage 2) is a separate prompt and has not started.

### Prompt v5 (`prompts/triage_citation_fixer_v5.md`)

Built from the iteration-3 decisions and the test diff. Changes from v4: a
mandatory `<name_sweep>` step (for every inventory case, list every untagged
occurrence of its short name, every `supra`, every `volume reporter, at page`
short form) — name-only recall is the dominant miss; possessive, adjectival,
roman-numeral (`Bush I`), hyphenated and OCR-split forms spelled out; `supra`
inside `(quoting …)`/`(citing …)` parentheticals is a mention; the
procedure-label exception (`Fatico hearing`, `Terry stop`) applies only when
the decision is never cited in the opinion; never tag the first word of a
name that introduces its own citation; public-domain, English/early
reporters, OCR-damaged and page-marker-split citations are mentions; `Id.`
chains survive footnote calls and parenthetical record cites; a parallel pin
after an internal cross-reference stays. v4 remains the frozen prompt for the
test-set number; v5 is for train seeding and any further dev iteration.

### Passage-level treatment seeding on dev/test — Opus 5 (2026-09-11)

`triage/seed_passages.py` (+ `seed_passages.sh` wrapper) is the `seed_run.py` the
checklist called for. Passages are pulled from the LIVE annotator viewer
(`/api/passages/{cid}` on :8125) so ids `{cid}:{group}:{i}`, text and text hash
are exactly what the Passages page shows; the model gets the passage prompt
(`prompts/triage_passage_seeder_v1.md`) as the system prompt and one
`<passage>` per record (Anthropic Messages body, adaptive thinking, no sampling
params, `max_tokens` 6000; Bedrock `recordId` = short `pNNNNN`, mapped in
`windows.jsonl`). `fetch` parses the JSON object (json-repair fallback, prose
before it kept as `rationale`), `apply --write` fills the `seed` slot of
`data/annotator/data/passage_reviews/{cid}.json` (reviews untouched, per-file
backups under `data/seed_passages/backups/`), `score` reports pair-level
P/R/F1 vs the benchmark finals (Citing Reference pairs; DH and "as recognized by"
excluded; any `treatment` passage = positive pair; exact-treatment rate on hits).

Prepared 2026-09-11 (`data/seed_passages/opus_dev`, `opus_test`):

| run | clusters | groups | passages | est. input tokens | est. cost (in only, $2.50/M) |
|---|---|---|---|---|---|
| opus_dev | 49 | 1,150 | 1,763 | ~10.7M | ~$27 |
| opus_test | 50 | 1,513 | 2,359 | ~14.6M | ~$37 |

Gold coverage of the prepared passages: dev 889/918 gold pairs, 74/75 Citing
Reference positives; test 1,081/1,104, 49/51 (the rest have no cited cluster id
in the verified grouping — 230 dev / 411 test passages carry `cited_cluster_id
null` and are seeded but unscorable). Test cluster 110212 is not `cluster_done`;
its passages come from the current grouping anyway. Output cost adds roughly
$20–40 per run with adaptive thinking; total for both ≈ $100–130.

Status: live 1-record smoke OK (5,811 in / 367 out tokens incl. 312 thinking,
7 s, valid JSON) → both runs SUBMITTED 2026-09-11 16:27 PDT as Opus 5 Bedrock
batch jobs (`opus_dev` run d906f241 / job hh586bwe2x65; `opus_test` run
7752280d / job qpfwpv8m7ky6; bookkeeping `data/seed_passages/bedrock_jobs.json`).
Measured cost projection from the smoke ≈ $85 for both. The SSO token had
expired while the CLI still held cached role credentials, so `seed_passages.py`
honours env credentials (`eval "$(aws configure export-credentials --profile
dev-env --format env)"`) by overriding the pipeline's hard-coded `dev-env`
session. Next: `status`/`fetch` → `apply --write` → `score`.

**Results (fetched + applied 2026-09-11 ~16:45 PDT, both jobs finished in < 20 min):**

| run | records ok | tokens in / out | cost (batch) | seed labels (cited_by / treatment / posture / not_about_target) | flagged |
|---|---|---|---|---|---|
| opus_dev | 1,763 / 1,763 | 10.62M / 0.32M | ≈ $31 | 1,450 / 251 / 57 / 5 | 14.2% |
| opus_test | 2,359 / 2,359 | 14.55M / 0.45M | ≈ $42 | 1,916 / 380 / 57 / 6 | 16.1% |

Pair-level vs benchmark finals (Citing Reference pairs; DH and "as recognized by"
excluded; a pair is positive when any of its passages is seeded `treatment`):

| split | gold positives | predicted positives | P | R | F1 | exact treatment on hits | gold pairs uncovered |
|---|---|---|---|---|---|---|---|
| dev | 57 | 104 | 0.413 | 0.754 | 0.534 | 34 / 43 | 28 (DH 18 excluded) |
| test | 34 | 130 | 0.238 | 0.912 | 0.378 | 24 / 31 | 18 (DH 17 excluded) |

Against the seeder gate (recall ≥ 0.80, precision ≥ 0.55, ≤ 15% flagged): dev
misses on recall (0.754) and precision; test clears recall (0.912) but precision
is 0.24. For reference the human leave-one-out binary F1 is 0.64 (P 0.57 /
R 0.74), so Opus recall is at or above one human's, precision is well below.
Seeds are UNREVIEWED; the pair gold itself is the noisy majority label. All
4,122 seeds are in `data/annotator/data/passage_reviews/` (viewer step 3 on
:8125; no human review existed, nothing overwritten). Next: Rachel reviews
seeded passages (start with the `treatment` seeds on dev), then re-score with
the passage-level reviews as gold instead of the pair finals.

**Viewer: seed-vs-gold filter for the passage review (2026-09-14).** On the
Passages page every citation group now shows whether its pair-level LLM seed
(most severe `treatment` seed over the group's passages) matches the expert
majority final; `g` / "≠ gold only" filters to the disagreements (and to the
passages carrying them), "Next opinion ≠ gold" walks the Records order, and
Records → citing has a "Seed ≠ gold" column + filter. Each group also shows a
gold box: the majority / gold final, recognized treatments, review state, every
expert / tiebreak / triple-review vote and the model vote (source
`citator-benchmark/data/treatments.csv`, finals topped up from
`treatments_most_negative.csv`; joined on cited cluster id, else `cited_ref`).
On the Opus seeds: dev 32 opinions with at least one disagreeing group.
Details in the citator-benchmark readme (Passages step).

**Passage context + evidence underline fixes (2026-09-14, Rachel's review).**
`build_windows.windows_for_group` no longer counts context in paragraphs alone:
CourtListener sets string cites and headings as their own blocks, so "± 1
paragraph" was often ± 40 chars. Now the ± 1 paragraphs are widened block by
block until each side holds ≥ 600 chars (`MIN_CONTEXT_CHARS`), never crossing
into another writing or over the 4,000-char cap; when the block is over the cap
the neighbours are trimmed sentence-aligned (`_trim_context`, ≥ 300 chars a
side or dropped) instead of being discarded; only a single paragraph over the
cap is split into chunks. Effect on the live viewer passages (dev / test):
mention within 150 chars of the passage start 14% → 4% / 11% → 4%; within 200
chars of the end 18% → 7% / 12% → 6%; median context before the first mention
≈ 1,070 chars, after the last ≈ 1,100–1,300; median passage 2,875 / 3,191 chars
(was 2,411 / 3,028). Passage counts 1,763 → 1,794 and 2,359 → 2,376. The Opus
seeds were produced on the OLD windows: 1,068 dev / 1,204 test passages now
show the "text changed" badge, 45 / 37 new passage ids have no seed, 14 / 20
old ids no longer render. Re-seeding on the new windows would be a new model
run (≈ $90) — pending Rachel's decision. The viewer's evidence underline
(`passages.html`) now matches the seed's `evidence` over the whole passage
including the [T] mentions (it used to search only between them, so 150 of the
175 verbatim dev evidences were never underlined), normalises quotes / dashes /
whitespace, and treats "..." / "…" as fragment separators. Seeds carry
`evidence` (all 251 dev / 380 test treatment seeds; 175 / 252 verbatim) but no
written rationale: the prompt's "reason briefly" went into Opus's thinking
block, which Bedrock batch does not return.
