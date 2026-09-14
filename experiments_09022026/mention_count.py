"""How many times the citing opinion mentions a cited case vs the applied
treatment, using the gold coreference groups (<cite cited_ref=...>) in the
reviewed revised_html opinions. Applied treatment from treatments.csv."""
import collections
import csv
import os
import re

from common import BENCH, category, load_predictions, spearman

finals = {(r["citing_cluster_id"], r["cited_ref"]): r
          for r in csv.DictReader(open(f"{BENCH}/treatments.csv")) if r["final_treatment"]}
pred = load_predictions()
cref = {r["cited_ref"]: r["cited_cluster_id"] for r in csv.DictReader(open(f"{BENCH}/cited_cases.csv"))}
recs = []
for f in os.listdir(f"{BENCH}/revised_html"):
    cid = f[:-5]
    html = open(f"{BENCH}/revised_html/{f}").read()
    cnt = collections.Counter(m.group(1) for m in re.finditer(r'<cite [^>]*cited_ref="([^"]+)"', html))
    for ref, n in cnt.items():
        if (r := finals.get((cid, ref))):
            recs.append((n, category(r["final_treatment"]), pred.get((cid, cref.get(ref, ref)))))
print(f"reviewed opinions: {len(os.listdir(f'{BENCH}/revised_html'))}; pairs: {len(recs)}")


def bucket(n):
    return "1" if n == 1 else "2" if n == 2 else "3-5" if n <= 5 else "6-10" if n <= 10 else "11+"


tab = collections.defaultdict(collections.Counter)
for n, c, _ in recs:
    tab[bucket(n)][c] += 1
print(f"\n{'mentions':8s} {'pairs':>6s} {'Cited by':>9s} {'CitRef':>7s} {'DH':>4s} {'P(treatment)':>13s}")
for b in ("1", "2", "3-5", "6-10", "11+"):
    c = tab[b]
    tot = sum(c.values())
    print(f"{b:8s} {tot:6d} {c['Cited by']:9d} {c['Citing Reference']:7d} "
          f"{c['Direct History']:4d} {(tot - c['Cited by']) / tot:13.1%}")
xs = [n for n, _, _ in recs]
ys = [0 if c == "Cited by" else 1 for _, c, _ in recs]
print(f"\nSpearman rho (mentions vs any treatment): {spearman(xs, ys):.3f}")
byc = collections.defaultdict(list)
for n, c, _ in recs:
    byc[c].append(n)
for c, v in byc.items():
    v.sort()
    print(f"  {c:18s} n={len(v):5d} median={v[len(v) // 2]} mean={sum(v) / len(v):.1f} "
          f"single-mention={sum(x == 1 for x in v) / len(v):.0%}")
neg = [n for n, c, _ in recs if c != "Cited by"]
for k in (2, 3):
    print(f"gate mentions>={k}: keeps {sum(x >= k for x in xs) / len(xs):.0%} of pairs, "
          f"captures {sum(x >= k for x in neg) / len(neg):.0%} of treatments")
fp = [n for n, c, p in recs if p and c == "Cited by" and p != "Cited by"]
tp = [n for n, c, p in recs if p and c != "Cited by" and p != "Cited by"]
print(f"\nmodel false flags (final Cited by, model negative): n={len(fp)} "
      f"single-mention={sum(x == 1 for x in fp) / len(fp):.0%}")
print(f"model true flags: n={len(tp)} single-mention={sum(x == 1 for x in tp) / len(tp):.0%}")
