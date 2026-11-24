# Annotation guidelines: case names

Rules for the two gold fields. The pre-annotation and the human pass both
follow this file. Rules marked "ruling of 2026-11-23" were decided during the review of the gold set.

## What is being labeled

Each record is the first 2,000 characters of a federal district or bankruptcy
court document. Two fields are recorded:

| Field | What it is |
|---|---|
| `case_name_full` | The caption as printed on the document, lightly cleaned |
| `case_name` | The Bluebook-style short name, derived from `case_name_full` |

## `case_name_full`: the caption as printed

Copy the caption exactly. Do not shorten it, reorder it or correct it, and keep the punctuation between parties as the text has it.

**Keep**

- Every party on both sides, in the order printed.
- Titles, offices and capacity wording: `Frank Bisignano, Commissioner of Social Security`, `in his official capacity as Warden`.
- Aliases and descriptions: `a/k/a`, `d/b/a`, `an individual`, `as Executor of the Estate of …`.
- Everything the caption prints about a party (ruling of 2026-11-23), including long explanations of placeholder parties: `John Doe #1 through John Doe #12, the last twelve names being fictitious and unknown to plaintiff, …`.
- `et al.` when the caption prints it. Do not add it when it is not printed.
- `In re`, `Ex parte`, `In the Matter of` as printed. Do not add a `v.` that is not there.
- The `v.` wherever the caption prints it, even when one side is not named (ruling of 2026-11-23): a caption with `ABDULLAH DILSOZ, Petitioner, vs. [blank], Respondent` is `Abdullah Dilsoz v.`, and a caption missing its first party is `v. Darrell Klute`. The short name mirrors it: `Dilsoz v.`, `v. Klute`.

**Leave out**

- Court name, division, judge names.
- Docket and case numbers, chapter numbers, filing stamps.
- The document title: `ORDER`, `MEMORANDUM OPINION`, `REPORT AND RECOMMENDATION`.
- Party-role labels that stand alone: `Plaintiff`, `Defendants`, `Petitioner`, `Respondent`, `Debtor`, `Appellant`.
- Caption box characters: `)`, `§`, `:`, `|`, rule lines.
- Identification numbers (ruling of 2026-11-23): inmate numbers, alien numbers, badge and shield numbers. `Jerome Jones (#110366) v. Kela Haynes` is `Jerome Jones v. Kela Haynes`. This is a stated exception to "keep everything the caption prints about a party": a number is an identifier, not part of a name. A number that is part of a placeholder name stays: `John Doe #1 through John Doe #12`.

**Clean up**

- Connectors become `v.`: `vs.`, `versus`, `-against-`, `-v-`.
- ALL CAPS becomes normal capitalization. Keep real acronyms and designators: `LLC`, `USA`, `FBI`, `N.A.`.
- Line breaks and runs of spaces become one space.

**More than one case in the caption**

- Record **every case the caption prints** (ruling of 2026-11-23), joined with `; ` in the order printed. This covers consolidated cases, counterclaim and third-party captions, and the member cases listed under "This document relates to" in multi-district litigation: `In re: Generic Pharmaceuticals Pricing Antitrust Litigation; Providence St. Joseph Health, et al., v. Actavis Holdco U.S., Inc., et al.; …`.
- A bankruptcy adversary proceeding has two: `In re: Nikia Nicole Patterson; Kori Henderson v. Nikia Nicole Patterson`.

**Problem captions**

- **Scanning errors** (ruling of 2026-11-23): copy the text exactly, errors included, and set the *text contains OCR error* flag. `TAMARA BAUGBHN` is recorded as `Tamara Baugbhn`, and `DAVID M. T., Il` as `David M. T., Il`. Do not correct a word, even when the right spelling appears elsewhere in the document: it is often unclear that something is an error, and a correction can change a name that was right.
- A word split across two lines by a hyphen is joined: `AMER- ICA` is `America`.
- Unreadable text: when the caption cannot be read at all, set the *text is garbled* flag. Do not guess names that cannot be read.
- Caption cut off at 2,000 characters: record the complete caption from the first 8,000 characters and set the *cut off* flag. If it is still cut off at 8,000, record what is visible.
- No caption in the first 2,000 characters, but one further down: record the caption from the first 8,000 characters and set the *not in the first 2,000 characters* flag, so scoring can report these apart from records the production input can answer.
- No caption anywhere in the document: leave both fields empty and set the *no case name in the document* flag. Do not take a name from the body text or from a cited case.
- **Letters** (ruling of 2026-11-23). A letter has no caption block. Its case name is the one that follows "Re:", when that line names the parties: `Re: Scott Cawthon v. Dung Vu` gives `Scott Cawthon v. Dung Vu`. A Re: line with only a docket number or a subject gives no case name. This applies to letters only; in every other document type the name comes from the formal caption.

## `case_name`: the short name

The short name is a rewritten form: "copy the text exactly" applies to `case_name_full` only. Derive the short name from `case_name_full` using Bluebook Rule 10.2.1 (convention confirmed
2026-11-20: Bluebook form, not the wording of the docket name). No word
abbreviations (`Commissioner`, not `Comm'r`), because CourtListener stores
case names unabbreviated.

| Rule | Example |
|---|---|
| First party on each side only; drop `et al.` | `Jon Van Huss, et al. v. Bill's Electric, Inc. and John Doe` → `Van Huss v. Bill's Electric, Inc.` |
| Individuals: surname only | `Jurell Crawford v. Gregory Patterson` → `Crawford v. Patterson` |
| Two surnames: use the last; keep a hyphenated surname whole | `Ana Maria Garcia Lopez` → `Lopez`; `Rosa Rivera-Lopez` → `Rivera-Lopez` |
| Drop titles, offices, capacity wording, aliases, descriptions | `Daniel Westburg v. Frank Bisignano, Commissioner of Social Security` → `Westburg v. Bisignano` |
| Party named only by office: keep the office | `Jane Roe v. Commissioner of Social Security` → `Roe v. Commissioner of Social Security` |
| Office followed by an institution and a place: keep the office title and the institution, drop the street address or place | `Warden/Facility Administrator, Denver Contract Detention Facility, Aurora, Colorado` → `Warden/Facility Administrator, Denver Contract Detention Facility`; `Warden, London Correctional Institution` stays |
| A suit brought on the government's behalf: government first | `James Alridge, Relator, on behalf of United States of America v. …` → `United States ex rel. Alridge v. …` |
| Name given as first name and initial: keep it as printed | `William Z. v. Frank Bisignano` → `William Z. v. Bisignano` |
| Organizations: full name. Drop `Inc.`, `LLC`, `Ltd.`, `N.A.` only when the name already has a business word such as `Co.`, `Corp.`, `Bank`, `Ass'n` (so `Bank of America, N.A.` → `Bank of America`, but `Capital One, N.A.` stays) | `Acme Widget Co., Inc.` → `Acme Widget Co.`; `NeuroColor, LLC` → `NeuroColor, LLC` |
| `United States of America` and `U.S.A.` → `United States` | `United States of America v. John Smith` → `United States v. Smith` |
| `State of X`, `Commonwealth of X` → `X` | `State of Washington v. …` → `Washington v. …` |
| Keep `City of`, `County of`, `Town of` at the start of a name | `City of Chamblee` |
| `In re`, no colon | `In re: Julie Lynn Roe` → `In re Roe` |
| Procedural phrases | `on behalf of`, `on the relation of` → `ex rel.`; `In the Matter of`, `Petition of` → `In re` |
| Drop a leading `The` | `The Travelers Indemnity Co.` → `Travelers Indemnity Co.` |

**More than one case**

- Consolidated cases: short name of the first case only.
- Bankruptcy adversary proceeding (confirmed 2026-11-23): adversary name first, then the debtor in parentheses: `Taylor v. Vanderpool (In re Vanderpool)`. Not the adversary name alone, and not the debtor alone.

**Never** change the spelling of a surname, and never reduce a party to a bare initial.
