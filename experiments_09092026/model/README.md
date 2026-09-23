# model/ — training and running the two models

Two separate models on one shared backbone
(`ai-law-society-lab/CaseLawModernBERT-large`):

- an **extractor**, a token classifier that tags citation spans, and
- a **linker**, the `AntecedentLinker`, which groups those spans into
  coreference clusters by scoring each mention against its candidate
  antecedents.

Both are published as private Hugging Face repositories; `../results/README.md`
maps each training run to its repository and shows how to download the weights.

## Inference

| File | Role |
|---|---|
| `inference.py` | Entry point: tag mentions with the extractor, then group them with the linker |
| `linking_model.py` | The `AntecedentLinker` class and `predict_clusters` |
| `linking_data.py` | Batch assembly and the `Id.` decode rule |
| `common.py` | Splits, candidate pairs, pair features, span decoding |
| `metrics.py` | Extraction and B-cubed coreference scoring |

`inference.py` also imports `../corpus/mention_features.py`, which pulls in
`../corpus/build_dataset.py`. Prediction has to derive mention features exactly
the way training did, so those two files travel with any inference deployment.

```bash
RUN_SUFFIX=_warm LINK_SUFFIX=_r4 uv run python model/inference.py \
    --records data/output/citations_r2.jsonl --split gold_test --out predictions.json
```

The two environment variables name the checkpoint directories under `../runs/`.

### The `Id.` rule is part of the published numbers

`linking_data.id_rule_slots` computes a forced-antecedent slot for every `Id.`
mention, including a skip for citations sitting inside a parenthetical, and
`predict_clusters` applies it. Without it the linker decodes by argmax alone and
scores about a point lower: 0.957 rather than 0.967 B-cubed on gold mentions. It
is on by default; `--no-id-rule` turns it off.

## Training

| File | Role |
|---|---|
| `train_extraction.py` | Trains the extractor |
| `train_linking.py` | Trains the linker |
| `run_runpod.sh` | The driver. Holds every published hyperparameter, keyed by `MODE` |
| `eval_isolated.py` | Scores a trained linker on gold mentions, isolated from extraction errors |

Training needs a CUDA GPU. `MODE=warm` produced the published extractor and
`MODE=r4link` the published linker; `run_runpod.sh` documents the rest.

## Getting work onto a pod

| Script | Builds |
|---|---|
| `make_pod_bundle.sh` | Training bundle: this code plus the built dataset, and the warm-start checkpoints where a round needs them |
| `make_predict_bundle.sh` | Inference bundle: this code plus the two published checkpoints and the records to predict over |
| `run_predict_pod.sh` | Pod-side runner for the inference bundle |

The bundles stage this folder as `model/` with `data/` and `runs/` beside it, so
paths resolve on the pod exactly as they do here. They also copy
`../corpus/mention_features.py` and `../corpus/build_dataset.py` flat next to
`model/`, which is why `inference.py` looks for them in both places.

## Dependencies

`torch`, `transformers` 4.48 or newer, `numpy`, `tqdm` (`pyproject.toml`).

**Pin `transformers` below 5.0.** 5.x replaced the tokenizer backend, and
`return_overflowing_tokens` with a stride no longer tiles a long input:
everything past the first window is silently dropped, with no error raised.
