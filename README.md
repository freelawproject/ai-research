# FLP_Citator

## Foreword
This project is the result of community effort within the Free Law Project and the broader legal community, see [GitHub ticket](https://github.com/freelawproject/courtlistener/issues/4963) for discussions and previous experiments.

## Goal
To create an AI Citator that helps users identify good law vs bad law through Case Law opinions.

### Legal Background
A legal citator is a research tool that helps users verify the validity and trace the history of court opinions, statutes, and other legal authorities. At its most basic level, a citator indicates whether a cited case has been overruled or remains good law. In practice, the treatment of a case can involve a range of classifications that vary in depth and impact — affirmed, modified, reversed, criticized, distinguished, explained, overruled, questioned, and more.

A comprehensive citator helps a user assess whether a case remains good law or has been undermined, guiding their decision on whether to use the case as precedent.

### Multi-Stage Goals
Since creating a comprehensive citator is a complex problem, we broke it down into more attainable bite-sized projects:

1. Stage 1: Overrule vs Not Overrule (SCOTUS)
2. Stage 2: Finegrained Classifications (Affirm, Overturn, Question, Disapprove, Criticize, etc.) (SCOTUS)
3. Stage 3: Strength and Depth of Treatment (Fully/Partially/Specific Topics, etc) (SCOTUS)
4. Stage 4: Appellate Chain Classifications
5. Stage 5: Full Table of Authorities with Color-Coded Finegrained Classifications (including reversals and expand to courts outside of SCOTUS)

## Existing Solutions
The most notable and widely used citators are [LexisNexis' Shepard's Citations](https://www.lexisnexis.com/en-us/products/lexis/shepards.page) and [WestLaw's KeyCite](https://legal.thomsonreuters.com/en/products/westlaw/keycite). Some other solutions are [CaseText SmartCite](https://help.casetext.com/en/articles/3630136-smartcite-report-cite-check-and-analyze-a-brief), [vLex](https://support.vlex.com/document-types/case-law/cited-authorities), and [Paxton AI](https://www.paxton.ai/post/introducing-the-paxton-ai-citator-setting-new-benchmarks-in-legal-research). None of these solutions are freely available to the public.

Some datasets that can be used for training/evaluations:
1. [RegLab by Stanford](https://reglab.stanford.edu/data/)
2. [Congress.gov Decisions Overruled](https://constitution.congress.gov/resources/decisions-overruled/)
3. [Spaeth Dataset on Reversals](http://scdb.wustl.edu/documentation.php)

## Where things live

| | Location | Purpose |
|---|---|---|
| Current pipeline code | `citator-pipeline/` | Reusable production code (prompts, run scripts, postprocessors) |
| Conventions + pipeline reference | `CLAUDE.md` | Pipeline structure, run instructions, canonical treatment taxonomy |
| Cross-experiment progression | `comparison.md` | Headline metrics across all experiments since prompt-version selection |
| Active experiments (2026) | `experiments_MMDD2026/` | Each folder has its own readme + notebooks |
| Historical experiments (2025) | `prior_experiments/` | See `prior_experiments/readme.md` for the baseline + v1 + 2025 status updates |

For the latest evaluation results, refer to the most recent `experiments_MMDD2026/` folder's readme. Specific metrics and costs are kept in the per-experiment writeups rather than here, to avoid this file going stale.
