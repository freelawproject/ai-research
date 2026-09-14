# LLM pass 1: correct citation extraction and coreference on the training set

Dev (49) and test (50) are fully verified by hand. Train (323 opinions, 15.8M chars,
41K eyecite mentions, median 29K chars / 66 mentions per opinion) gets the same
correction from an LLM, gated on dev, then the passage-level treatment pass runs on
the corrected grouping.

## What the pass must fix (measured on the dev/test gold)

| Correction type | Count in dev+test gold | Examples |
|---|---|---|
| removed tags | 961 (381 statutes, ~580 other) | record cites, `Id.` pointing at a statute or the complaint, secondary sources |
| added mentions | 983 | name-only refs 692 (`Pugliese`), `Id.`/`Ibid.` 173, reporter cites eyecite missed 109, supra 5 |
| regrouped mentions | 218 | `Id.` through a `(citing …)` parenthetical, short forms on the wrong case, parallel cites split |
| group names set | 440 | eyecite has no name for unresolved groups |

## Input (one request per opinion)

The tagged text `tagged_text.from_opinion_html` already produces, with every mention
numbered: `<c id="M" g="N">342 F.3d 903</c>`, plus the `<citedCaseInventory>` (group id,
name, sample citations, count) and a `<citingCase>` header. Whole opinion in one request:
coreference needs the full `Id.` chain and the short forms' antecedents. Median ~8K
tokens, 95th percentile ~40K; one outlier (557K chars, 6,117 mentions) is run per
sub-opinion or skipped.

## Output

An edit list, not a re-emitted document (`prompts/triage_citation_fixer_v1.md`):
`remove[m, why]`, `regroup[m → g | new:label]`, `merge[[g, g]]`,
`add[text, before-context, opinion, → g]`, `names{g: name}`, `roles{g: role}`.
Compact (hundreds of tokens, not thousands), so the 16K output cap of Kimi is not a
constraint, and every edit maps 1:1 onto the annotator's override schema:

| edit | override field |
|---|---|
| remove m | `removed_occs` += occ id |
| regroup m → g | `assignments[occ id] = g` |
| merge | `assignments` for every occ of the absorbed groups |
| add | `manual` += {text, nth (from `before`), opinion_type, group} |
| names | `group_names` |
| roles | `roles` |

So the applier writes the LLM's corrections straight into `grouping_overrides/{cid}.json`
(with `by: llm` in `citation_changes.csv`) and the train opinions render corrected in the
same annotator, where a spot-check sample can be verified like dev/test. The export
(`revised_html`) then feeds `build_windows.py` unchanged.

## Model choice

Cost is not the deciding factor: the whole train set is ~4M input tokens and ~0.3M
output tokens, i.e. roughly $3 on Kimi K2.5, $6 on GLM 5, $13 on Opus 5 batch (1.3×
tokenizer), $5 on Sonnet 5 batch. What matters is exactness: correct mention ids,
verbatim `text`/`before` strings, and legal judgment on `Id.` antecedents and
same-decision merges.

Order of trial (per the open-weights-first rule), each run on the 49 dev opinions and
scored against the gold:

1. **Kimi K2.5** (Bedrock batch, 256K context, 16K output). Runner-up in the March
   bake-off; the batch record builder already exists.
2. **GLM 5** (Bedrock on-demand only, 200K context, 128K output) if Kimi misses the gate.
3. **Opus 5** on Bedrock batch (effort medium) if neither open model clears the gate.
   Expected to be the most reliable at verbatim-span discipline; at ~$2 per dev run it
   is cheap to confirm.

GLM 4.7 is excluded only by habit (4K output is enough for an edit list); include it if
GLM 5 on-demand is awkward to run.

## Gate on dev

Apply the edits, export revised html, score against the gold revised html:

- mention F1 (case mentions, span overlap match) — eyecite floor from Exp 3 was 0.856
  on a different set; recompute here as the baseline;
- coreference pairwise F1 / B³ over the mention groups (reuse
  `experiments_06142026/baselines_citation.py`);
- edit-level precision: share of proposed removes/adds/regroups that agree with the gold
  (an edit the gold does not make is a regression, since eyecite was right there).

Gate: the LLM pass beats untouched eyecite on both mention F1 and coref F1 and its
edit precision is ≥ 0.9; otherwise iterate the prompt on dev (test is never looked at)
or move to the next model. Then run train, spot-check ~20 opinions in the annotator,
and proceed to the passage treatment pass.
