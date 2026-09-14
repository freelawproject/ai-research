"""Passage-level seeder prompt: the LLM sees EXACTLY what the encoder sees —
a header naming the citing and target case, and one passage with every
mention of the target wrapped in [T]…[/T] — and labels that passage alone.

Built from the canonical `citator` prompt's definitions (case types, the
treatment lists with their descriptions, the Distinguished-by patterns and
citation signals) so the label vocabulary is identical; the instructions are
rewritten for a single passage. Output is one small JSON object.

    python passage_prompt.py        # writes prompts/triage_passage_seeder_v1.md
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "..", "citator-pipeline", "utils"))
from instructions import citator as CITATOR  # noqa: E402

VERSION = "triage_passage_seeder_v1"

PASSAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "enum": ["treatment", "cited_by", "posture", "not_about_target"],
                  "description": "treatment: the passage expresses a treatment of the marked case beyond citing it. "
                                 "cited_by: the passage cites, quotes, relies on or describes the marked case without treating it. "
                                 "posture: the marked case is the decision under review and the passage recounts procedural history. "
                                 "not_about_target: the marked span is not actually a reference to a case (mis-tag)."},
        "treatment": {"type": "string", "description": "When label is treatment: the treatment THIS passage expresses, from the defined lists. Otherwise 'Cited by'."},
        "caseHistory": {"type": ["string", "null"], "enum": ["Direct History", "Citing Reference", "Related Reference", None]},
        "actingCase": {"type": ["string", "null"], "description": "'Citing Case', the Acting Case's name or citation, or 'Implicit'; null when label is not treatment."},
        "evidence": {"type": ["string", "null"], "maxLength": 400,
                     "description": "The sentence(s) INSIDE the passage that express the treatment, copied verbatim; null when label is not treatment."},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["label", "treatment", "caseHistory", "actingCase", "evidence", "confidence"],
}


def _slice(text, start_marker, end_marker):
    a = text.index(start_marker)
    b = text.index(end_marker, a)
    return text[a:b]


PREAMBLE = """
You are an expert legal citator. You will be shown ONE passage from the opinion of a Citing Case, preceded by a header line that names the Citing Case and the Target Case. Inside the passage every reference to the Target Case is wrapped in [T] … [/T] markers (a full citation, a short citation, "Id.", "supra", or the case name). Other cases cited in the passage are NOT marked and are not your concern except as context.

Your task is to decide whether THIS PASSAGE expresses a treatment of the marked Target Case beyond simply citing it, and if so which treatment. Judge only what the passage shows. Do not infer treatment from what the opinion might say elsewhere, and do not treat reliance, quotation, or description of the Target Case as negative treatment.

Strictly follow the definitions below. Reason briefly, then answer with the JSON object described at the end.
"""

INSTRUCTIONS = """
<instructions>
1. **Read the passage for the marked case only.** The [T] markers tell you which case is being judged. When several cases are cited in the passage, treatment language may be directed at one of the OTHER cases; assign it to the Target Case only if the passage directs it at the Target Case.

2. **Decide the label.**
   - `treatment` — the passage itself applies, or reports that another court applied, a treatment to the Target Case: it overrules, abrogates, questions, disapproves, limits, criticizes, distinguishes, or declines to follow it; or it reports a cert. denial, affirmance, reversal, or overruling of it by another court ("as recognized by"). Use the decision process and the Distinguished-by / citation-signal guidance below.
   - `cited_by` — the passage cites, quotes, relies on, summarizes, or explains the Target Case with no negative or procedural treatment. This is the common case. "See", "accord", "e.g." and neutral parentheticals are `cited_by`.
   - `posture` — the Target Case is the decision under review in this appeal (the court below) and the passage recounts what that court did or held. Do not label the disposition here; posture passages are routed separately.
   - `not_about_target` — the marked span is not a reference to a case (a statute, a mis-tag).

3. **When label is `treatment`,** set `treatment` to exactly one entry from the lists in the definitions (the most negative that the passage supports), `caseHistory` to Citing Reference (the Citing Case applies it) or Related Reference (the passage reports another court's treatment; use the "as recognized by" form), `actingCase` accordingly, and `evidence` to the sentence(s) inside the passage that carry the treatment, copied verbatim.
   - Related Reference cue: "X, cert. denied, …", "X, aff'd, …", "X, overruled by Y", "(distinguishing X)" attributed to another court.
   - Cert. denied directionality: the case that SOUGHT cert is the target of "Cert. denied as recognized by"; the Supreme Court citation that denied it is merely cited.
   - If the passage both relies on the Target Case and distinguishes it on some point, the label is `treatment` with "Distinguished by".

4. **When label is not `treatment`,** set `treatment` to "Cited by" and `caseHistory`, `actingCase`, `evidence` to null.

5. `confidence`: "high" when the passage states the treatment (or its absence) explicitly, "medium" when implicit but clear, "low" when you are inferring.

6. Return ONLY the JSON object. No prose before or after it.
</instructions>
"""

EXAMPLES = """
<examples>
- Header: Target case: Felton v. Graves 174 F.2d 228. Passage: "We find the reasoning in [T]Felton v. Graves, 174 F.2d 228[/T], inapplicable to the facts here and distinguish it." → label treatment, treatment "Distinguished by", caseHistory Citing Reference, actingCase "Citing Case".
- Same passage but the Target Case is a DIFFERENT case cited in the previous sentence for a general rule → label cited_by.
- "…applicable statute of limitations. 403 U.S., at 221, distinguishing [T]Felton v. Graves, 174 F.2d 228[/T]." → label treatment, treatment "Distinguished as recognized by", caseHistory Related Reference, actingCase "403 U.S. 217".
- "[T]United States v. Bolton, 68 F.3d 396[/T], cert. denied, 116 S.Ct. 966 (1996)" → label treatment, treatment "Cert. denied as recognized by", Related Reference, actingCase "116 S.Ct. 966".
- "See [T]Parker v. Whitfield, 489 U.S. 312, 318[/T] (holding that the statute reaches conditional intent)." → label cited_by.
- "The district court, [T]467 F. Supp. 381[/T], granted the motion, holding that the claim was time-barred. We review de novo." (Target is the case below) → label posture.
- "[T]Grafton[/T] would now be regarded as repugnant to the power conferred upon Congress to regulate commerce among the several States." → label treatment, treatment "Questioned by", Citing Reference.
</examples>

Output schema:
"""


def build_passage_prompt():
    definitions = _slice(CITATOR, "<definitions>", "</definitions>") + "</definitions>\n"
    guidance = _slice(CITATOR, "   **Distinguished by — identifying implicit distinguishing:**",
                      "   **Mandatory verification step")
    return (PREAMBLE.strip() + "\n\n" + definitions + "\n<guidance>\n" + guidance + "</guidance>\n"
            + INSTRUCTIONS + EXAMPLES + json.dumps(PASSAGE_SCHEMA, indent=2) + "\n")


def render_user_message(system_prompt, header, passage_text):
    """Chat-completions user message: prompt + the encoder's exact input."""
    return f"{system_prompt}\n\n<passage>\n{header}\n\n{passage_text}\n</passage>\n\nJSON:"


if __name__ == "__main__":
    p = build_passage_prompt()
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts", f"{VERSION}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"<!-- {VERSION}: generated by passage_prompt.py from the canonical `citator` definitions; "
                f"edit passage_prompt.py, not this file -->\n\n" + p)
    print(f"wrote {out}: {len(p):,} chars (~{len(p) // 4:,} tokens)")
