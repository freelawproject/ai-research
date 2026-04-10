## Experiment: Two-Stage Pipeline — Haiku Extraction + Kimi Classification

### Objective
Test a two-stage pipeline that splits citation extraction and treatment classification across two models. Enhanced Kimi classifier prompt with cert denied pattern recognition, citation signal guidance, implicit distinguishing detection, and quote quality requirements.

### Pipeline
1. **Stage 1 (Haiku 4.5)**: Extract citations and report which sections they appear in
2. **Stage 2 (Kimi K2.5)**: Classify treatment for each citation using section context + neighbors
3. **Post-process**: Filter non-case citations, clean treatments, deduplicate, merge to labels, evaluate

### Models
- **Extraction**: Haiku 4.5 (`us.anthropic.claude-haiku-4-5-20251001-v1:0`) via AWS Bedrock
- **Classification**: Kimi K2.5 (`moonshotai.kimi-k2.5`) via AWS Bedrock

### Results (9 expert-annotated cases, vs 0403 Sonnet + Kimi baseline)

| Metric | 0403 | 0407 |
|--------|------|------|
| Match Rate | 98.98% | 98.64% |
| Treatment F1 (macro / weighted) | 0.54 / 0.61 | **0.66 / 0.74** |
| Direction F1 (macro / weighted) | 0.87 / 0.81 | **0.92 / 0.88** |
| Severity F1 (macro / weighted) | 0.76 / 0.73 | **0.80 / 0.77** |
| Cost/Case | $0.210 | **$0.048** |
