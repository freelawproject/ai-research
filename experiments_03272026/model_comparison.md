# Citator Model Comparison: Binary Flagging Benchmark

Evaluation of 17 models on a binary citator task (8 citing cases, 183 total labels with 44 flagged).

## Pricing Sources

| Provider | Source |
|---|---|
| Anthropic (Claude) | [platform.claude.com/docs/en/about-claude/pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| AWS Bedrock | [aws.amazon.com/bedrock/pricing](https://aws.amazon.com/bedrock/pricing/) |
| OpenAI | [developers.openai.com/api/docs/pricing](https://developers.openai.com/api/docs/pricing) |
| Google AI | [ai.google.dev/pricing](https://ai.google.dev/pricing) |

## Pricing Table (per 1M tokens)

| Model | Provider | Input | Output | Cache Write | Cache Read |
|---|---|---|---|---|---|
| Claude Sonnet 4.6 | Bedrock | $3.00 | $15.00 | $3.75 (1.25x) | $0.30 (0.1x) |
| Claude Haiku 4.5 | Bedrock | $1.00 | $5.00 | $1.25 (1.25x) | $0.10 (0.1x) |
| Gemma 3 27B | Bedrock | $0.23 | $0.38 | - | - |
| Llama 4 Maverick 17B | Bedrock | $0.24 | $0.97 | - | - |
| Llama 3.3 70B | Bedrock | $0.72 | $0.72 | - | - |
| DeepSeek V3.2 | Bedrock | $0.62 | $1.85 | - | - |
| DeepSeek-R1 | Bedrock | $1.35 | $5.40 | - | - |
| GPT OSS 120B | Bedrock | $0.15 | $0.60 | - | - |
| Kimi K2.5 | Bedrock | $0.60 | $3.00 | - | - |
| Qwen 3 Next 80B | Bedrock | $0.15 | $1.20 | - | - |
| Qwen 3 235B | Bedrock | $0.22 | $0.88 | - | - |
| GPT 5.4 Mini | OpenAI | $0.75 | $4.50 | - | - |
| GPT 4o Mini | OpenAI | $0.15 | $0.60 | - | - |
| GPT 4.1 | OpenAI | $2.00 | $8.00 | - | - |
| GPT 4.1 Mini | OpenAI | $0.40 | $1.60 | - | - |
| Gemini 3 Flash Preview | Google | $0.50 | $3.00 | - | - |
| Gemini 3.1 Pro Preview | Google | $2.00 | $12.00 | - | - |

## Token Usage (8-case benchmark run)

Claude models use prompt caching (5-min ephemeral). Most input tokens are billed as cache writes rather than standard input. Cache read tokens are billed at 0.1x the input price.

| Model | Input Tokens | Output Tokens | Cache Write Tokens | Cache Read Tokens |
|---|---|---|---|---|
| Claude Sonnet 4.6 | 224 | 46,474 | 106,525 | 12,081 |
| Claude Haiku 4.5 | 216 | 50,215 | 118,606 | 0 |
| Gemma 3 27B | 111,445 | 17,341 | - | - |
| Llama 4 Maverick 17B | 103,774 | 29,488 | - | - |
| Llama 3.3 70B | 104,082 | 15,660 | - | - |
| DeepSeek V3.2 | 102,143 | 25,900 | - | - |
| DeepSeek-R1 | 102,151 | 54,658 | - | - |
| GPT OSS 120B | 104,173 | 32,768 | - | - |
| Kimi K2.5 | 104,396 | 78,197 | - | - |
| Qwen 3 Next 80B | 110,203 | 34,788 | - | - |
| Qwen 3 235B | 110,203 | 35,884 | - | - |
| GPT 5.4 Mini | 102,317 | 31,366 | - | - |
| GPT 4o Mini | 102,349 | 13,301 | - | - |
| GPT 4.1 | 102,349 | 26,576 | - | - |
| GPT 4.1 Mini | 102,349 | 25,000 | - | - |
| Gemini 3 Flash Preview | 106,061 | 24,642 | - | - |
| Gemini 3.1 Pro Preview | 106,061 | 36,662 | - | - |

## Estimated Cost

Costs are linearly scaled from the 8-case benchmark run. Claude cache efficiency may improve at 1000 cases if requests are batched within the 5-min cache TTL (more cache reads, fewer writes), so Claude estimates below represent a **worst-case upper bound**.

| Model | 8-Case Cost | Per Case | **Est. 1,000 Cases** |
|---|---|---|---|
| GPT 4o Mini | $0.02 | $0.003 | **$3** |
| Gemma 3 27B | $0.03 | $0.004 | **$4** |
| GPT OSS 120B | $0.04 | $0.005 | **$5** |
| Llama 4 Maverick 17B | $0.05 | $0.007 | **$7** |
| Qwen 3 235B | $0.06 | $0.007 | **$7** |
| Qwen 3 Next 80B | $0.06 | $0.007 | **$7** |
| GPT 4.1 Mini | $0.08 | $0.010 | **$10** |
| Llama 3.3 70B | $0.09 | $0.011 | **$11** |
| DeepSeek V3.2 | $0.11 | $0.014 | **$14** |
| Gemini 3 Flash Preview | $0.13 | $0.016 | **$16** |
| GPT 5.4 Mini | $0.22 | $0.027 | **$27** |
| Kimi K2.5 | $0.30 | $0.037 | **$37** |
| Claude Haiku 4.5 | $0.40 | $0.050 | **$50** |
| GPT 4.1 | $0.42 | $0.052 | **$52** |
| DeepSeek-R1 | $0.43 | $0.054 | **$54** |
| Gemini 3.1 Pro Preview | $0.65 | $0.081 | **$81** |
| Claude Sonnet 4.6 | $1.10 | $0.138 | **$138** |

## Quality Metrics

Sorted by completeness (missed labels), then Adj. FN.

**Adj. FN** = reported FN + unmatched flagged cases (44 total flagged - flagged support). This treats flagged cases the model never returned as missed detections -- a more honest measure of FN when completeness varies.

| Model | Matched | Missed Labels | Flagged F1 | FP | FN | Adj. FN | Est. 1K Cases |
|---|---|---|---|---|---|---|---|
| Claude Sonnet 4.6 | 183 | **0** | 0.63 | 15 | 17 | 17 | $138 |
| Claude Haiku 4.5 | 187 | 2 | 0.39 | 46 | 22 | 22 | $50 |
| GPT 5.4 Mini | 175 | 15 | 0.37 | 7 | 30 | 33 | $27 |
| GPT OSS 120B | 6 | 18 | 0.00 | 0 | 2 | 44 | $5 |
| Kimi K2.5 | 163 | 20 | **0.70** | 11 | 13 | 16 | $37 |
| Qwen 3 235B | 159 | 26 | 0.48 | 87 | **0** | **4** | $7 |
| DeepSeek V3.2 | 149 | 34 | 0.49 | 15 | 22 | 26 | $14 |
| Gemini 3 Flash Preview | 149 | 34 | 0.61 | 13 | 17 | 21 | $16 |
| Gemini 3.1 Pro Preview | 93 | 55 | 0.52 | 4 | 16 | 33 | $81 |
| GPT 4.1 | 134 | 55 | 0.16 | 3 | 29 | 41 | $52 |
| GPT 4.1 Mini | 122 | 65 | 0.39 | 50 | 10 | 25 | $10 |
| Qwen 3 Next 80B | 114 | 70 | 0.41 | 68 | 1 | 20 | $7 |
| Llama 4 Maverick 17B | 102 | 81 | 0.25 | 16 | 14 | 39 | $7 |
| GPT 4o Mini | 78 | 105 | 0.38 | 37 | 3 | 32 | $3 |
| DeepSeek-R1 | 76 | 107 | 0.45 | 29 | 5 | 30 | $54 |
| Gemma 3 27B | 64 | 119 | 0.23 | 24 | 9 | 39 | $4 |
| Llama 3.3 70B | 42 | 141 | 0.00 | 2 | 10 | 44 | $11 |

## Analysis

### Evaluation criteria (in priority order)

1. **Completeness** -- fewer missed citation extractions (missed labels)
2. **Low FN for flagged cases** -- conservative flagging preferred (better to flag too many than miss one)
3. **Cost at scale** -- viable for 1,000+ cases

### Top contenders

Only models with fewer than 35 missed labels and Adj. FN under 25 are considered:

| Model | Missed Labels | Adj. FN | FP | Flagged F1 | Est. 1K Cases |
|---|---|---|---|---|---|
| Claude Sonnet 4.6 | 0 | 17 | 15 | 0.63 | $138 |
| Claude Haiku 4.5 | 2 | 22 | 46 | 0.39 | $50 |
| Kimi K2.5 | 20 | 16 | 11 | 0.70 | $37 |
| Qwen 3 235B | 26 | 4 | 87 | 0.48 | $7 |
| Gemini 3 Flash Preview | 34 | 21 | 13 | 0.61 | $16 |
| DeepSeek V3.2 | 34 | 26 | 15 | 0.49 | $14 |

### Recommendations

**Best completeness + quality: Claude Sonnet 4.6 ($138/1K cases)**
- Only model with 0 missed labels -- every citation is extracted
- Adj. FN = 17, balanced FP/FN (15/17), solid F1 (0.63)
- Expensive, but the only choice when complete extraction is non-negotiable

**Best balance of completeness, FN, and cost: Kimi K2.5 ($37/1K cases)**
- 20 missed labels (89% completeness), best F1 (0.70), lowest FP among top models (11)
- Adj. FN = 16 -- comparable to Sonnet at 3.7x lower cost
- Strong choice for scaling up where some missed extractions are tolerable

**Best for aggressive FN minimization at low cost: Qwen 3 235B ($7/1K cases)**
- Adj. FN = 4 -- catches nearly every flagged case
- 26 missed labels (86% completeness) is reasonable
- Major downside: 87 FP (flags almost everything), so a second-pass filter is needed
- At $7/1K cases, budget allows pairing with a second model

**Best budget all-rounder: Gemini 3 Flash Preview ($16/1K cases)**
- 34 missed labels (81% completeness), Adj. FN = 21, low FP (13), F1 = 0.61
- Acceptable quality at a low price; good option if some missed extractions are OK

**Recommended two-stage strategy for low FN at scale:**
1. **Stage 1 -- Qwen 3 235B** ($7/1K): Extract citations with near-complete flagged recall (Adj. FN = 4)
2. **Stage 2 -- Kimi K2.5 or Claude Sonnet 4.6** on flagged results only: Filter out false positives
   - If ~70% of cases get flagged by Qwen, Stage 2 costs ~$26 (Kimi) or ~$97 (Sonnet) per 1K
   - **Total: ~$33-104/1K cases** with near-zero FN and much better precision than Qwen alone

### Models to avoid for this task

| Model | Reason |
|---|---|
| GPT OSS 120B | Only matched 6/183 labels -- effectively non-functional for citation extraction |
| Llama 3.3 70B | Only matched 42/183, 0 flagged F1, 141 missed labels |
| GPT 4.1 | 29 FN out of 32 matched flagged cases (misses 91% of flags) |
| Gemma 3 27B | Only matched 64/183, 119 missed labels |
| DeepSeek-R1 | 107 missed labels despite being one of the most expensive models |
| GPT 4o Mini | 105 missed labels -- poor citation extraction despite low cost |

---

*This analysis was generated with the assistance of Claude (Anthropic).*
