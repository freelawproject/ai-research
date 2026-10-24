"""Rule post-filter over a Clairic prediction file: keep spans that look like case-citation mentions.

  filter_predictions.py --pred data/pred_d2.json [--out data/pred_d2_filtered.json] [--inputs data/inputs]

Keep classes (span text, whitespace-normalised):
  reporter  volume + reporter + page, optional pincite, incl. the "301 U. S., at 640" short form
  id        Id. / Ibid. / id. forms
  supra     anything containing "supra"
  name      a case-name reference: contains " v. ", or is 1–4 capitalised tokens with ≥ 4 letters each,
            not in the legal-noun stoplist — and NOT directly followed by a reporter citation or "supra"
            (a name standing before its own citation is not a mention under the convention)
Everything else (single words, fragments, digits, section signs, constitutional/statutory references)
is dropped. Overlapping kept spans collapse to the earliest-longest. The rules were written after
looking at the D1/D2 dev output, so the filtered dev score is tuned on dev; say so wherever it is quoted.
"""
import argparse
import json
import os
import re
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
RE_REPORTER = re.compile(r"\b\d{1,4}\s+[A-Z][A-Za-z0-9.'&\-]*(?:\s+[A-Z][A-Za-z0-9.'&\-]*){0,4}\.?,?\s+(?:at\s+)?\d{1,5}\b")
RE_ID = re.compile(r"^(?:id\.|ibid\.?|id\b)", re.I)
RE_FOLLOWS_CITE = re.compile(r"^[\s,.;:\-—–]*(?:\(?\d{1,4}\s+[A-Z]|supra\b)")
RE_NAME_TOKEN = re.compile(r"^[A-Z][A-Za-z'\-]{3,}\.?,?$")
STOP = {"The", "This", "That", "These", "Those", "Court", "Courts", "Congress", "Constitution", "Amendment",
        "Clause", "Rule", "Rules", "Code", "Act", "State", "States", "United", "Government", "Commission",
        "Board", "District", "Circuit", "Judge", "Justice", "Chief", "Plaintiff", "Plaintiffs", "Defendant",
        "Defendants", "Appellant", "Appellee", "Petitioner", "Respondent", "Federal", "Supreme", "Section",
        "Title", "Article", "Chapter", "Part", "Commerce", "Medicaid", "Medicare", "County", "City", "Company",
        "Corporation", "Department", "Secretary", "President", "Senate", "House", "Committee", "Opinion",
        "Dissent", "Majority", "Brief", "Record", "Appendix", "Trial", "Jury", "Order", "Judgment", "Statute",
        "Statutes", "Legislature", "Attorney", "General", "Counsel", "Motion", "Petition", "Complaint",
        "Indictment", "Fourteenth", "First", "Second", "Third", "Fourth", "Fifth", "Sixth", "Eighth", "Tenth",
        "Eleventh", "Equity", "Common", "Law", "Laws", "Reports", "Report", "Term", "Term,", "American",
        "National", "International", "Railroad", "Railway", "Insurance", "Bank", "Trust", "Union"}


def classify(text, following):
    t = re.sub(r"\s+", " ", text).strip()
    if not t:
        return None
    if RE_REPORTER.search(t):
        return "reporter"
    if RE_ID.match(t):
        return "id"
    if "supra" in t.lower():
        return "supra"
    if RE_FOLLOWS_CITE.match(following):
        return None
    if " v. " in t or " v " in t:
        return "name"
    toks = t.split()
    if 1 <= len(toks) <= 4 and all(RE_NAME_TOKEN.match(x) for x in toks) and not all(x.strip(".,") in STOP for x in toks):
        return "name"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", required=True)
    ap.add_argument("--out")
    ap.add_argument("--inputs", default=os.path.join(HERE, "data", "inputs"))
    a = ap.parse_args()
    with open(a.pred, encoding="utf-8") as f:
        pred = json.load(f)
    kept, dropped, before, after = Counter(), Counter(), 0, 0
    for cid, rec in pred.items():
        with open(os.path.join(a.inputs, f"{cid}.json"), encoding="utf-8") as f:
            texts = {op["idx"]: op["text"] for op in json.load(f)["opinions"]}
        for op in rec["opinions"]:
            text = texts[op["opinion_idx"]]
            out, last_end = [], -1
            for m in sorted(op["mentions"], key=lambda x: (x["start"], -x["end"])):
                before += 1
                k = classify(text[m["start"]:m["end"]], text[m["end"]:m["end"] + 12])
                if k is None:
                    dropped["rule"] += 1
                    continue
                if m["start"] < last_end:
                    dropped["overlap"] += 1
                    continue
                kept[k] += 1
                last_end = m["end"]
                out.append(m)
            op["mentions"] = out
            after += len(out)
    out_path = a.out or a.pred.replace(".json", "_filtered.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(pred, f, ensure_ascii=False)
    print(f"{os.path.basename(a.pred)}: {before:,} → {after:,} mentions; kept {dict(kept)}; dropped {dict(dropped)} → {out_path}")


if __name__ == "__main__":
    main()
