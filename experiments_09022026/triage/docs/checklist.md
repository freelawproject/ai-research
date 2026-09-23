# Triage experiment — data selection and seeder checklist

Working order. Each block ends with what has to be true before the next one
starts. Companion to `../triage_plan.md`.

## A. Sample citing opinions from CLReplica — `sampling/sample_clreplica.py`

Decisions baked into the script (change the constants at the top):

- [ ] **Population.** Published clusters filed 1950 onward, with at least one
      cited opinion, from SCOTUS, the 13 federal circuits, and state courts of
      last resort (`Court.jurisdiction == "S"`, in use). Every sub-opinion must
      have `html_with_citations`.
- [ ] **Exclusions.** `inputs/triage_exclude_cluster_ids.csv`: 383 benchmark
      citing clusters + 647 clusters from the 2026-04-02 sampled pool (1,030
      ids). Add ids here if the dev/test split ever grows.
- [ ] **Targeting.** An opinion is a *keyword* pick when at least one STRONG
      term (overrule, abrogate, disapprove, distinguish, decline to follow,
      limit … to its facts, criticize, call into question, cast doubt, no
      longer good law, not persuasive, reject the reasoning, inapposite,
      superseded, repudiate, disavow, undermine, do not follow, question the
      validity) occurs within 300 characters of a citation span. DH terms
      (reverse, vacate, affirm, remand, cert. denied/granted, modify, dismiss)
      and signals (but see, contra, cf., compare … with) are counted but not
      required. Review the STRONG list before running; add terms, don't loosen
      the near-citation rule.
- [ ] **Control quota.** Opinions with no STRONG term near a citation, so
      the encoder does not learn "the opinion contains *distinguish*" as the
      feature. Direct-history words are deliberately not part of the filter:
      every appellate opinion has a disposition. Default 1 per cell vs 5
      keyword.
- [ ] **Strata.** court group (3) × decade (≤1970s, 80s, 90s, 00s, 10s, 20s)
      × length (short <20K chars, medium 20–60K, long >60K) = 54 cells.
      Pilot = 5 keyword + 1 control per cell ≈ 324 opinions. Scale-up = 25 + 5
      per cell ≈ 1,620.
- [ ] **Scan cap.** 800 candidates per (group, decade). Cells still short at
      the cap are listed in `summary.json → unfilled_cells`; expect ≤1970s
      state and long-bin cells to be the thin ones.

Run (in the courtlistener checkout, container up):

```bash
docker cp inputs/triage_exclude_cluster_ids.csv cl-django:/opt/courtlistener/
docker cp sampling/sample_clreplica.py cl-django:/opt/courtlistener/
docker exec cl-django python manage.py shell -c "exec(open('sampling/sample_clreplica.py').read())"
docker cp cl-django:/opt/courtlistener/triage_sample/ ./data/
```

Post-run checks:

- [x] `cell_fill.csv`: every cell at target (one duplicate pick → SCOTUS/2020s/short has 4 keyword).
- [x] `summary.json → strong_term_opinions`: no single term dominates; if
      *distinguish* is >60% of keyword picks, raise the others' share by
      capping per-term picks on the next run.
- [x] `sample_metadata.csv`: no `cluster_id` in the exclusion list; length and
      decade distributions roughly flat; `n_cited_clusters` median in the
      benchmark's range (~25).
- [x] Open 10 random `opinion_html/*.json` and confirm the STRONG term really
      sits near a citation to *another* case (not the disposition of this one).
- [x] Record the run: seed, constants, counts, and elapsed time in
      `../readme.md` under a "Sample" heading.

## B. Human correction of citation extraction and coreference

- [x] **Annotator instance — BUILT 2026-09-02, smoke-tested.** Reuses the
      citator-benchmark viewer's Grouping page (span fixes, add missed
      mentions, regroup parallel and short forms, export
      `citation_groups.json`). Pieces:
      1. Benchmark viewer roots are env-configurable (`CITATOR_BENCH_DATA_DIR`,
         `CITATOR_BENCH_OVERRIDES_DIR`; defaults unchanged) — patched in
         `pipeline/common.py`, `pipeline/grouping_data.py`,
         `pipeline/consolidate.py`; documented in the benchmark readme.
      2. `annotator/make_annotator_data.py` converts the sampler output into a viewer
         root: copies `opinion_html/`, writes one unlabeled
         `assignments_long.csv` row per authority pair, empty
         `predictions.csv`, and `overrides/citing_metadata.csv` for the
         SCOTUS/FED/STATE grouping.
      3. `annotator/run_annotator.sh [root] [port]` starts the second instance (default
         `data/annotator`, port 8125).
      Smoke run: 3 benchmark clusters faked as sampler output →
      `data/annotator_smoke/` → records list, grouping page (117 citation
      spans), opinion API all 200; the auto statute-strip wrote to the smoke
      root, the benchmark's own files were untouched.
      **When the real sample lands:**
      ```bash
      python annotator/make_annotator_data.py            # data/triage_sample → data/annotator
      ./annotator/run_annotator.sh                       # http://127.0.0.1:8125/records?tab=citing
      ```
- [x] **Centralia pass over every opinion — BUILT 2026-09-03 (decision:
      run all, auto-flag; no manual flagging).** `triage/centralia/run_centralia.py
      --all` (run inside `flp/centralia`: `uv run python …/centralia/run_centralia.py
      --all`) fetches each cluster's PDF from CourtListener (storage
      `local_path` → Harvard scan → court `download_url`), runs
      `centralia.read(pdf, court_id, allow_pending=True)`, writes
      `data/centralia/out/{cid}.json` + `{cid}.review.html`, appends
      `data/centralia/runs.csv`, and writes the result into the annotator's
      `grouping_overrides/{cid}.json` as `centralia: {status, pdf_source,
      n_opinions, warnings…}` with `needs_centralia` set automatically to
      status != valid. Resumable; `--force` redoes; `--ids` for a subset.
      Viewer: Records → citing has a Centralia column (valid / review /
      scanned / failed badge) and filter (needs review · valid · not needing
      review · not run) + `queue.csv`; the Grouping/Verify page button
      "Centralia review" shows the status and the last run's warnings in its
      tooltip, and toggles the review flag as a manual override.
      `GET /api/centralia_queue[?format=csv]` = the review list.
      **Pass complete 2026-09-03** (readme §7): 95 valid (all born-digital
      PDFs; none before 1990) · 139 review · 54 scanned · 35 failed (31 no
      PDF, okla/colo unknown to centralia, 2 centralia crashes). 95 fed into
      the annotator; Records page shows a status summary line
      ("centralia: … | text: 95 centralia · 228 CL"). `--sync-flags`
      rebuilds every override's centralia record from runs.csv (the viewer's
      loader now keeps the `centralia` key; it used to drop unknown keys on
      save).
- [x] **Feed centralia back into the annotator — BUILT 2026-09-03.** Rule:
      if centralia read the PDF cleanly (status valid) the annotator shows
      centralia's html; otherwise the CL html_with_citations stays.
      centralia emits no citation tags, so `triage/centralia/feed_centralia.py` runs
      eyecite over each writing (case citations only, no resolution to CL
      cluster ids; a full cite and its short forms / id. / supra share a
      synthetic `data-id` so they seed one group), writes the payload to
      `opinion_html/{cid}.json` (CL original kept as `{cid}.cl.json`),
      marks `centralia.fed_back` in the override. Skips clusters already
      annotated on the CL text unless `--force`; `--revert` restores CL.
      Seeding rule (fixed twice 2026-09-03 after over-grouping showed up):
      shared seed id only for a full case cite + the short forms / Id. /
      supra eyecite resolves to it; unresolved cites seed alone; resolution
      runs over ALL citations of the WHOLE opinion (all writings at once,
      one id counter — per-writing numbering had collided groups across
      writings) so an Id. after a statute is not attached to the previous
      case and a dissent's short cite finds the majority's full cite.
      `--refeed` re-tags already-fed clusters (skips hand-annotated). Sanity: eyecite
      finds fewer spans than CL's html because the court PDF does not print
      the parallel reporters (S. Ct., L. Ed. 2d) that CL's text carries;
      short-form and id. counts match exactly.
- [x] **Keyboard navigation between opinions** — `[` / `]` (and the
      "‹ prev · n / N · next ›" links by the stepper) walk the Records
      citing order on the Grouping and Verify pages, staying on the same
      page kind.
- [x] **Step 3 = passage-level treatment review — BUILT 2026-09-03 (decision:
      after step 2 Verify; review happens per passage × citation, seeded by
      the LLM).** On this instance the stepper's third step is
      `/passages/{cid}` (replaces Treatments; Records stage 3 = "passages
      reviewed"). The page lists every citation group of the verified
      grouping with its model-input passages (exactly what the seeder labels
      and the encoder trains on), and per passage: the **LLM seed** (label ·
      treatment · confidence · rationale; the seed's evidence sentence is
      underlined in the text) and **your review** (label: Cited by /
      Treatment / Procedural posture / Not a case; treatment dropdown incl.
      "as recognized by"; note; accept seed · mark done · clear). Reviews are
      stored per passage in `data/passage_reviews/{cid}.json` alongside the
      seed and a text hash (a "text changed" badge flags passages whose
      grouping moved since they were seeded/reviewed). "Accept all seeds"
      takes every unreviewed seed; `u` shows unreviewed only; `r` marks the
      opinion's step 3 done (`passages_reviewed`); `[`/`]` navigate.
      Passage ids = `{cid}:{group}:{i}` — the same `window_id`
      `lib/build_windows.py` emits, so the seeding run's outputs drop straight
      into the `seed` slot. The per-group "passages" button and `v` key were
      removed from the Grouping page.
      **Seeder built 2026-09-11:** `runners/seed_passages.py` (prepare from the live
      viewer API → Bedrock batch → fetch → `apply --write` into the `seed`
      slot → `score`); ids match the page because the passages come from the
      same `/api/passages` code path. dev/test prepared for Opus 5.
- [x] **Dev + test in the same annotator (decided 2026-09-03).**
      `sampling/add_benchmark_clusters.py` adds the 383 benchmark citing clusters to
      the annotator root with expert labels blanked (gold stays in the
      benchmark for evaluation), their cached html, the 74 gold revised_html
      and 84 gold grouping overrides (so gold grouping is not redone). They
      go through the same steps as the sample: html fetch → centralia → feed
      → grouping/verify → passage review; the Set badge (train/dev/test)
      tells them apart. Do NOT use the viewer's "Fetch all opinions from CL"
      on this instance — it overwrites every payload, including the
      centralia-fed ones; fetch missing clusters one by one instead
      (`GET /api/opinion/{cid}`).
- [ ] Annotator brief (one page): fix span boundaries; add every missed
      mention including `id.`, `supra`, short names, possessives; one group
      per cited case (parallel cites unify); mark the decision under review
      with the "on appeal" flag; strip non-case authorities (statutes, rules).
- [ ] Order of work: dev/test benchmark clusters already have gold groups for
      74 opinions; the new sample is the annotator's queue. Start with the
      pilot's keyword picks; controls last.
- [ ] Double review on a 10% sample; disagreement rate recorded.
- [ ] Export `citation_groups.json`; this plus `opinion_html/` is the input to
      the window builder. Every mention has a group id and cluster id (or a
      case name when CL has no match).

## C. Dev and test split from the benchmark

- [x] Split the benchmark citing clusters by cluster, stratified by court
      group and has-any-positive, fixed seed 20260902. DOWNSIZED 2026-09-03
      to dev 50 / test 50 (`sampling/build_splits.py --dev 50 --test 50`), taking
      → 2026-09-08: 9600200 dropped from dev by curator decision (dev = 49); files backed up in `data/annotator_pruned/9600200/`
      curator-verified clusters first (dev 35 verified, 78 positive pairs;
      test 18 verified, 54 positives); 283 clusters "unused" and pruned from
      the annotator. Full split kept as `inputs/splits_full.csv`.
- [ ] Only pairs with a final treatment count. Drop pairs whose only
      non-neutral label is "as recognized by".
- [ ] Freeze. Test is not opened until step 4 of the plan.

## D. Seeder model selection

Inventory done 2026-09-02; table and rationale in `../readme.md` § 5. Re-check
the AWS batch-inference support page before the bake-off runs; the lineup
moves.

- [x] **Inventory the current frontier models.** Bedrock batch-eligible today:
      Opus 5, Opus 4.6, Sonnet 4.6, Sonnet 4.5, Haiku 4.5, Kimi K2.5. Not
      batch-eligible on Bedrock: Fable 5.1, Fable 5, Sonnet 5, Opus 4.8/4.7.
- [x] **Shortlist, in order (decided 2026-09-02): open-weight first.**
      1. Kimi K2.5 (batch, us-west-2; 16K output cap → smaller pages).
      2. GLM 5 (on-demand only; 128K output; ~1.5x Kimi's price).
      3. GLM 4.7 (batch; 4K output cap → compact output: list only cases with
         a treatment beyond Cited by).
      4. Anthropic models (Sonnet 4.6 incumbent, Opus 5) only if none of the
         above clears the gate. Specs and prices in `../readme.md` § 5.
- [ ] **Pipeline changes for the open-weight runs:** a single-stage record
      builder on the Kimi body shape (system prompt + opinion + inlined
      `citator` schema, JSON parsed from text) with a `--model` switch for
      `zai.glm-5` / `zai.glm-4.7`; page size tuned so expected output fits
      the model's cap (Kimi 16K, GLM 4.7 4K with compact output); on-demand
      path for GLM 5. Smoke 10 clusters on-demand before any batch.
- [ ] **Pipeline changes before any Opus 5 / Sonnet 5 record is built** (only
      if reached): omit `temperature` (400 otherwise); add `MODEL_CAPS`
      entries; pass `output_config.effort` (`low` or `medium` first) or
      disable thinking; keep forced `tool_choice`; budget 1.3x tokens for the
      newer tokenizer. Smoke a 100-record batch first.
- [ ] **Extend the prompt** to return, per cited case, the treatment plus the
      quote it relied on (already emitted) and the mention it anchors on. No
      other prompt changes in the bake-off, so differences are the model.
- [ ] **Bake-off on the dev set** (150 clusters, one batch per model):
      pair-level any-treatment precision, recall, F1 against the experts on
      the same pairs, human leave-one-out alongside (ceiling: F1 0.64, P 0.57,
      R 0.74 with "as recognized by" excluded); per-category slice (citing
      reference vs direct history); quote-locates-a-window rate; extraction
      recall against gold groups; measured cost per opinion split into
      per-token and per-case terms; wall-clock. Order: Kimi K2.5, GLM 5,
      GLM 4.7 compact; Anthropic only if needed.
- [ ] **Pick** by: recall first (silver labels the encoder never sees a
      positive for are lost), then precision, then cost per opinion at the
      1,620-opinion scale. Write the decision and the table into
      `../readme.md`.
- [ ] Iterate the prompt on dev with the chosen model until the gate holds
      (recall ≥ 0.80, precision ≥ 0.55, every dev positive's quote resolves).
      Each version gets a run id; disagreements reviewed in the viewer.

## E. Hand-off to the plan

Built 2026-09-02 (all in `triage/`, smoke-tested on 3 clusters + the 39 dev
clusters with local HTML):

- [x] **Seeding is PASSAGE-LEVEL (decided 2026-09-03).** The LLM sees exactly
      what the encoder sees — header + one passage with the target marked
      `[T]…[/T]` — and labels that passage alone: `treatment` (with the
      treatment, caseHistory, actingCase, verbatim evidence) · `cited_by` ·
      `posture` (target is the case below; routed structurally, excluded
      from training) · `not_about_target`. No quote location, no masked
      windows, every passage gets a definite label. `lib/passage_prompt.py` →
      `prompts/triage_passage_seeder_v1.md` (~3.6K tokens; canonical
      definitions + Distinguished/signal guidance verbatim, instructions
      rewritten for one passage). `runners/prepare_passage_inputs.py` renders one
      batch record per passage from a `lib/build_windows.py --all-pairs` file.
      Pilot: 323 opinions → 17,349 targets → 23,536 passages (median 2.3K
      chars) → ~100M input tokens ≈ $60 + output ≈ $10 on Kimi on-demand.
- [x] **Whole-opinion compact seeder (v2)** — `lib/seed_prompt.py` →
      `prompts/triage_seeder_v2.md`: canonical `citator` verbatim except
      inventory-id identification and compact, passage-listed output (every
      treating passage per case, each with its own treatment). Kept as an
      optional cheap PRE-FILTER (one call per opinion) if the passage pass
      ever needs narrowing at scale; not the primary labeler.
- [x] **`lib/tagged_text.py`** — one representation for seeder + encoder:
      `<citedCase group="N">` mentions + `<lead>/<dissent>/…` sections +
      inventory; from the annotator's `revised_html` (gold coref, only when
      the cluster is marked done) or raw `html_with_citations` (eyecite
      groups by linked cluster id) as fallback.
- [x] **`runners/prepare_seed_inputs.py`** — annotator root or `--root benchmark
      --split dev|test` → `data/seed/<tag>/{tagged,inventory,input.jsonl,
      manifest.json}`; Kimi/GLM chat-completions batch records; pages sized
      per model output cap.
- [x] **`lib/build_windows.py`** — PASSAGES: the paragraph holding a mention
      ± 1 paragraph (cap 4,000 chars ≈ 1K tokens; overlapping blocks merge),
      `[T]…[/T]` on every target mention, header line, optional `[CITE]`
      masking. Modes: `--all-pairs` (unlabeled, the seeder's input; targets
      need no cluster id), `--seed` (per-passage labels from the passage
      seeder), `--gold` (dev/test eval: pair label + quote-overlap
      positives, masked others; DH pairs to be excluded). Tagged text now
      re-stitches CL's fragment paragraphs (citations set in their own
      blocks) and groups centralia-fed spans by eyecite seed id.
- [ ] **Fetch HTML for the rest of dev/test.** Only 39/150 dev and 46/233
      test clusters have `opinion_html` cached in the benchmark. Use the
      benchmark viewer's "Fetch all opinions from CL" (token in `.env`), or
      export from CLReplica; then rerun `runners/prepare_seed_inputs.py --root
      benchmark --split dev`.
- [x] **Seed run + collect** — `runners/seed_passages.py` (2026-09-11): passage-level
      (not the whole-opinion `{citedCases, citedByIds}` form); outputs go to
      `passage_reviews/{cid}.json` seeds, scored pair-level vs gold. dev/test
      prepared for Opus 5 batch; submit pending SSO login.
- [ ] Seed the pilot sample (keyword + control) with the accepted seeder.
- [ ] Viewer up on :8195 over gold windows before the first training run.
- [ ] Then plan steps 3 and 4.

## F. Train-set citation correction (LLM pass 1) — designed 2026-09-09
- [x] Design + prompt: `citation_seed_design.md`, `prompts/triage_citation_fixer_v1.md` (edit-list output mapping onto the override schema)
- [x] `lib/citation_seed.py prepare` (numbered `<c id g>` inputs + id maps; `--seed-state` for gold clusters) — 2026-09-09
- [x] `lib/citation_seed.py apply [--write]`: edits → overrides (`by: llm`), `before` context → nth — 2026-09-09; [ ] server-side revised_html export for LLM-corrected train
- [x] `lib/citation_seed.py score`: mention F1 + coref F1 + edit precision + gold-edit recall vs dev gold — 2026-09-09
- [x] Pilot: Claude Opus subagents on 10 train + 2 dev — dev mention F1 0.98/1.00, coref 1.00/1.00, edit precision 43/45 (readme last §) — 2026-09-09
- [x] Wave 2: 50 more train opinions via Opus subagents, all applied (0 skipped) — 2026-09-09
- [ ] Decide production route for the remaining 263 train opinions: Opus subagents (≈20 at a time) vs Bedrock batch (Kimi/GLM/Opus 5)
- [ ] Run train, spot-check ~20 opinions in the annotator, then passage treatment pass

