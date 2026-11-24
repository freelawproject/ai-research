# Case-name evaluation set

Evaluation data and metrics for `classify_case_name_by_llm` in CourtListener
([courtlistener#8325](https://github.com/freelawproject/courtlistener/issues/8325)).
Built 2026-11-20 to 2026-11-23.

## Terms

- **Record:** one opinion imported from a RECAP document, with its text.
- **Full caption** (`case_name_full`): the case name as the document prints it, with every party.
- **Short name** (`case_name`): the Bluebook-style short form, such as `Crawford v. Patterson`.
- **Production:** the names stored on the opinion cluster today, written by `gpt-4o-mini` with the production prompt.
- **Gold:** the accepted correct name for a record.
- **Model input:** the first 2,000 characters of the document text. This is all the production model reads.

## Bottom line

**The gold set is complete, and production scores well on full captions and near zero on short names.**

- **Gold set:** 2,000 records with both names, split into 1,000 validation and 1,000 test.
- **Full caption:** production matches gold on an estimated 91% of imported opinions.
- **Short name:** production matches gold on under 1%, because the model leaves the short name out of its reply.
- **Use:** tune and compare replacement models on the validation split. Report final numbers on the test split.

## What is evaluated

When a RECAP document is imported as an opinion, the new cluster copies its
names from the docket. `classify_case_name_by_llm` then reads the document and
overwrites `case_name_full` and `case_name`.

| Setting | Value |
|---|---|
| Model | `gpt-4o-mini`, called through `instructor` |
| Prompt | `CASE_NAME_EXTRACT_SYSTEM` in `cl/corpus_importer/prompts/system.py` |
| Input | Model input only: no docket name, no metadata |
| Limits | Temperature 0, 300 output tokens |
| Courts | Federal district courts (civil dockets) and bankruptcy courts |

`gpt-4o-mini` is being retired, so replacement models need a labeled set to be
compared on.

## How the records were selected

The sample is 2,000 records drawn from every cluster the task could have
processed. It is stratified, so the cases that break the model are well
represented.

**Pool.** Clusters with source RECAP, created on or after 2025-10-15, with
opinion text. The task took its current form on 2025-10-14.

**Strata.** Each cluster falls in one stratum. The first matching rule wins.

| Stratum | Rule | Pool | Sampled | Why it is there |
|---|---|---:|---:|---|
| `bankruptcy` | Bankruptcy court | 4,095 | 300 | "In re" captions and lawsuits inside a bankruptcy |
| `social_security` | Nature of suit, or "Commissioner of Social Security" in the model input | 10,984 | 300 | Claimants named by first name and initial |
| `in_re_or_consolidated` | Docket name starts "In re", or "consolidated" in the model input | 2,368 | 250 | More than one case in a caption |
| `long_caption` | Production full caption of 250 or more characters | 6,268 | 250 | Long captions hit the output limit |
| `name_unchanged` | Cluster names still equal the docket names | 597 | 250 | The model wrote nothing |
| `general` | Everything else | 150,460 | 650 | The ordinary case |
| **Total** | | **174,772** | **2,000** | |

Pool sizes were counted with a start date of 2025-10-03, twelve days earlier
than the sample's. The `name_unchanged` pool is the most overstated by this.

- **Court spread.** Within a stratum, records are taken one court at a time, so busy courts do not dominate.
- **Reproducible.** The order comes from a hash of the cluster id and a fixed seed.
- **Splits.** Records are ordered by stratum and hash, then assigned alternately. Each split holds exactly half of every stratum.

## What the sample looks like

The sample covers 180 courts and 13 months, and most captions are short and
hold one case.

| Property | Value | What it means |
|---|---|---|
| Courts | 180: 93 district, 87 bankruptcy | Nearly every court in scope appears |
| Records per court | Median 10, largest 77 (S.D.N.Y.) | No court exceeds 4% of the sample |
| Court type | 1,700 district, 300 bankruptcy | Bankruptcy is oversampled; it is 2% of the pool |
| Created | 2025-10-15 to 2026-11-20 | 294 in 2025 Q4; 340, 326, 662 and 378 in the four quarters of 2026 |
| Document length | Median 17,455 characters | The model input is the first 11% of a typical document |
| Shorter than the model input | 37 records | These are read whole |
| Text flagged as OCR | 1,297 of 2,000 | The flag is set on 65% of records, so it does not mark poor scans |

**Document type.** Types come from text rules that look at the headings. They
have not been checked by hand.

| Type | Records |
|---|---:|
| Opinion | 813 |
| Order | 607 |
| Report and recommendation | 266 |
| Other | 110 |
| Judgment | 95 |
| Standing order | 63 |
| Letter to the court | 28 |
| Minutes | 15 |
| Letter from the court | 3 |

**Gold captions.** The gold names show how varied the task is.

| Property of the gold name | Records | What it means |
|---|---:|---|
| No case name in the document | 32 | Standing orders, minutes and some letters. The right answer is empty. |
| One case in the caption | 1,720 | The usual form |
| Two or more cases | 240 | Consolidated cases, counterclaims and bankruptcy lawsuits |
| Full caption starts with "In re" | 353 | Bankruptcy and multi-district matters |
| Full caption has "et al." | 478 | The caption itself abbreviates the parties |
| Full caption over 300 characters | 235 | 37 exceed 1,000 characters; the longest is 3,539 |
| Short name ends in "(In re Debtor)" | 153 | Lawsuits inside a bankruptcy |

Full captions have a median length of 67 characters, and short names 29.

**Where the caption sits.** The production model cannot fully see 156 captions.

| Segment | Records | Meaning |
|---|---:|---|
| Answerable | 1,812 | The whole caption is inside the model input |
| Beyond the model input | 156 | The caption starts after 2,000 characters (74) or runs past them (82) |
| No case name | 32 | The document has no case name |

## How the gold names were created

Each gold name is a value on which two independent sources agree, or one a
reviewer saved. Three models and one reviewer took part.

| Source | What it produced | Input it saw |
|---|---|---|
| Claude Opus, run as sub-agents | Both names for all 2,000 records, following `annotation_guidelines.md` | Model input only |
| Production | Names already on the cluster | Model input only |
| Original docket name | The docket's `case_name` | Not from the document |
| GPT-6.2 Sol, batch API | Both names for 1,330 records, following the same guidelines | Model input, or 8,000 characters for the wider read |
| `gpt-4o-mini`, production prompt | Full caption for 183 records | First 8,000 characters |
| Reviewer | Every name the sources did not settle | Full text, all of the above |

**Acceptance rule.** The Opus value is the starting value for every record. It
is accepted without review when a second source gives the same name, ignoring
capitalization, spacing and punctuation.

| Field | How the name was accepted | Records |
|---|---|---:|
| Full caption | Opus matches production | 1,437 |
| Full caption | Opus matches GPT-6.2 Sol | 312 |
| Full caption | Opus matches `gpt-4o-mini` on 8,000 characters | 50 |
| Full caption | Saved by the reviewer | 201 |
| Short name | Opus matches the original docket name | 862 |
| Short name | Opus matches GPT-6.2 Sol | 1,064 |
| Short name | Saved by the reviewer | 74 |

The reviewer saved the Opus value unchanged on 187 of 201 full captions and 63
of 74 short names. The reviewer worked in a local app that shows the document
beside each source's value, with the differing words marked.

**Wider read.** Opus found no caption, or a cut-off one, in the model input of
183 records. All three models then read the first 8,000 characters of those
records.

| Result at 8,000 characters | Records |
|---|---:|
| Caption complete | 148 |
| Caption still cut off | 9 |
| No caption | 26 |

Gold names taken from the wider read carry a flag, so scoring can report them
apart from records the model input can answer.

**Flags on gold records.**

| Flag | Records | Meaning |
|---|---:|---|
| `caption_cut_off` | 82 | The caption runs past the model input |
| `caption_beyond_input` | 74 | The caption starts after the model input |
| `no_caption` | 32 | The document has no case name |
| `ocr_error` | 6 | The text has a scanning error, copied as it is |
| `garbled_text` | 3 | The text cannot be read |

**Rules.** `annotation_guidelines.md` holds the full rules. The ones that shape
the gold most:

- **Full caption:** copy the text exactly, scanning errors included. Keep every party, every case and everything printed about a party.
- **Left out of the full caption:** court name, case numbers, stand-alone role labels such as "Plaintiff", and identification numbers such as inmate numbers.
- **Short name:** Bluebook Rule 10.2.1, with no word abbreviations. A lawsuit inside a bankruptcy is written `Taylor v. Vanderpool (In re Vanderpool)`.
- **Letters:** the case name is the one after "Re:". Every other document type needs a formal caption.

**Checks.** `annotator/check_gold.py` tests every label against these rules and
against the other name of the same record. It runs 24 checks; the two
remaining hits are a word joined across a line break and one caption the
reviewer chose to keep as a single case.

**Cost.** The model runs cost about $30 to $45 at API rates.

| Run | Tokens | Estimated cost | Basis |
|---|---|---|---|
| Claude Opus sub-agents, 48 runs | About 6.2 million | $25 to $40 | $4 input and $20 output per million; the input and output split was not recorded |
| GPT-6.2 Sol, 4 batches | 3.29 million input, 0.15 million output | $2 to $4 | $2 input and $10 output per million, halved for batch. Rates are from third-party pages, not confirmed. |
| `gpt-4o-mini`, 283 calls | Small | Under $0.10 | |

## How predictions are scored

`scoring/score.py` compares a model's names with the gold names, one field at
a time.

| Metric | What counts |
|---|---|
| **Match** | The predicted name equals the gold name, ignoring capitalization, spacing and punctuation. An empty gold name is matched by an empty prediction. |
| **Strict match** | The predicted name equals the gold name character for character, after collapsing spaces. |
| **Word overlap** | The share of words the two names have in common, from 0 to 1. It gives part credit to a name that is nearly right. |
| **Returned** | The prediction is not empty. |

Higher is better for all four. Match is the headline metric. Strict match adds
capitalization and punctuation on top of it.

## Production against gold

Production gets the full caption right for most ordinary cases and returns
almost no short names.

**Overall, on the stratified sample.** These rates describe the sample, which
oversamples hard cases.

| Metric | Validation, 1,000 records | Test, 1,000 records | What it means |
|---|---:|---:|---|
| Full caption: match | 0.744 | 0.729 | About 3 in 4 sampled captions are right |
| Full caption: strict match | 0.649 | 0.648 | 1 in 10 is right apart from capitalization or punctuation |
| Full caption: word overlap | 0.865 | 0.856 | Wrong captions still share most of their words with gold |
| Full caption: returned | 0.875 | 0.875 | 1 in 8 sampled clusters has no full caption |
| Short name: match | 0.054 | 0.068 | About 1 in 17 short names is right |
| Short name: returned | 0.141 | 0.144 | 6 in 7 clusters have an empty short name |
| Both names match | 0.000 | 0.000 | No record has both names right |

**Estimated for all imported opinions.** Weighting each stratum by its pool
size gives the rate over the whole pool. The figures are approximate, because
the pool sizes use the earlier start date.

| Metric | Validation | Test | What it means |
|---|---:|---:|---|
| Full caption: match | 0.91 | 0.91 | About 9 in 10 imported opinions have the right full caption |
| Full caption: returned | 1.00 | 1.00 | The model writes a full caption almost every time |
| Short name: match | 0.002 | 0.005 | Fewer than 1 in 200 has the right short name |
| Short name: returned | 0.03 | 0.03 | About 3 in 100 clusters have any short name |

**By segment.** Production cannot answer what it cannot see.

| Segment | Validation records | Full caption match, validation | Test records | Full caption match, test | What it means |
|---|---:|---:|---:|---:|---|
| Answerable | 915 | 0.788 | 897 | 0.795 | The fair test of the model |
| Beyond the model input | 67 | 0.090 | 89 | 0.045 | A wider input, not a better model, fixes these |
| No case name | 18 | 0.944 | 14 | 0.857 | Production is right when it leaves the name empty |

**By stratum.**

| Stratum | Records per split | Full caption match, validation | Full caption match, test | Short name returned, validation | What it means |
|---|---:|---:|---:|---:|---|
| `general` | 325 | 0.929 | 0.929 | 0.028 | Strong on ordinary cases |
| `social_security` | 150 | 0.980 | 0.960 | 0.007 | Strong; these captions are short and regular |
| `in_re_or_consolidated` | 125 | 0.896 | 0.848 | 0.024 | Some captions lose a case |
| `long_caption` | 125 | 0.712 | 0.664 | 0.024 | Long party lists are cut or reworded |
| `bankruptcy` | 150 | 0.513 | 0.547 | 0.000 | Plain "In re" captions are right; lawsuits inside a bankruptcy are mostly wrong |
| `name_unchanged` | 125 | 0.136 | 0.096 | 1.000 | The model wrote nothing; the short name is the docket's |

**Why the short name is empty.** The model's reply leaves the short name out,
and the task saves an empty string in its place. A replay of the production
call on 100 validation records returned a short name once.

- **Cause:** both names are optional in the response schema, so a reply with only a full caption passes validation.
- **Effect on readers:** pages fall back to the full caption, so the gap is not visible on the site.
- **Short names that are present** come from the `name_unchanged` stratum, where the cluster still holds the docket name.

**Where the full caption goes wrong.** Of 915 answerable validation records,
194 do not match gold.

| Cause | Records | Example: production, then gold |
|---|---:|---|
| No full caption written | 72 | *(empty)* → `Phelps Mineral Holdings, LLC v. Continental Resources, Inc.` |
| Small wording difference | 40 | `… Robert G. Doucette, Jr., TD Bank …` → `… Robert G. Doucette, Jr, and TD Bank …` |
| Fewer cases than the caption prints | 33 | `National Loan Acquisitions Company v. Jerome W.H. Niswonger …` → `In re: Jerome W.H. Niswonger …; National Loan Acquisitions Company v. …` |
| Bankruptcy and lawsuit merged | 31 | `In re: Karen Lee Bishop v. Gary Bishop …` → `In re: Karen Lee Bishop; Gary Bishop … v. Karen Lee Bishop` |
| Parties or wording left out | 10 | `Farnam Street Financial, Inc. v. …` → `Farnam Street Financial, Inc., a Minnesota corporation v. …` |
| Extra wording | 8 | `Abdullah Dilsoz v. Respondent` → `Abdullah Dilsoz v.` |

The 72 empty captions are sampled on purpose through `name_unchanged`. They are
about 0.3% of the pool.

## Limits

- **The gold favors the sources that built it.** 1,437 full captions were accepted because Opus and production agreed, and no reviewer read them. A shared mistake would be counted as correct.
- **Opus wrote the starting value for every record.** A future Claude model may match the gold's style choices more often than another model family.
- **Pool sizes use the earlier start date.** The weighted estimates move slightly when the strata are recounted from 2025-10-15.
- **Corrections cannot be fully checked.** A model that silently fixed a scanning error leaves a label that differs from the text. The check finds this only when the corrected word appears nowhere in the document.
- **Document types are unverified.** Letters are detected reliably; the opinion and order types are not.
- **Third-party models built part of the gold.** A replacement model from the same family as a gold source needs this stated beside its score.

## Conclusion

- **Full captions are close to production quality already.** A replacement model has to beat 0.79 match on answerable records and 0.91 across the pool.
- **Short names are the gap.** Any model that returns a correct short name beats production. The docket name alone matches the gold short name on 42% of sampled validation records.
- **Three fixes are independent of the model:** require the short name in the response schema, raise the 300-token output limit, and widen the input. In the sample, 156 of 2,000 captions sit beyond 2,000 characters.
- **Bankruptcy lawsuits need a prompt rule.** Production matches gold on 8 of 74 validation captions that hold a lawsuit inside a bankruptcy, against 69 of 75 plain "In re" captions.
- **Dependence on an external model remains.** A retirement, a price change or a silent update forces a new run of this evaluation.

## Files

| Path | Purpose |
|---|---|
| `annotation_guidelines.md` | Rules for the two gold names |
| `docs/reproduce.md` | Commands for every step |
| `data/gold/validation.jsonl`, `data/gold/test.jsonl` | Gold records: model input, both names, flags, document type, how each name was accepted. Not committed. |
| `scoring/score.py` | Scores predictions against gold; `test_score.py` tests the metrics |
| `results/production.json` | Production scores by split, segment, stratum and document type |
| `annotator/` | Review app, label store scripts and `check_gold.py` |
| `sampling/` | Replica queries |
| `prod_replay/` | Production call replay |
