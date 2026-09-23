## Experiment: CA1 Production Run with v404 Re-evaluation

### Objective
Run the citator pipeline from experiments_04032026 on all 49 CA1 (First Circuit) opinions for expert review. No ground truth treatments are available for these cases, so no evaluation step is needed — the output is for expert annotation.

### Changes from v403 (experiments_04032026)

#### New instruction version: v404
The prediction instructions are unchanged from v403. The v404 changes are in the **re-evaluation prompt** and **flagging logic**:

1. **"Reversed by" vs "Reversed and remanded by" guidance in re-evaluation prompt**: The re-evaluator is now instructed to check quotes for remand language (e.g., "reversed and remanded", "remand for further proceedings") and upgrade "Reversed by" to "Reversed and remanded by" when present. The same applies to "Vacated by" vs "Vacated and remanded by" and their "as recognized by" variants.

2. **"Vacated by" added to flagging**: In v403, only "Reversed by" was flagged for re-evaluation. In v404, "Vacated by" is also flagged, since the same missed-remand problem applies.

### Models
- **Prediction**: Sonnet 4.6 (AWS Bedrock): `us.anthropic.claude-sonnet-4-6`
- **Re-evaluation**: Kimi K2.5 (AWS Bedrock): `moonshotai.kimi-k2.5` (recommended from v403 experiments)

### Data
- **Input**: Opinions from experiments_04022026 database query, filtered by circuit court at runtime
  - Cluster IDs: Generated dynamically from `data/citing_metadata.csv` based on `--court` parameter
  - Opinion texts: `data/input/opinion_texts/` (symlink → experiments_04022026)
  - Authority labels: `data/cited_metadata.csv` (symlink → experiments_04022026) — used for merge only, no ground truth treatments

### Pipeline
Same pipeline as v403, without the evaluation step. Step 0 added for dynamic court filtering:

0. **Generate Court Input** — Filter `citing_metadata.csv` by `--court` parameter, write cluster IDs to `data/input/{court}.txt`
1. **Prediction with Page Splitting** — Sonnet 4.6 with v404 instructions (same as v403)
2. **Deduplication** — Keep most negative treatment per `(citing_cluster_id, mainCitationString)`
3. **Re-evaluation** — Flag and re-evaluate using Kimi K2.5 with v404 re-evaluation prompt:
   - "Cited by" with suspicious keywords (59 patterns)
   - All "as recognized by" treatments
   - All "Reversed by" treatments (v403)
   - All "Vacated by" treatments (v404 addition)
4. **Final Deduplication** — Second dedup pass after re-evaluation
5. **Merge to Labels** — Match predictions to authority records on citation string (for expert review context, not evaluation)
