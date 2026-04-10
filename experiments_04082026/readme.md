## Experiment: Two-Stage Pipeline with Sonnet Re-evaluation

### Objective
Test whether adding a Sonnet re-evaluation step to the two-stage pipeline improves quality. The re-evaluator receives full section context (not just the quote) for flagged results.

### Pipeline
1. **Stage 1 (Haiku 4.5)**: Extract citations and report section IDs
2. **Stage 2 (Kimi K2.5)**: Classify treatment from section context + neighbors
3. **Stage 3 (Sonnet 4.6)**: Re-evaluate flagged results (Distinguished by, Cited by with distinguishing keywords, as recognized by)
4. **Post-process**: Filter non-case citations, clean, dedup, merge, evaluate

### Models
- **Extraction**: Haiku 4.5 (`us.anthropic.claude-haiku-4-5-20251001-v1:0`) via AWS Bedrock
- **Classification**: Kimi K2.5 (`moonshotai.kimi-k2.5`) via AWS Bedrock
- **Re-evaluation**: Sonnet 4.6 (`us.anthropic.claude-sonnet-4-6`) via AWS Bedrock

### Results (9 expert-annotated cases, vs 0407 without re-evaluation)

| Metric | 0407 (no re-eval) | 0408 (+ Sonnet) |
|--------|-------------------|-----------------|
| Match Rate | 98.64% | 99.32% |
| Treatment F1 (macro / weighted) | **0.66 / 0.74** | 0.64 / 0.68 |
| Direction F1 (macro / weighted) | **0.92 / 0.88** | 0.88 / 0.87 |
| Severity F1 (macro / weighted) | **0.80 / 0.77** | 0.77 / 0.74 |
| Cost/Case | **$0.048** | $0.123 |

### Conclusion
The Sonnet re-evaluator does not improve quality and adds 156% cost overhead. The enhanced Kimi classifier prompt already handles the error patterns that re-evaluation was designed to correct. **Recommendation: use 0407 (no re-evaluation) as the production configuration.**
