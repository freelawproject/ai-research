"""Where does the evidence for a negative treatment sit relative to mentions
of the target case? For every gold non-Cited-by pair with an expert quote:
locate the quote in the citing opinion text and measure the distance to the
nearest mention of the cited case (reporter cite or short party name). Also
count other <citedCase> tags near the quote and how many opinions fit an 8K
token window whole."""
import collections
import csv
import os
import re

from common import BENCH, TEXTS, category, norm_ws

TAG = re.compile(r"</?citedCase>")
cited = {r["cited_ref"]: r for r in csv.DictReader(open(f"{BENCH}/cited_cases.csv"))}
rows = [r for r in csv.DictReader(open(f"{BENCH}/treatments.csv"))
        if r["final_treatment"] and r["final_treatment"] != "Cited by"
        and r["final_quote"]]
STOP = {"the", "of", "inc", "co", "corp", "city", "county", "board", "company",
        "dept", "department", "state"}


def cite_keys(c):
    out = []
    for cit in (c.get("citations") or "").split(";"):
        m = re.match(r"(\d+)\s+([A-Za-z. \d]+?)\s+(\d+)$", cit.strip())
        if m:
            out.append((m.group(1), m.group(3)))
    return out


def short_names(c):
    n = c.get("case_name_short") or c.get("case_name") or ""
    out = []
    for p in re.split(r"\s+v\.?\s+", n)[:2]:
        p = re.sub(r"^(United States|In re|Ex parte|State|People|Commonwealth)\b.*", "", p)
        w = [x for x in re.findall(r"[A-Z][A-Za-z'\-]+", p) if x.lower() not in STOP]
        if w:
            out.append(w[0])
    return out


def locate(text, q):
    for L in (80, 50, 30):
        for start in (0, max(0, len(q) // 2 - L // 2)):
            frag = q[start:start + L]
            if len(frag) >= 20 and (pos := text.find(frag)) >= 0:
                return pos
    return -1


buckets, bycat, density = collections.Counter(), collections.defaultdict(collections.Counter), []
for r in rows:
    path = f"{TEXTS}/{r['citing_cluster_id']}.txt"
    if not os.path.exists(path):
        buckets["no text"] += 1
        continue
    raw = open(path).read()
    text = norm_ws(TAG.sub("", raw))
    q = norm_ws(r["final_quote"]).strip(" .…")
    pos = locate(text, q)
    if pos < 0:
        buckets["quote not found"] += 1
        continue
    c = cited.get(r["cited_ref"], {})
    ment = []
    for vol, pg in cite_keys(c):
        ment += [m.start() for m in re.finditer(rf"\b{vol}\s+[A-Za-z.\s\d]{{1,14}}?\s+{pg}\b", text)]
    for nm in short_names(c):
        ment += [m.start() for m in re.finditer(rf"\b{re.escape(nm)}\b", text)]
    if not ment:
        buckets["no mention found"] += 1
        continue
    qend = pos + len(q)
    d = min(0 if pos <= m <= qend else min(abs(m - pos), abs(m - qend)) for m in ment)
    b = ("inside quote" if d == 0 else "<=500" if d <= 500 else "<=2000"
         if d <= 2000 else "<=8000" if d <= 8000 else ">8000")
    buckets[b] += 1
    bycat[category(r["final_treatment"])][b] += 1
    rawn = norm_ws(raw)
    if (rp := rawn.find(q[:50])) >= 0:
        density.append(rawn[max(0, rp - 500):rp + len(q) + 500].count("<citedCase>"))

n = len(rows)
print(f"gold non-Cited-by pairs with an expert quote: {n}")
for k in ("inside quote", "<=500", "<=2000", "<=8000", ">8000",
          "no mention found", "quote not found", "no text"):
    print(f"  {buckets[k]:4d} ({buckets[k] / n:5.1%})  {k}")
print("\nby category (chars from quote to nearest target mention):")
for c, cnt in bycat.items():
    tot = sum(cnt.values())
    near = cnt["inside quote"] + cnt["<=500"]
    print(f"  {c:18s} n={tot:3d}  within 500: {near / tot:.0%}  "
          f"beyond 8000: {cnt['>8000'] / tot:.0%}  {dict(cnt)}")
density.sort()
print(f"\n<citedCase> tags within ±500 chars of the quote: median "
      f"{density[len(density) // 2]}, p90 {density[int(len(density) * .9)]}, "
      f">=2: {sum(x >= 2 for x in density) / len(density):.0%}, "
      f">=4: {sum(x >= 4 for x in density) / len(density):.0%}")
lens = sorted(len(TAG.sub("", open(f"{TEXTS}/{f}").read())) for f in os.listdir(TEXTS))
for cap in (32000, 16000):
    print(f"opinions <= {cap:,} chars (~{cap // 4:,} tokens) whole: "
          f"{sum(l <= cap for l in lens)}/{len(lens)} = "
          f"{sum(l <= cap for l in lens) / len(lens):.0%}")
