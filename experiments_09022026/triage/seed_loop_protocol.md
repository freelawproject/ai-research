# Citation-seeding loop (timer-driven)

State: `data/citation_seed/loop_state.json`. One tick every 2 hours (cron `12 1-23/2 * * *`,
first 15:12 on 2026-09-09). Every tick runs ONE batch of Opus subagents (≤20 concurrent —
launch 20, then more as completion notifications arrive) and finishes its bookkeeping before
the next tick. Nothing runs between ticks. A tick that fires before `state.not_before`
(2026-09-09 15:09) is logged in `state.ticks` and skipped — no agents are launched. Likewise a tick that fires
before `state.next_allowed` (= last batch start + 2 h; batches may also be started by hand,
which resets it) is skipped, so batches never run closer than two hours apart.

A `state.paused` block means no tick runs anything until Rachel says to resume
(2026-09-09 18:40: paused after iteration 2 for her LLM-vs-gold review; the cron was
deleted — re-create it or kick off iteration 3 by hand).

## Phase "dev" (iterations 1..3)

1. `iter = state.dev_iteration + 1`; `out = data/citation_seed/dev_iter{iter}`; prompt = `state.prompt`.
2. Inputs already exist (`prepare --seed-state` over `data/citation_seed/dev_ids.txt`, 49 opinions).
   Launch one Opus subagent per dev id with the standard task text, pointing at `state.prompt`
   and writing `{out}/{cid}.response.md` + `{out}/{cid}.edits.json`.
3. When all 49 are back: `python3 citation_seed.py score --ids $(cat data/citation_seed/dev_ids.txt) --out-dir {out} --write-review --review-prompt <prompt name>`
   (also refreshes the annotator's seed-diff review items; Rachel's earlier decisions on identical items are kept).
   Gate = pooled `llm_mention_PRF[2] ≥ 0.99` AND pooled `llm_coref_PRF[2] ≥ 0.99` (summary in
   `{out}/eval/score.json`). Record the summary in `state.dev_runs[iter]`.
4. If the gate passes, or `iter == 3`: set `state.phase = "train"`, keep `state.prompt` as the
   best-scoring prompt so far, and write the dev results into `readme.md` (experiment) + memory.
5. Otherwise: read `{out}/eval/diff.md`, group the disagreements (wrong removes / wrong adds /
   missed adds / wrong or missed regroups / merges), separate prompt-fixable convention gaps
   from gold judgment calls, write `prompts/triage_citation_fixer_v{iter+1}.md` (copy of the
   current prompt + the fixes, changelog line in the header comment), set `state.prompt` to it,
   `state.dev_iteration = iter`. Note the change summary in `state.dev_runs[iter].prompt_changes`.
   Never edit dev/test gold; never write dev edits into overrides (inputs are `--seed-state`).

## Phase "train" (repeats)

1. `python3 citation_seed.py pick --n 50` → `data/citation_seed/batch_<stamp>_ids.txt`
   (unverified, unseeded, 3K–90K chars, stratified). `prepare --ids …` (no `--seed-state`).
2. Launch the 50 subagents (≤20 concurrent), standard task text, `state.prompt`, outputs in
   `data/citation_seed/outputs/`.
3. As they finish: `apply --write --ids …` (writes overrides + `by: llm` rows; skips any
   with bad JSON — re-run those agents once). Record `{batch, n, applied, skipped}` in
   `state.train_batches`; update memory line. Stop when `pick` finds no eligible opinions.

## Standard subagent task text

"Read /…/triage/{state.prompt} in full, then /…/triage/data/citation_seed/inputs/{cid}.txt in
full; follow the instructions exactly; write the complete response (scan + edits) to
{out}/{cid}.response.md and ONLY the JSON object to {out}/{cid}.edits.json (must parse with
json.loads). Use only Read and Write; write only those two files; copy mention numbers, group
ids, `text` and `before` verbatim; list only real changes; when unsure leave the tagger's
output alone, but tag clearly missed case mentions and fix Id. chains attached to the wrong
case. Reply with a two-line summary." Model: opus.
