# Reproducing the case-name evaluation set

Commands for each step, in order. The method, the numbers and the results are
in [`../readme.md`](../readme.md). Paths are relative to `case_name_eval/`.

## 1. Pull the sample

Both queries read the CourtListener replica and write nothing to it. They run
from the Django shell in the `cl-django` container.

```bash
docker cp sampling/. cl-django:/opt/courtlistener/case_name_sampling/

# Candidates per stratum. Adjust the quotas in pull_sample.sql if needed.
docker exec -e STEP=counts cl-django python manage.py shell -c \
    "exec(open('case_name_sampling/run_in_django.py').read())"

# The sample.
docker exec -e STEP=pull cl-django python manage.py shell -c \
    "exec(open('case_name_sampling/run_in_django.py').read())"
mkdir -p data
docker cp cl-django:/opt/courtlistener/case_name_sampling/sample_2k.csv data/
```

- The runner uses the container's default database connection. Add `-e DB_ALIAS=replica` for another Django database alias.
- After editing a query, repeat the first `docker cp`. The `/.` makes it overwrite.
- The two SQL files repeat the candidate and stratum rules. Change both together.
- Change `seed` in `pull_sample.sql` for a different draw.

`data/sample_2k.csv` columns:

| Column | Meaning |
|---|---|
| `cluster_id`, `opinion_id`, `docket_id`, `recap_document_id` | Identifiers |
| `court_id`, `jurisdiction`, `docket_number`, `nature_of_suit`, `date_created`, `extracted_by_ocr` | Metadata for slicing results |
| `stratum`, `in_quota` | Sampling stratum, and whether the row filled a quota or topped one up |
| `current_case_name`, `current_case_name_full` | Names on the cluster when the sample was pulled |
| `docket_case_name`, `docket_case_name_full` | Names on the docket |
| `pre_update_case_name`, `pre_update_case_name_full`, `first_update_at` | Cluster names before the cluster's first change. Empty if it never changed. |
| `llm_input` | First 2,000 characters of the text |
| `plain_text_length`, `plain_text` | Full text |

## 2. Build the gold names

All commands run from `annotator/`. Model calls need `OPENAI_KEY`, which
`conda run -n citator` provides.

```bash
# Load the sample, assign the splits, set the document types
uv run python load_sample.py
uv run python doc_types.py

# Opus pass: write 40 batches of 50 records, run one sub-agent per batch
# following ../preannotation_brief.md, then check and load the outputs
uv run python make_batches.py
#   reads  ../data/preannotate/batches/batch_NNN.json
#   writes ../data/preannotate/outputs/batch_NNN.json
uv run python collect_preannotations.py

# GPT-6.2 Sol on the records where Opus and production differ, then on the
# short names still unlabeled (export, submit, wait, fetch for each run)
conda run -n citator uv run python gpt_batch.py export --run sol_disagreements --select full
conda run -n citator uv run python gpt_batch.py submit --run sol_disagreements
conda run -n citator uv run python gpt_batch.py wait   --run sol_disagreements
conda run -n citator uv run python gpt_batch.py fetch  --run sol_disagreements
#   repeat with: --run sol_short --select short

# Wider read for captions missing or cut off at 2,000 characters
uv run python window_rerun.py batches        # then one sub-agent per batch
uv run python window_rerun.py collect
#   repeat the gpt_batch steps with: --run sol_wide --select wide
(cd ../prod_replay && conda run -n citator uv run python replay_prod.py \
    --wide-set --run gpt-4o-mini_prod-prompt_8k --max-tokens 2000)
uv run python load_suggestions.py \
    --file ../data/prod_replay/gpt-4o-mini_prod-prompt_8k.jsonl \
    --source "GPT-4o-mini (8K window)"

# Review what the sources did not settle
uv run uvicorn app:app --port 8215          # http://127.0.0.1:8215

# Check the labels against the guidelines, then export
uv run python check_gold.py
uv run python export_gold.py                # ../data/gold/{validation,test}.jsonl
```

- **Auto-acceptance** runs inside `collect_preannotations.py`, `gpt_batch.py fetch` and `window_rerun.py collect`. It never changes a field that already has a label.
- **Re-running a `gpt_batch.py export`** under a new `--run` name retries only the records without an answer.
- **Review keys:** `d` accept and next, `x` no case name, `s` skip, `j` / `k` next / previous, `e` edit, `1`–`9` take a source's value. In the field, Cmd+Enter accepts and Esc leaves it.

**Store.** `data/annotations.db` keeps each kind of value in its own table:

| Table | Holds |
|---|---|
| `records` | The sampled clusters, their split and document type |
| `preannotations` | The Opus value of each record |
| `suggestions` | Other sources' values, one row per record and source |
| `field_labels` | The accepted value of each name, with status `auto`, `reviewed` or `skipped` |
| `review_holds` | Fields put back in the review queue, with the reason |

The store is copied to `data/backups/` each time the app starts.

## 3. Replay the production call

`prod_replay/replay_prod.py` sends the production request and records the raw
reply. Library versions are pinned to CourtListener's lock file.

```bash
cd prod_replay
conda run -n citator uv run python replay_prod.py --limit 100
```

## 4. Score

```bash
cd scoring
python3 -m unittest test_score.py                             # metric tests
python3 score.py --production                                 # names on the clusters
python3 score.py --predictions preds.jsonl --name my-model    # any other model
```

A predictions file has one JSON object per line: `cluster_id`,
`case_name_full`, `case_name`. The summary goes to `results/<name>.json` and
the per-record scores to `data/scores/<name>_records.jsonl`.
