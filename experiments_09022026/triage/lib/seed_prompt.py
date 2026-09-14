"""Seeder prompt for the triage experiment — built FROM the canonical
`citator` prompt (citator-pipeline/utils/instructions.py), not a rewrite.

What changes vs `citator` (everything else is verbatim):
  1. Identification: the model no longer discovers/groups citations. Every
     mention is tagged `<citedCase group="N">` and a `<citedCaseInventory>`
     lists each group once (id, name, citation). One decision per inventory id.
  2. Compact output: return an entry ONLY for inventory ids whose treatment is
     anything other than "Cited by", plus `citedByIds` listing every id judged
     plain "Cited by". Every inventory id must appear in exactly one place, so
     coverage is checkable. Output stays small enough for 4K–16K output caps.
  3. PASSAGE-LEVEL output: a treated case lists EVERY passage that treats
     it, each with its own treatment, verbatim quote (so we can locate it in
     the text) and confidence. The encoder learns from passages, so one
     quote per case would leave every other treating passage unlabeled.
  4. Opinion-type comes from the enclosing <lead>/<concurrence>/<dissent> tag.
  5. Schema is inlined in the user message (Kimi / GLM have no tool_use).

    python seed_prompt.py          # writes prompts/triage_seeder_v1.md
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "..", "citator-pipeline", "utils"))
from instructions import citator as CITATOR  # noqa: E402

VERSION = "triage_seeder_v2"

PASSAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "quote": {"type": "string", "maxLength": 500,
                  "description": "Verbatim passage (copied exactly, tags removed) in which the treatment is expressed. Must include or sit next to a tagged mention of the case."},
        "treatment": {"type": "string", "description": "The treatment THIS passage expresses, from the defined lists, never 'Cited by'."},
        "caseHistory": {"type": "string", "enum": ["Direct History", "Citing Reference", "Related Reference"]},
        "actingCase": {"type": "string", "description": "'Citing Case', the Acting Case's name or citation, or 'Implicit'."},
        "opinionType": {"type": "string", "description": "Lead, Plurality, Concurrence, or Dissent, optionally with '(Footnote)' — from the tag enclosing the passage."},
        "rationale": {"type": "string", "maxLength": 300},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["quote", "treatment", "caseHistory", "actingCase", "opinionType", "rationale", "confidence"],
}

SEEDER_SCHEMA = {
    "type": "object",
    "properties": {
        "citedCases": {
            "type": "array",
            "description": "One entry per inventory id that receives ANY treatment beyond 'Cited by' anywhere on this page.",
            "items": {
                "type": "object",
                "properties": {
                    "inventoryId": {"type": "integer", "description": "The id from <citedCaseInventory> / the group attribute."},
                    "passages": {"type": "array", "minItems": 1, "items": PASSAGE_SCHEMA,
                                 "description": "EVERY passage that treats this case beyond plain citation, one entry each, in document order. Passages that merely cite the case are not listed."},
                },
                "required": ["inventoryId", "passages"],
            },
        },
        "citedByIds": {"type": "array", "items": {"type": "integer"},
                       "description": "Every inventory id judged plain 'Cited by' (no treatment)."},
    },
    "required": ["citedCases", "citedByIds"],
}


def _slice(text, start_marker, end_marker):
    a = text.index(start_marker)
    b = text.index(end_marker, a)
    return text[a:b]


PREAMBLE = """
You are an expert legal citator. Your goal is to determine whether a citation would **negatively** impact a lawyer's reliance on a case in any jurisdiction for any purpose.

You will be given (1) a `<citingCase>` tag naming the Citing Case, (2) a `<citedCaseInventory>` listing every Cited Case that appears in the opinion, each with an `id`, and (3) the opinion of the Citing Case (enclosed in <opinion> tags). The Citing Case is always an appellate opinion — it is a court reviewing the decision of a lower court on appeal. Inside the opinion, every mention of a Cited Case is wrapped in a `<citedCase group="N">` tag whose N is the inventory `id`; all mentions of the same case share one N. Sub-opinions are wrapped in `<lead>`, `<plurality>`, `<concurrence>`, `<dissent>` (and similar) tags.

Your task is to decide, for every inventory id, the treatment the Citing Case's opinion applies to or reports about that Cited Case, following the definitions and decision process below exactly. The opinion may be one page of a longer opinion; judge only what this page shows.

Strictly follow the definitions and instructions below. Before producing your final JSON output, reason through each inventory id explicitly: locate its tagged mentions, determine its Case History type and Acting Case, then determine its treatment and the opinion type that governs it. Ensure your response adheres exactly to the required output format.
"""

IDENTIFY = """
1. **Identifying the Cited Cases**
   - The Cited Cases are given: one per inventory `id`. Do not add cases that are not in the inventory and do not merge or split inventory entries. If a tagged mention seems mis-grouped, still judge it under the id on its tag.
   - Locate every `<citedCase group="N">` mention of each id. Read the sentence and paragraph around each mention; the treatment is decided from those passages together with the opinion's disposition (for the case on appeal).
   - The lower court decision under review may appear in the inventory only as an unnamed or partially named entry (a docket-style citation, "the court below"). It still gets a decision.
   - Some inventory ids may have mentions only on another page of a long opinion; if an id has no tagged mention on this page, put it in `citedByIds` unless the page's text plainly treats it.
"""

OUTPUT = """
4. **Output Format**
   - Return ONLY a JSON object that complies with the schema provided below. No prose before or after it.
   - **Compact output.** Include an entry in `citedCases` ONLY for inventory ids that receive a treatment other than "Cited by" somewhere on this page. List every remaining inventory id (the ones you judged plain "Cited by" everywhere) in `citedByIds`. Every inventory id must appear exactly once across `citedCases` and `citedByIds`; do not omit any and do not repeat any.
   - **Passage-level entries.** A Cited Case is often mentioned in several places; usually most of those mentions merely cite it and one or a few treat it. For each treated Cited Case, list EVERY passage in which a treatment is expressed — one `passages` entry per passage, in document order — and give each passage its own `treatment`, `caseHistory`, `actingCase`, and `opinionType`. Different passages may carry different treatments (e.g. distinguished in one section, questioned in a footnote); report each as it stands. Do not list passages that merely cite, quote, or rely on the case without treating it. Do not merge two distant passages into one entry.
   - A passage is the sentence or consecutive sentences in which the treatment is expressed; `quote` is that text copied verbatim (tags removed), 500 characters at most, and it must contain or sit immediately next to a tagged mention of that Cited Case so a reviewer can locate it. If the treatment is derived from a parenthetical, quote the full citation string including the parenthetical. If the treatment can only be inferred from the opinion's disposition (Direct History), quote the disposition sentence.
   - `rationale` is 1-2 sentences connecting the quote to the specific treatment label.
   - `confidence` is "high" when the passage states the treatment explicitly, "medium" when it is implicit but clear, "low" when you are inferring.
   - `opinionType` comes from the tag enclosing the passage (`<lead>` → "Lead", `<concurrence>` → "Concurrence", `<dissent>` → "Dissent", `<plurality>` → "Plurality"); append "(Footnote)" if the passage is a footnote.
"""

EXAMPLE_10 = """**Example 10: Valid JSON Output (compact, passage-level)**
Suppose the inventory has ids 1 through 7. Id 3 is mentioned five times: three mentions merely cite it, one passage questions it and a later footnote distinguishes it. Id 5 is the decision under review. Ids 1, 2, 4, 6, 7 are plain "Cited by" everywhere:
```json
{
  "citedCases": [
    {
      "inventoryId": 3,
      "passages": [
        {
          "quote": "In view of the course of decisions that have been made since that time, it is very certain that such an ordinance as applied by Grafton would now be regarded as repugnant to the power conferred upon Congress to regulate commerce among the several States.",
          "treatment": "Questioned by",
          "caseHistory": "Citing Reference",
          "actingCase": "Citing Case",
          "opinionType": "Lead",
          "rationale": "The passage says subsequent decisions have undermined the Cited Case, questioning its continuing validity.",
          "confidence": "high"
        },
        {
          "quote": "Grafton involved a municipal ordinance directed at interstate carriers and is therefore inapplicable to the intrastate tax before us.",
          "treatment": "Distinguished by",
          "caseHistory": "Citing Reference",
          "actingCase": "Citing Case",
          "opinionType": "Lead (Footnote)",
          "rationale": "The footnote explains why the Cited Case does not govern on its facts.",
          "confidence": "high"
        }
      ]
    },
    {
      "inventoryId": 5,
      "passages": [
        {
          "quote": "For the reasons stated above, the judgment of the Court of Appeals is reversed.",
          "treatment": "Reversed by",
          "caseHistory": "Direct History",
          "actingCase": "Citing Case",
          "opinionType": "Lead",
          "rationale": "Id 5 is the decision under review and the disposition reverses it.",
          "confidence": "high"
        }
      ]
    }
  ],
  "citedByIds": [1, 2, 4, 6, 7]
}
```

</examples>
"""


def build_seeder_prompt():
    definitions = _slice(CITATOR, "<definitions>", "</definitions>") + "</definitions>\n"
    decision = _slice(CITATOR, "2. **Determining Treatment", "4. **Output Format**")
    examples = _slice(CITATOR, "<examples>", "**Example 10")
    return (PREAMBLE.strip() + "\n\n" + definitions + "\n<instructions>\n" + IDENTIFY
            + "\n" + decision + OUTPUT + "\n</instructions>\n\n" + examples + EXAMPLE_10)


def render_user_message(system_prompt, citing_case, inventory_block, page_text,
                        page_idx=0, n_pages=1):
    """Single user message for chat-completions models without tool_use:
    system prompt + inputs + inlined schema."""
    cc = " ".join(f'{k}="{str(v).replace(chr(34), chr(39))}"' for k, v in citing_case.items() if v)
    page_note = f"\n<pageInfo page=\"{page_idx + 1}\" of=\"{n_pages}\" />" if n_pages > 1 else ""
    return (
        f"{system_prompt}\n\n<citingCase {cc} />{page_note}\n{inventory_block}\n\n"
        f"<opinion>\n{page_text}\n</opinion>\n\n"
        "Return your response as a JSON object conforming to this schema:\n"
        f"{json.dumps(SEEDER_SCHEMA, indent=2)}"
    )


if __name__ == "__main__":
    p = build_seeder_prompt()
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts", f"{VERSION}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"<!-- {VERSION}: generated by seed_prompt.py from the canonical `citator` prompt; "
                f"edit seed_prompt.py, not this file -->\n\n" + p + "\n\n---\n\nOutput schema (inlined in the user message):\n\n```json\n"
                + json.dumps(SEEDER_SCHEMA, indent=2) + "\n```\n")
    print(f"wrote {out}: {len(p):,} chars (~{len(p) // 4:,} tokens); canonical citator is {len(CITATOR):,} chars")
