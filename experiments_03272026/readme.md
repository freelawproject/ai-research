# What this folder contains
1. Continue working with the same eval set
Eval set: 110274, 117849, 103543, 110420, 110360, 7774813, 725046, 101625
2. Use a cheaper model to:
   - Identify and extract cited cases (citation string & case name)
   - Determine whether the cited case was likely to have been treated negatively by the citing/acting case
3. If the cheaper model is able to produce good enough results, then use a larger model to:
   - Determine the treatment applied towards the cited cases with likely negative treatments (as produced by the cheaper model)
   - Potentially ask the model to produce all negative treatments and use a heuristic approach to present the most negative treatment as the final treatment

## Cheaper models to consider:

- Within Bedrock:
   - Claude Sonnet 4.6
   - Claude Haiku 4.5
   - Gemma 3 27B
   - Llama 4 Maverick 17B
   - Llama 3.3 70B
   - DeepSeek V3.2
   - DeepSeek-R1
   - GPT OSS 120B
   - Kimi K2.5
   - Qwen 3 Next 80B
   - Qwen 3 235B

- Outside of Bedrock:
   - GPT 5.4 Mini
   - GPT 4o Mini
   - GPT 4.1
   - GPT 4.1 Mini
   - Gemini 3 Flash Preview
   - Gemini 3.1 Pro Preview

## Conclusion:

1. Sonnet 4.6 is the best, Kimi is a close second, Haiku 4.5 and Gemini models are worth trying out
2. Based on the budget analysis, it probably makes sense to just use the v318 prompt and Sonnet 4.6 with batch processing to get results for ~500 cases for expert's annotation (50 cases per circuit court)
3. Next step is to try the few models that worth looking into on the existing benchmark data to see how it performs, use batch requests to make the cost more manageable. And use Sonnet 4.6 to get results for cases for annotation.