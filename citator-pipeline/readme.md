## Citator Pipeline

Reusable pipeline for identifying and classifying case treatment citations in legal opinions using LLMs.

### Overview
The pipeline uses Sonnet 4.6 to analyze legal opinions and identify how each cited case is treated (e.g., reversed, distinguished, overruled). A secondary re-evaluation model (Kimi K2.5) corrects common misclassifications.

### Pipeline Steps
0. **Generate Input** — Filter citing_metadata.csv by court and source
1. **Prediction** — Sonnet 4.6 analyzes each opinion (with page splitting for long opinions)
1b. **Treatment Cleanup** — Fix formatting errors ("Cited as recognized by" → "Cited by", malformed modifiers)
2. **Deduplication** — Keep most negative treatment per (citing_cluster_id, mainCitationString)
3. **Re-evaluation** — Flag suspicious results and re-evaluate with Kimi K2.5:
   - "Cited by" with keywords suggesting negative treatment (59 patterns)
   - All "as recognized by" treatments (directionality errors)
   - All "Reversed by" treatments (missed remand language)
   - All "Vacated by" treatments (missed remand language)
4. **Final Deduplication** — Second pass after re-evaluation
5. **Merge to Labels** — Match predictions to authority records with citation normalization
6. **Evaluate** — (Optional) Compare against expert labels using severity/direction/treatment F1

### Incremental Saving
Real-time predictions are saved to `{output_dir}/incremental/{cluster_id}.json` as soon as each completes. If the pipeline is interrupted, it resumes from where it left off.

### Instruction Versions

| Version | Prediction Prompt | Re-evaluation Prompt | Changes |
|---------|------------------|---------------------|---------|
| v318 | v318 | — | Baseline: core citator instructions |
| v403 | v403 | v403 | Mandatory verification for "as recognized by" and cert treatments; expanded "Distinguished by" patterns; "Acting Case ≠ Target Case" rule; new examples |
| v404 | v403 (unchanged) | v404 | Re-eval prompt adds "Reversed by" vs "Reversed and remanded by" guidance; "Vacated by" also flagged for re-evaluation |

**Current versions**: Prediction = v403, Re-evaluation = v404

### Run Scripts

#### `run_example.py` — Real-time inference on example cases
```bash
python run_example.py --data-dir /path/to/data
python run_example.py --data-dir /path/to/data --evaluate --labels /path/to/labels.csv
```

| Flag | Description |
|------|-------------|
| `--data-dir` | Path to experiment data directory (required) |
| `--txt-name` | Input file name in data/input/ (default: example.txt) |
| `--opinion-dir` | Override opinion text directory |
| `--labels` | Path to expert labels CSV |
| `--evaluate` | Run evaluation against expert labels |

#### `run_batch.py` — Batch inference for circuit courts
```bash
python run_batch.py --data-dir /path/to/data --court ca1
python run_batch.py --data-dir /path/to/data --court all --num-records 10
```

| Flag | Description |
|------|-------------|
| `--data-dir` | Path to experiment data directory (required) |
| `--court` | Circuit court ID or "all" (required) |
| `--num-records` | Limit records per court (for testing) |
| `--collect-only` | Download batch results + postprocess |
| `--postprocess-only` | Run postprocessing on existing parsed results |
| `--merge-only` | Run only the merge step |

### Models
- **Prediction**: Sonnet 4.6 (AWS Bedrock): `us.anthropic.claude-sonnet-4-6`
- **Re-evaluation**: Kimi K2.5 (AWS Bedrock): `moonshotai.kimi-k2.5`

### S3 Configuration
Update `S3_BUCKET` in `utils/batch_utils.py` before running batch jobs:
```python
S3_BUCKET = "your-batch-inference-bucket"
```
