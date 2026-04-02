## Experiment: Model Comparison for Citator v318

### Objective
Compare 5 models on the citator task using v318 instructions against 8 expert-annotated cases to select the best model for scaled annotation support.

### Models Evaluated
- Sonnet 4.6 (AWS Bedrock)
- Haiku 4.5 (AWS Bedrock)
- Kimi K2.5 (AWS Bedrock)
- Gemini 3 Flash Preview (Google) — failed, MAX_TOKENS on 7/8 records
- Gemini 3.1 Pro Preview (Google)

### What Was Done
1. Modified utils files to align with v318 output structure (caseHistory, treatment, opinionType, quote, rationale). Removed prompt caching logic.
2. Created `run_all.py` to run all 5 models sequentially.
3. Created `evaluate.ipynb` to run evaluation for each model using `evaluate()` from `eval_utils.py`.
4. Created `model_comparison.md` summarizing completeness, severity/direction/treatment F1 (macro avg, with Cited by downsampled to 1%), cost estimates, and recommendation.

### Conclusions
- **Sonnet 4.6** is the best model: 99.19% match rate, highest direction macro F1 (0.84), tied best severity macro F1 (0.79), and the only model to identify "Cert. denied as recognized by" (F1 = 0.47).
- **Kimi K2.5** had the best treatment macro F1 (0.46) but low match rate (73.45%).
- **Haiku 4.5** was cheapest but weak on direction (0.53) and treatment (0.26).
- **Gemini 3.1 Pro** was the most expensive with the worst metrics overall.
- Treatment classification remains challenging across all models — all had low treatment macro F1 (0.26–0.46), largely driven by failure to identify "Cert. denied as recognized by" and over-prediction of "Cited by".

### Next Step
Use Sonnet 4.6 with batch inference to run 50 cases per circuit court. The goal is to identify likely negative treatment citations for expert annotation.
