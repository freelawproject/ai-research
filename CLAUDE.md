# CLAUDE.md

## Project Overview

Legal citator pipeline that uses LLMs to analyze appellate court opinions and classify how each cited case is treated (e.g., reversed, distinguished, overruled). Data comes from the CourtListener (CLReplica) Django database. Opinions are pre-processed with `<citedCase>` tags marking citation spans.

## Repository Structure

- `data/` — Shared data files (opinion texts, metadata CSVs, expert labels). Experiment folders symlink here instead of duplicating data. Excluded from git.
- `citator-pipeline/` — Reusable pipeline code (instructions, utils, run scripts). Created after experiments showed Sonnet and Kimi as strong contenders, to consolidate the pipeline and track version changes via git. No data lives here.
- `experiments_MMDD2026/` — Each folder contains the data and results for a specific experiment. Before 0405, each experiment folder also contained its own utils and run scripts. From 0405 onward, all code lives in `citator-pipeline/` and experiment folders contain only data and thin wrapper scripts. New experiments should symlink directly to `data/`.
- `prior_experiments/` — Archived experiments. Everything dated before 0501 lives here, alongside the older pre-2026 folders. Paths inside them keep working: 0402–0405 still share data through legacy symlinks in `prior_experiments/experiments_04022026/data/`.

### Recent Experiments

Everything dated before 0501 has been archived under `prior_experiments/`; the
entries are kept here because later experiments still cite their results.

- `0319` — Baseline evaluation of v225, v318, and v320 instruction versions on 8 expert-annotated cases using Sonnet. v318 improved severity and treatment F1 over v225. v320 was a sidetrack (attempted cost savings by grouping "Cited by" outputs, unsuccessful).
- `0327` — Tested 17 cheaper LLM models on 8 expert-annotated cases for citation extraction and negative treatment detection. Concluded Sonnet 4.6 is the best performer, Kimi K2.5 is a close second.
- `0401` — Compared 5 models on 8 expert-annotated cases using v318 instructions. Sonnet 4.6 was the best overall; Kimi K2.5 had the best treatment F1.
- `0402` — Queried CLReplica DB for ~50 sampled circuit court opinions per circuit (13 circuits, 661 total). This data is shared by later experiments via symlinks.
- `0403` — Built the full pipeline: pagination for long opinions, post-processing (dedup, keyword flagging), and re-evaluation using a second model. Developed v403 prediction instructions and v404 re-evaluation prompt. Kimi K2.5 re-evaluator boosted direction F1 from 0.84 to 0.96 at +6% cost.
- `0404` — Adapted the pipeline for per-court production runs with parameterized `--court` flag. Added "Vacated by" flagging, citation normalization for label matching, and treatment cleanup.
- `0405` — Moved to AWS Bedrock batch inference for running at scale. Experiment folder restructured to be data-only, with code in `citator-pipeline/`.
- `0407` — Two-stage pipeline: Haiku extraction + Kimi classification with section context. Enhanced Kimi classifier prompt with cert denied pattern recognition, citation signals, and implicit distinguishing. Treatment F1 0.54→0.66 macro, direction F1 0.87→0.92, 77% cheaper than 0403 ($0.048 vs $0.210/case). **Recommended production configuration.**
- `0408` — Added Sonnet re-evaluator with section context to the two-stage pipeline. Did not improve quality (treatment F1 0.66→0.64) and added 156% cost overhead. Conclusion: re-evaluation is not beneficial when the Kimi classifier prompt is well-tuned.
- `0518` — First end-to-end batch evaluation of the production two-stage pipeline on the full 0410 benchmark (383 citing clusters, 39 courts). Treatment macro F1 0.38, Cohen's κ 0.58, QWK 0.70. Measured cost $0.047/case batch. Hardened `clean_treatments` canonicalizer; added `derive_severity_direction` postprocess step; added `Modified by` + `Reversed in part; Vacated in part by` to taxonomy (TREATMENT_RANK now 22 entries). DH vs Other split: v305 (Sonnet) wins on Direct History, 0518 wins on Other — motivated the 0529 prompt rewrite that combined both strengths into one single-stage pipeline.
- `0529` — Single-stage Sonnet (`citator` prompt, with kimi_classifier-style structured blocks ported in) on the same 383-cluster 0410 benchmark. Treatment macro F1 0.47, κ 0.71, QWK 0.79 — beats 0518 on every agreement metric. Coverage 327/383 clusters (vs 0518's 321). Cost $35.54 batch ($0.093/case). Built `submit-sonnet` / `collect-sonnet` / `run-sonnet` Bedrock batch path; built optional Kimi K2.5 re-evaluator (`submit-reeval` / `collect-reeval` / `run-reeval`) but found it **net-negative on this Sonnet base** — the improved prompt subsumes most of what reeval was fixing on 0528. Also fixed two latent bugs: `merge_to_labels` empty-citation phantom matches, and `SONNET_MAX_TOKENS = 16384` silent truncation (raised to 64000 = Sonnet 4.6 ceiling). **Best-performing setup measured so far; candidate for adoption pending further validation.**
- `0617` — Gold citation tagging + two-class prompt, over the **55 completed** cases of the reviewed `citator-benchmark` (cases with `treatments_reviewed=true` and a `revised_html`). Both arms run on the gold, de-duplicated opinion text (`revised_html` → `<citedCase>` tags via `convert_revised_html.py`) in **one** Bedrock batch (55×2 = 110 records, clears the ≥100 floor): Exp 1 uses the unchanged `citator` prompt (tests gold input vs the 0529 baseline); Exp 2 uses the new `citator_v2` prompt (tests the prompt change, input held constant). `citator_v2` adds (vs `citator`): opinion-type segmentation (the opinion's `<section data-opinion-type>` wrappers become `<lead>`/`<concurrence>`/`<dissent>`/`<combined>`/`<trialcourt>` tags so `opinionType` comes from the enclosing tag); the two-class treatment model (one `appliedTreatment` + 0+ `recognizedTreatments`, mirrored severity; no `appliedCaseHistory`, lean definitions — applied = the disposition when on appeal else an analytical label, no Direct History/Citing Reference/Related Reference jargon); an injected `<citingCase name/court/year>` identity; reporter→court-hierarchy reasoning; and an injected `<citedCaseInventory>` (the authoritative authority list — id/name/court/citation, name+court from `cited_cases.csv`, no year) whose `id` **equals the `group="N"` on the gold `<citedCase>` tags**, so the model returns one entry per inventory `id` (`inventoryId`) and eval joins on `cited_ref` exactly (1037/1043 gold joinable). The converter keeps `group` on the tags (removes only `change`); the Exp 1 arm strips them to bare `<citedCase>` at submit time. All **additive** (canonical `citator`/`tool_spec` untouched). New combined batch path `run_combined_batch.py` (+ `prepare_combined_jsonl`, `build_citator_v2_record`, `build_inventory.py`); two-class parse/postprocess in `v2_postprocess.py`; eval in `eval_gold.py` (Exp 1 vs 0410 + reviewed gold via citation match; Exp 2 applied + recognized-set F1 vs gold via cited_ref join). NOTE: gold text is not prose-identical to 0529's (only sub-opinions reviewed when a combined opinion also exists), so Exp 1 vs 0529 conflates tagging + cleaner text. Prep + eval harness validated offline; inference pending.
- `0902` — Citator quality triage on the reviewed benchmark (annotator viewer on :8125, dev 49 / test 50 / train pool). LLM citation-seeding pass (`triage/lib/citation_seed.py` prepare/apply/score/report/pick; prompts `triage/prompts/triage_citation_fixer_v1…v5.md`, v4 frozen for the test number, v5 = name-only recall hardening) corrects eyecite extraction + coreference before treatment seeding; dev iterations gated at mention/coref F1 ≥ 0.99; test (v4, unreviewed gold) mention F1 0.882 → 0.954, coref 0.971 → 0.979. Train seeding runs as Bedrock batch via `triage/runners/bedrock_batch.sh`, which reuses `citator-pipeline/utils/batch_utils.py` (dev-env, `CITATOR_S3_BUCKET`/`CITATOR_BATCH_ROLE_ARN`, ≥100 records/job). eyecite bugs filed as freelawproject/eyecite#348 and #349.
- `0909` — eyecite-seeded citation extraction + coreference on CaseLawModernBERT-large: sample ~20K CLReplica opinions (`corpus/sample_cl_20k.py`, Django shell in `cl-django`), build silver labels from `html_with_citations` (case-only; unresolved spans = singletons) + gold eval from the 0902 triage annotator's verified dev/test revised HTML, train the ported Exp 3 harness (tag-based splits, `large-caselaw` backbone) on a pod (`model/run_runpod.sh`). Full run done 2026-09-11 (13,304 train clusters): extraction on gold_dev span F1 0.870 (P 0.911 / R 0.833) vs eyecite floor 0.877 (P 0.858 / R 0.896) — the model sheds eyecite's statute/record false positives but inherits its recall ceiling; linking B³ 0.947. Apples-to-apples on the triage scorer (dev 49): encoder 0.914 mention F1 / 0.933 coref vs Opus 0.993 / GPT-5.6 v8g 0.972 — table in `experiments_09022026/model_comparison_dev.md`. Round 2 (2026-09-11→14) = `corpus/sample_cl_20k_round2.py` (10K new clusters, negative-signal oversampling, no overlap with prior data) seeded with GPT-5.6 v8g via the OpenAI Batch API (9,994/10,000 ok, $79.94, 7.3 h) through the 0902 harness pointed at `data/annotator_r2/` (`CITSEED_ANNOT`/`CITSEED_OUT`); labels exported with `corpus/export_r2_revised_html.sh`, built by `corpus/build_dataset.py --gpt-train-*` (splits `train`/`validation` + `train_high_quality`), packed by `model/make_pod_bundle.sh R2=1`; `run_runpod.sh MODE=warm|cold` = warm start from the silver checkpoints (`--init`) vs cold control. Pod run pending. Rounds 2–4 ran 2026-09-15/16 (warm start wins extraction; lwin 128 + chunk recompute; `Id.`→nearest decode rule) and gold_test was scored once: encoder mention F1 0.960 vs eyecite 0.860, coref 0.959 vs 0.964 vs GPT v8g 0.994 → encoder = production extractor, coref not yet. **2026-09-18 (built, not run):** round 5 = eyecite's own grouping as a linker input channel (`corpus/mention_features.py --payload-dir` → `eyecite_gid`; `model/train_linking.py --eyecite-feats --eyecite-dropout`; padded warm start from r4; `model/run_runpod.sh MODE=r5link`, `EYE=0` control; `R5=1 make_pod_bundle.sh`); offline dev studies in `experiments_09022026/triage/analysis/` — `decode_rules_dev.py` (reporter-key rule +0.002 dev coref, 0 harmful merges; eyecite `Id.` resolution harmful) and `disagreement_dev.py` (a GPT↔encoder disagreement flags 82% of GPT's false positives / 71% of misses; oracle adjudication 0.972→0.993 mention F1); `disagreement_select.py` + `prompts/triage_citation_fixer_v5d.md` render disagreement-targeted Opus adjudication inputs for the round-2 labels. **Folder layout (2026-09-23):** `corpus/` builds the labelled dataset (sample → seed → export → build), `model/` holds training and inference for both models, `results/` keeps each run's eval reports and trainer state; `data/` and `runs/` (the checkpoint landing zone) are gitignored. Weights and corpus are published as private Hugging Face repos under `freelawproject` — `caselaw-citation-{corpus,extractor,linker,warmstart}`; `experiments_09092026/results/README.md` maps run to repo.
- `0911` — Two-stage citator on the 382-cluster reviewed benchmark (149 dev / 233 test): a cheap GATE (Kimi K2.5 batch, GPT-5.6 Luna via Bedrock Converse) flags cited cases the opinion treats as more than merely cited; a strong LABELER (Sonnet 4.6, Opus 5) names the exact Citing Reference treatment for flagged cases only (+ `Cited by`, `Other treatment`). Direct history and "as recognized by" out of scope. Inputs: CL html (centralia text for 88 clusters), citation grouping corrected for all 382 (99 verified gold + 283 via the triage GPT-5.6 v8g fixer through `seed_citations.sh`). Scored vs the benchmark's final treatment AND vs any expert vote (`score.py`); baseline = 0529 Sonnet on the same pairs. Harness: `run_stage.py` (prepare/submit/fetch/run/parse per named run), `build_inputs.py`, `records.py`. **Gate result (dev, 2026-09-14):** frozen gate = `prompts/gate_v4.md` on GPT-5.6 Luna at reasoning effort high, two passes unioned at ≥ medium confidence → recall 0.82 of majority positives at 9.5% of pairs flagged (0529 single-stage Sonnet: 0.63 at 5.9%); Kimi K2.5 dropped (0.52–0.58); GPT rejects temperature on Bedrock and has no fine-tuning path. **Test (once, 2026-09-14):** gate recall 0.82 @ 10.7% flagged; gate→Opus 5 lifts Distinguished recall 0.58→0.79 and finds Criticized (0.04→0.79) at lower precision (false-flag 6.4% vs 4.5%), 3-class macro F1 0.63 vs 0.53, ≈$0.086/opinion at batch rates vs $0.093; gate→Sonnet halves false flags (2.4%) at 0529's recall but drops 12% of flagged ids. labeler_v2 (base rate, evidence rule, tighter definitions) → **recommended pipeline = gate → Sonnet 4.6 / labeler_v2**: test accuracy 0.969 vs 0.940, false-flag 1.7% vs 4.5%, Distinguished 0.59/0.66 vs 0.54/0.58, 3-class macro F1 0.559 vs 0.533, $0.053 vs $0.093/opinion; gate → Opus 5 / labeler_v1 is the recall configuration (0.81 of positives, 6.4% false flags). Direct history must come from the structural route; dissent/concurrence convention undecided. **2026-09-16:** `prompts/gate_v5.md` (v4 verdict rules + case-on-appeal identification: inventory id / citation / lower court / docket number, and the opinion's disposition in the Direct History taxonomy) with `score.py` direct-history block (`direct_history.csv`; v4 bare ids identify 0.82 dev / 0.71 test of gold DH pairs, 0529 disposition exact 0.84–0.86) — dev run DONE 2026-09-16 (`g5_gpt_dev`+`g5b_gpt_dev`, $3.07): verdict recall 0.842 @ 10.6% (v4 0.822 @ 9.5%, noise); case on appeal 48/49 identified, disposition tier-exact 1.00, label-exact 0.75 where all 12 misses are gold "Reversed by" on opinions whose decretal text says reversed AND remanded; trial-court orders (~30/149) come back Other/None → v5.1 needs a label. **Related references (dev run DONE 2026-09-16):** `gate_v6` marks ids carrying another court's reported treatment (+17% gate tokens, verdicts/DH unchanged, routes 32/36 gold RR pairs); `labeler_v3` returns two-class output (`appliedTreatment` + `recognizedTreatments`, role-aware input; `run_stage.py prepare --from-gate` routes flagged ∪ recognized ids, `--no-recognized` to disable); first Bedrock-batch labeler run (130 opinions, $7.30) detects 32/36 majority-gold related references at 0.94 label exactness (0529 25/36), applied track unchanged; "precision" 0.21 vs any-expert is mostly the gold not recording cert-denied/affirmed strings (guideline question). **TEST run 2026-09-17** (g6 ×2 $5.66 + labeler_v3 batch $11.93, 206 opinions): DH 86/88 identified, exact 0.80 / tier 0.98 / QWK 0.95 (0529 66/77, 0.89, 1.00); RR 23/30 detected, base exact 0.91 (0529 14/30); CR with v3 on v6 = 0.971 / QWK .545 / flagged .57 / ff 1.4% ≈ v2 on v4; ≈ $0.063/opinion batch. `ceiling_tracks.py` = per-track human LOO ceiling at the tier (CR test QWK 0.49 / positives flagged 0.71 / ff 2.2%; DH identification 0.91–0.94, tier 0.98–1.00; RR detection ≈ 0). **Three-track severity scoring** (`score.py` `tracks` + `tracks.py`): Citing Reference / Direct History / Related Reference scored separately by tier (QWK, tier-exact on positives, flag rate); revealed that 0529's "beats on every axis" gap was mostly its "as recognized by" labels scored as applied flags — applied false-flag 1.8% vs 1.7%, CR QWK 0.50 vs 0.55 (test), gated pipelines keep the recall edge (0.61/0.81 vs 0.54 positives flagged); 0529 related-reference precision 0.28.

For detailed metrics across all experiments, see [comparison.md](comparison.md).

## Maintaining CLAUDE.md

- When a change affects project structure, conventions, CLI interfaces, or code style, review and update this file to keep it in sync.
- Examples: new scripts, renamed flags, moved data directories, new treatment types, new experiment patterns.

## Code Style

- **All imports at the top of the file.** Do not use inline/lazy imports inside functions. All `import` and `from ... import` statements must appear at the top of the module, grouped in the standard order: stdlib, third-party, local.

## Key Conventions

- **Instruction versions** are tracked in `citator-pipeline/utils/instructions.py`. The active prompts are: `citator` (single-stage prediction), `citator_v2` (single-stage, two-class treatments — applied + recognized; uses `tool_spec_v2`/`schema_v2`), `reevaluator` (re-evaluation), `haiku_extractor` (two-stage extraction), `kimi_classifier` (two-stage classification). Do not rename these variables.
- **Treatment lists** are defined in the Treatments table below. Any change must be synced to all files listed there.
- **Citation normalization**: The model normalizes reporter names (e.g., "F. Supp." to "F.Supp."). The merge step in `postprocess.py` normalizes both sides to match. Keep this in sync if citation format changes.
- **Incremental saving**: Predictions save to `incremental/{cluster_id}.json` as they complete. Re-evaluation batches save to `reevaluation_incremental/batch_{n}.json`. Resume is opt-in via `--resume` flag.
- **Shared data** lives in `data/` at the repo root (opinion texts, metadata, labels). Experiment folders contain only results (`data/output/`, `data/eval/`). Legacy experiments (pre-0405) still have `data/input/` with symlinks.
- **Benchmark labels** live in `data/benchmark_original/`. The latest version is `0410.csv`. All evaluations default to this file. When the labels are updated, add a new versioned CSV and update the default path in `run_example.py`, `run_two_stage.py`, and `eval_utils.py`.
- **Git ignore**: All experiment `data/` subfolders must be excluded from git. When creating a new experiment folder, add `experiments_MMDD2026/data/` to `.gitignore` immediately.

## Running

All scripts run from `citator-pipeline/`. Input data is read from `--input-dir` (defaults to `../data`), results are written to `--output-dir` (the experiment's `data/` folder). `--txt-path` defaults to `../data/example.txt` (9 expert-annotated cases).

```bash
# Single-stage pipeline (Sonnet + Kimi re-eval) on all 9 examples
python run_example.py --output-dir ../prior_experiments/experiments_04062026/data --evaluate

# Two-stage pipeline (Haiku extraction + Kimi classification) on all 9 examples
python run_two_stage.py --output-dir ../prior_experiments/experiments_04072026/data --evaluate

# Two-stage batch inference (Haiku Stage 1 + Kimi Stage 2) — the 0518 production config.
# Each run is keyed by a UUID `run_id` and persisted to s3://{bucket}/Citator/runs/{run_id}/.
# S3 is the source of truth between stages; --output-dir is local scratch + final CSVs.
python run_batch.py submit-extraction \
    --output-dir ../prior_experiments/experiments_04052026/data --court ca1
# → prints run_id at exit
python run_batch.py submit-classification \
    --output-dir ../prior_experiments/experiments_04052026/data --run-id <run_id>
python run_batch.py collect \
    --output-dir ../prior_experiments/experiments_04052026/data --run-id <run_id> \
    --labels-file ../data/benchmark_original/0410.csv

# Or end-to-end (chains all three with wait-for-completion between stages)
python run_batch.py run --output-dir ../prior_experiments/experiments_04052026/data --court ca1

# Single-stage Sonnet batch inference — the 0529 candidate config.
# IMPORTANT: pass --input-dir explicitly so defaults resolve via the experiment's
# symlinks (otherwise picks up the shared 661-cluster sample metadata).
python run_batch.py run-sonnet \
    --input-dir ../experiments_05292026/data \
    --output-dir ../experiments_05292026/data \
    --labels-file ../data/benchmark_original/0410.csv

# Optional Kimi re-evaluator pass (chains off a completed Sonnet run; flags ~12% of
# rows for Kimi to re-classify). Net-negative on the 0529 Sonnet base — not part
# of the candidate setup; left in place for future flag_for_review retuning.
python run_batch.py run-reeval \
    --run-id <sonnet_run_id> \
    --output-dir ../experiments_05292026/data \
    --labels-file ../data/benchmark_original/0410.csv

# Smoke test on the synthetic batch_test data (overrides metadata + opinion paths,
# uses short test prompts in place of the production prompts)
python scripts/generate_synthetic_opinions.py
python run_batch.py run \
    --output-dir ../experiments_batch_test/data \
    --metadata-file ../data/batch_test_citing_metadata.csv \
    --opinion-dir ../data/batch_test_opinion_texts \
    --smoke-test
```

## AWS

- Uses Bedrock via `boto3.Session(profile_name="dev-env")` in `us-west-2`
- Batch jobs require `CITATOR_S3_BUCKET` and `CITATOR_BATCH_ROLE_ARN` env vars
- Bedrock batch inference requires ≥100 records per JSONL input file
- Two-stage pipeline:
  - Stage 1 model: `us.anthropic.claude-haiku-4-5-20251001-v1:0` (tool_use)
  - Stage 2 model: `moonshotai.kimi-k2.5` (no tool_use; schema inlined in user prompt)
- Single-stage Sonnet pipeline:
  - Model: `us.anthropic.claude-sonnet-4-6` (tool_use)
  - `SONNET_MAX_TOKENS = 64000` (model's hard ceiling; required for large opinions emitting 60+ cases per page)
- Re-eval pipeline (optional, currently net-negative on 0529 Sonnet base):
  - Model: `moonshotai.kimi-k2.5` with the `reevaluator` prompt
  - Flagged rows batched in groups of 5 (`REEVAL_BATCH_SIZE`)

## Domain Context

- The most common model errors are: cert denied directionality swaps, missing "as recognized by" modifier, "Reversed by" when it should be "Reversed and remanded by", and "Cited by" when the case was actually distinguished.
- "Cited by" is the neutral/default treatment. All other treatments represent some form of negative or procedural action.

## Treatments

This is the canonical treatment list. Any changes here must be reflected in:
- `citator-pipeline/utils/instructions.py` — `citator`, `reevaluator`, and `kimi_classifier` prompts
- `citator-pipeline/utils/bedrock_converse_utils.py` — JSON schema
- `citator-pipeline/utils/postprocess.py` — `TREATMENT_RANK`, `severity_mapping`, and `direction_mapping` (all in one file; `eval_utils.py` imports them)

### Two treatment classes (canonical direction, 2026-06-17)

Each cited authority carries **two classes** of treatment, distinguished by *who* applied the treatment, not by the label:

1. **Applied** — how *this* citing case treats the authority. **Exactly one** treatment, drawn from the "by" forms (Direct History or Citing Reference). When the citing case does nothing more than cite the authority, the applied treatment is `Cited by`. Direct History is the special case where the cited case is the immediate case on appeal (the citing case *is* the appellate decision); Citing Reference is analytical treatment of a case not on appeal.
2. **Recognized** — treatments the citing case *reports another court* applied (the "as recognized by" forms; e.g. "*Smith*, aff'd, … cert. denied", or "*Smith*, overruled by *Jones*"). **Zero or more** per authority. Recognized treatments span the full taxonomy, not just direct history — recognized *overruling* is the strongest negative signal.

The same label can appear in either class (a case can *affirm* an authority, or merely *recognize* that another court affirmed it). Applied is single-winner by expert vote; each recognized treatment is final when 2+ reviewers list it.

| Severity | Direct History (applied) | Citing Reference (applied) |
|----------|---------------|-----------------|
| **Stop** | Reversed by | Overruled by |
| | Reversed and remanded by | Abrogated by |
| | Vacated and remanded by | Questioned by |
| | Vacated by | |
| **Warning** | Reversed in part; Vacated in part by | Disapproved by |
| | Affirmed in part; Reversed in part by | Limited by |
| | Affirmed in part; Vacated in part by | |
| **Caution** | Modified by | Criticized by |
| | Remanded by | Distinguished by |
| | Cert. granted by | Declined to follow by |
| **Neutral** | Dismissed by | Cited by |
| | Cert. denied by | |
| | Affirmed by | |

- **Recognized treatment severity mirrors its base label** — "Overruled as recognized by" is **Stop**, "Affirmed as recognized by" is **Neutral**. (This supersedes the earlier rule that classed *all* recognized treatments as Stop.) An authority's overall signal = the worst severity across its applied + recognized treatments.
- **"Cited as recognized by" is not a valid treatment.** If a case is merely cited without negative treatment, the treatment is `Cited by` — never "Cited as recognized by".
- **Pipeline sync — partial.** The two-class model is implemented **additively** as `citator_v2` (`instructions.py`) + `tool_spec_v2`/`schema_v2` (`bedrock_converse_utils.py`), with two-class parse/postprocess + mirrored-severity in `experiments_06172026/v2_postprocess.py` (first run: 0617). The **canonical single-stage** path (`citator`, `tool_spec`, `postprocess.py` `TREATMENT_RANK`/`severity_mapping`) is intentionally left single-class + blanket-Stop so 0529 stays reproducible. When the two-class model is adopted as the default, fold `v2_postprocess` logic into `postprocess.py` and promote `citator_v2`. The `citator-benchmark` viewer + `citator-annotator` taxonomy must also stay aligned with this direction.
