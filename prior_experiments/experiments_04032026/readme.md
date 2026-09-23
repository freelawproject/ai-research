## Experiment: Citator v403 Pipeline with Re-evaluation

### Objective
Develop and evaluate an enhanced citator pipeline using Sonnet 4.6 for prediction with v403 instructions, post-processing (deduplication, keyword flagging), and a re-evaluation step using a second model to correct common misclassifications. Compare against the v318 baseline from experiments_04012026.

### Key Findings
See `experiment_results.md` for the full analysis. Summary:
- **Kimi K2.5 is the recommended re-evaluator**: best quality at lowest cost (+6% overhead)
- **Direction F1**: 0.84 → 0.96 (Kimi), 0.91 (Sonnet) — the biggest improvement
- **Treatment F1**: 0.35 → 0.47 (Kimi), 0.41 (Sonnet)
- **Cert. denied as recognized by F1**: 0.47 → 0.93 (Kimi) — nearly solved
- **Page splitting** validated on 150K char opinion with perfect scores

### Models
- **Prediction**: Sonnet 4.6 (AWS Bedrock): `us.anthropic.claude-sonnet-4-6`
  - Context window: 1M tokens | Max output: 64K tokens
- **Re-evaluation**: Kimi K2.5 (AWS Bedrock): `moonshotai.kimi-k2.5` (recommended)
  - Alternative: Sonnet 4.6 (higher cost, lower quality on directionality)

### Instructions
- **v403** (`utils/instructions.py`): Enhanced from v318 with:
  - Mandatory verification step for "as recognized by" and cert treatments
  - Expanded "Distinguished by" language patterns
  - "Acting Case ≠ Target Case" rule
  - "Cited as recognized by" explicitly prohibited
  - New Example 10 showing cert denied verification with common error
  - Example 8 expanded with implicit distinguishing pattern
- **Re-evaluation prompt** (`reevaluation_v403`): Focused prompt for correcting flagged results, with guidance on independent directionality analysis and common error patterns

### Data
- **Example test set**: `example.txt` — 8 expert-annotated cases from experiments_03192026
  - Opinion texts in `data/input/example_opinion_texts/` (copied from 0319, `targetCase` → `citedCase`)
  - Expert labels: `data/revised_metadata_labels_317.csv` (from experiments_03192026)
- **Production data**: experiments_04022026 — newly queried CLReplica DB data
  - Opinion texts: `data/input/opinion_texts/` (symlink → experiments_04022026)
  - Authority labels: `data/cited_metadata.csv` (symlink → experiments_04022026)
  - Metadata: `data/citing_metadata.csv`, `data/authority_metadata.csv`, `data/unmatched_metadata.csv` (symlinks)

### Pipeline

#### Step 1: Prediction with Page Splitting
Long opinions are split into overlapping pages before sending to the model. This prevents output token exhaustion for opinions with many authorities.

- **Page size**: 145K chars (~41K tokens) — targets ~150 authorities per page
- **Overlap**: 20K chars — prevents missing citations at page boundaries
- **Split logic**: Breaks at paragraph boundaries; results from all pages are combined
- **Pages saved**: Split pages are saved to `data/output/v403/{model}/pages/{cluster_id}/` for inspection
- **Threshold justification**: See `analysis.ipynb`. The binding constraint is the 64K output token limit (~300 tokens per cited case), not the 1M input context window.

#### Step 2: Deduplication
For each opinion, deduplicate model-returned cited cases to keep only the most negative treatment per unique `(citing_cluster_id, mainCitationString)`. Rows without a citation string are kept as-is.

Treatment severity ranking (most to least negative):
- **Stop**: Reversed, Vacated, Overruled, Abrogated, Questioned
- **Warning**: Affirmed in part; Reversed in part, Disapproved, Limited
- **Caution**: Remanded, Cert. granted, Criticized, Distinguished, Declined to follow
- **Neutral**: Dismissed, Affirmed, Cert. denied, Cited by

#### Step 3: Re-evaluation
Flagged results are sent to a re-evaluation model in batches of 5 for independent treatment verification. Three categories are flagged:

1. **"Cited by" with suspicious keywords** (59 regex patterns across 8 categories)
2. **All "as recognized by" treatments** — directionality is frequently wrong
3. **All "Reversed by" treatments** — the model often misses "remanded", outputting "Reversed by" when it should be "Reversed and remanded by"

The re-evaluation prompt instructs the model to independently analyze directionality from the citation string and quote, treating the original model's rationale as a hint that may be wrong.

#### Step 4: Final Deduplication
Second deduplication pass after re-evaluation may change treatments, ensuring only the most negative treatment per cited case is retained.

#### Step 5: Merge to Labels
Merge final results to labels on citation string. Outputs matched, label-missed, and model-missed CSVs.

#### Step 6: Evaluate (example_run.py only)
Evaluate against expert labels using `evaluate_example()` — computes severity, direction, and treatment F1 metrics with "Cited by" downsampled to 1%.

### Known Limitations / Future Enhancements
- **Re-evaluation prompt does not yet address "Reversed by" vs "Reversed and remanded by"**: The current re-evaluation prompt lacks specific guidance for checking whether a "Reversed by" should include "remanded". The prompt should be enhanced to instruct the re-evaluator to check the quote for remand language (e.g., "reversed and remanded", "remand for further proceedings") and upgrade to "Reversed and remanded by" when present. The same applies to "Vacated by" vs "Vacated and remanded by".
- **Distinguished by vs Cited by** remains the primary quality bottleneck (65% of remaining misclassifications).

### Next Step
Run the full CA1 sampled dataset (49 cases) with Kimi K2.5 re-evaluation for expert's review.
