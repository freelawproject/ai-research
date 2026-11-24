# Pre-annotation brief

Instructions for one sub-agent run. One run handles one batch file.

## Task

Read the caption of each court document in a batch and record two case names
for it. A person reviews every answer afterwards, so mark doubt instead of
guessing.

## Steps

1. Read `annotation_guidelines.md` in this folder. It defines both fields and
   is the only source of rules.
2. Read the batch file given in the prompt. It is a JSON list of
   `{"cluster_id", "text"}`. `text` is the first 2,000 characters of a document.
3. For each record, find the caption in `text`, then write `case_name_full`,
   then derive `case_name` from it.
4. Write the output file given in the prompt, then reply with one line:
   records written, and how many were low confidence.

## Output

A JSON list with one object per input record, in the same order:

```json
{
  "cluster_id": 11066574,
  "case_name_full": "Jurell Crawford v. Gregory Patterson",
  "case_name": "Crawford v. Patterson",
  "caption_in_input": "complete",
  "confidence": "high",
  "notes": ""
}
```

| Field | Values |
|---|---|
| `caption_in_input` | `complete`: whole caption is in the text. `partial`: the caption is cut off by the 2,000-character limit. `none`: no caption in the text. |
| `confidence` | `high`, `medium`, `low` |
| `notes` | One short sentence when something needs a person's attention: garbled names, several cases, an unusual caption. Otherwise empty. |

- **Both names are required for every record that has a caption.** Never fill
  `case_name_full` and leave `case_name` empty, or the reverse.
- `caption_in_input` = `none`: both names are empty strings.
- `caption_in_input` = `partial`: record what is visible.
- Every `cluster_id` in the batch appears exactly once. Valid JSON only.

## Limits

- Use only the batch text. Do not look up the case, read other files in this
  folder, or use the docket name.
- Every word of `case_name_full` must come from the text. Do not complete a
  name from memory.
- Do not run commands other than reading the two input files and writing the
  one output file.
