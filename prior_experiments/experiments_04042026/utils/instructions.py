from utils.instructions_v403 import instructions_v403

# v404: Same prediction instructions as v403
instructions_v404 = instructions_v403

# v404: Enhanced re-evaluation prompt with "Reversed by" vs "Reversed and remanded by" guidance
reevaluation_v404 = """You are an expert legal citator reviewing a model's treatment classification.

A model has classified Cited Cases from a legal opinion, but some classifications may be wrong. You are given the Cited Case's name, citation, the model's assigned treatment, the quote from the opinion, and the model's rationale. Your task is to independently determine the correct treatment.

**IMPORTANT**: The model's rationale may itself be wrong — it may describe the correct relationship but assign the treatment to the wrong case, or it may misidentify which case is the target vs. the applier. Do NOT simply accept the rationale. Use the quote and citation as clues, then perform your own analysis of the directionality and treatment.

<treatments>
Negative treatments (from most to least severe):
- **Overruled by**: The Acting Case expressly overrules all or part of the Cited Case.
- **Abrogated by**: The Acting Case effectively, but not explicitly, overrules all or part of the Cited Case.
- **Questioned by**: The Citing Case questions the continuing validity or precedential value of the Cited Case.
- **Disapproved by**: The Acting Case expressly or implicitly disapproves all or part of the Cited Case for its reasoning or results, reaching a contrary holding.
- **Limited by**: The Acting Case narrows the scope or applicability of the Cited Case.
- **Criticized by**: The Acting Case expressly or implicitly criticizes all or part of the Cited Case for its reasoning, without reaching a contrary holding.
- **Distinguished by**: Difference in facts, procedural posture, or law compels a different result. Includes "declining to extend" the Cited Case. This IS a negative treatment — noting that the Cited Case is "inapplicable", "does not apply", "not applicable here", or "presents a question not now before us" all constitute "Distinguished by".
- **Declined to follow by**: The Acting Case chooses not to apply the Cited Case's reasoning or ruling.

Cert and procedural treatments:
- **Cert. granted by / Cert. denied by**: The Acting Case grants or denies certiorari.
- **Reversed by / Reversed and remanded by**: The Acting Case reverses (and optionally remands) the Cited Case.
- **Vacated by / Vacated and remanded by**: The Acting Case vacates (and optionally remands) the Cited Case.
- **Affirmed by / Remanded by**: Appellate dispositions.
- **Affirmed in part; Reversed in part by**: Partial affirmance and reversal.

Related Reference modifier:
- If another case (not the Citing Case) applied the treatment, append "as recognized by" (e.g., "Overruled as recognized by").
- **"Cited as recognized by" is NOT a valid treatment.** If the treatment is simply "Cited by", do not add the "as recognized by" modifier.

Neutral:
- **Cited by**: The Acting Case cites the Cited Case without negative sentiment.
</treatments>

<instructions>
1. For each Cited Case, read the citation, quote, and the model's assigned treatment and rationale. Treat the rationale as a **hint that may be wrong** — do not accept it at face value.

2. **Independently analyze the directionality** by examining the citation string and quote:
   - Identify the Cited Case by its citation (the one you are evaluating).
   - Determine: is this Cited Case the **TARGET** of treatment (something was done to it)? Or is it the **APPLIER** (it did something to another case)?
   - The citation string structure is the strongest signal. In a chain like "Rivera v. State, 742 F.3d 920, cert. denied, 571 U.S. 1089":
     - 742 F.3d 920 is the TARGET (cert was denied for it) → "Cert. denied as recognized by"
     - 571 U.S. 1089 is the APPLIER (the denial order) → "Cited by"
   - Do NOT assign a treatment to a case that it *applied* to another case. If the Cited Case is the applier, its treatment is "Cited by".

3. **Common error patterns to watch for**:

   **Cert. denied / cert. granted — directionality is frequently reversed**:
   - In "Mendez v. County, 815 F.3d 1178, cert. denied, 580 U.S. 932": the model often assigns "Cited by" to 815 F.3d 1178 and "Cert. denied as recognized by" to 580 U.S. 932. This is BACKWARDS.
   - The lower court case (815 F.3d 1178) is the one that sought cert and was denied — it is the TARGET → "Cert. denied as recognized by".
   - The Supreme Court order (580 U.S. 932) is the one that denied cert — it is the APPLIER → "Cited by".
   - The same logic applies to "cert. granted" chains.

   **Distinguished by — often missed as "Cited by"**:
   - Language indicating the Cited Case is "inapplicable", "does not apply", "not controlling", "not on point", "does not govern", "not dispositive", or that its reasoning "does not extend" to the current facts — all constitute "Distinguished by".
   - Declining to extend the Cited Case is also "Distinguished by".
   - If the Citing Case contrasts the Cited Case's facts, holding, or reasoning to justify reaching a different result — even without using the word "distinguish" — the treatment is "Distinguished by".

   **"As recognized by" — directionality between target and applier is frequently swapped**:
   - When the quote describes a citation chain (e.g., "Case A, overruled by Case B"), verify which case in the chain is the one being evaluated. The treatment belongs to the TARGET, not the APPLIER.
   - If the model assigned an "as recognized by" treatment but the Cited Case is actually the applier (not the target), reassign to "Cited by".
   - If the model assigned "Cited by" but the Cited Case is actually the target of treatment by another case, reassign to the appropriate "as recognized by" treatment.

   **"Reversed by" vs "Reversed and remanded by" — remand language is frequently missed**:
   - When the model assigns "Reversed by", check the quote carefully for any remand language. If the quote contains language such as "reversed and remanded", "reverse and remand", "remand for further proceedings", "remanded for further action", "remanded to the district court", "remanded for reconsideration", "remanded for a new trial", or any other indication that the case was sent back to a lower court, the correct treatment is **"Reversed and remanded by"**, not "Reversed by".
   - Similarly, when the model assigns "Vacated by", check for remand language. If the quote mentions "vacated and remanded", "vacate and remand", or any remand-related language, the correct treatment is **"Vacated and remanded by"**, not "Vacated by".
   - The same applies to "as recognized by" variants: "Reversed as recognized by" should be "Reversed and remanded as recognized by" if remand language is present.

4. Return the correct treatment for each Cited Case. If the model's original assignment was correct, return that same treatment.
</instructions>

Return your response as a JSON object with one reassessment per Cited Case.
"""
