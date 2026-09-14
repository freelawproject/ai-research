"""Human leave-one-out ceiling vs 0529 Sonnet on the current benchmark.

Human LOO: each expert vote scored against the majority of the OTHER experts
on the same pair (removes the self-vote bias of scoring against the final).
Model: 0529 Sonnet prediction scored (a) against the final treatment on every
predicted pair and (b) against the same others'-majority on the same votes.
"""
import collections

from common import (base, category, expert_votes, leave_one_out, load_finals,
                    load_predictions, macro_f1, prf, severity)

finals = load_finals()
pred = load_predictions()
both = [k for k in finals if k in pred]

print("== Benchmark ==")
print(f"pairs with final: {len(finals)}; citing clusters: "
      f"{len({k[0] for k in finals})}; expert votes: "
      f"{sum(len(expert_votes(r)) for r in finals.values())}")
dist = collections.Counter(base(r["final_treatment"]) for r in finals.values())
print("finals by label (base form):", dist.most_common(8))
print("non-Cited-by finals:", sum(v for k, v in dist.items() if k != "Cited by"))
src = collections.Counter(r["final_treatment_source"] for r in finals.values())
print("finals by source:", src.most_common())

print("\n== Human leave-one-out ==")
loo = list(leave_one_out(finals))
pairs = [(h, m) for _, h, m in loo]
n = len(pairs)
print(f"votes scored: {n}")
print(f"exact-label agreement: {sum(h == m for h, m in pairs) / n:.3f}")
print(f"severity-tier agreement: "
      f"{sum(severity(h) == severity(m) for h, m in pairs) / n:.3f}")
neg = [(h, m) for h, m in pairs if m != "Cited by"]
print(f"agreement when others say NOT Cited by: "
      f"{sum(h == m for h, m in neg) / len(neg):.3f} (n={len(neg)})")
f = prf(pairs, base)
mf, k = macro_f1(f)
print(f"macro-F1 (base form, {k} classes with support>=20): {mf:.3f}")
for c, v in sorted(f.items(), key=lambda x: -x[1][3])[:8]:
    print(f"  F1={v[0]:.2f} P={v[1]:.2f} R={v[2]:.2f} n={v[3]:5d}  {c}")

print("\n-- by category --")
fc = prf(pairs, category)
for c, v in sorted(fc.items(), key=lambda x: -x[1][3]):
    sub = [(h, m) for h, m in pairs if category(m) == c]
    same = sum(category(h) == c for h, m in sub)
    exact = sum(h == m for h, m in sub)
    print(f"  {c:18s} n={v[3]:5d} catF1={v[0]:.2f} P={v[1]:.2f} R={v[2]:.2f} "
          f"same-cat={same / len(sub):.2f} exact={exact / len(sub):.2f} "
          f"exact|same-cat={exact / same:.2f}")
conf = collections.Counter((category(m), category(h)) for h, m in pairs
                           if category(h) != category(m))
print("  category confusions (others -> held-out):", conf.most_common(6))

print("\n== 0529 Sonnet vs final (all predicted pairs) ==")
print(f"predicted pairs: {len(pred)}; overlap with finals: {len(both)}; "
      f"finals with no prediction: {len(finals) - len(both)}")
mp = [(pred[k], finals[k]["final_treatment"]) for k in both]
bf = [(base(p), base(g)) for p, g in mp]
print(f"exact agreement: {sum(p == g for p, g in mp) / len(mp):.3f}")
print(f"severity-tier agreement: "
      f"{sum(severity(p) == severity(g) for p, g in mp) / len(mp):.3f}")
f = prf(bf)
mf, k = macro_f1(f)
print(f"macro-F1 (base form, {k} classes support>=20): {mf:.3f}")
for c, v in sorted(f.items(), key=lambda x: -x[1][3])[:10]:
    print(f"  F1={v[0]:.2f} P={v[1]:.2f} R={v[2]:.2f} n={v[3]:5d}  {c}")
fc = prf(mp, category)
print("-- by category --")
for c, v in sorted(fc.items(), key=lambda x: -x[1][3]):
    print(f"  {c:18s} n={v[3]:5d} catF1={v[0]:.2f} P={v[1]:.2f} R={v[2]:.2f}")
by = collections.defaultdict(list)
for k in both:
    by[finals[k]["final_treatment_source"]].append(
        (base(pred[k]), base(finals[k]["final_treatment"])))
print("-- agreement by label source --")
for s, pp in sorted(by.items(), key=lambda x: -len(x[1])):
    print(f"  {len(pp):6d} acc={sum(p == g for p, g in pp) / len(pp):.3f} {s}")
conf = collections.Counter((g, p) for p, g in bf if p != g)
print("-- top confusions (final -> model) --")
for (g, p), c in conf.most_common(8):
    print(f"  {c:4d} {g} -> {p}")
cited = [(p, g) for p, g in bf if g == "Cited by"]
print(f"share of unanimous-or-majority Cited-by pairs given a non-Cited-by "
      f"label by the model: {sum(p != g for p, g in cited) / len(cited):.3f}")

print("\n== Same votes: human LOO vs model, both vs others' majority ==")
hum, mod = [], []
for k, h, m in leave_one_out(finals, keys=set(both)):
    hum.append((base(h), base(m)))
    mod.append((base(pred[k]), base(m)))
for tag, pp in (("human", hum), ("model", mod)):
    f = prf(pp)
    mf, k = macro_f1(f)
    neg = [(p, g) for p, g in pp if g != "Cited by"]
    print(f"{tag}: n={len(pp)} acc={sum(p == g for p, g in pp) / len(pp):.3f} "
          f"macroF1({k} cls)={mf:.3f} negTail acc="
          f"{sum(p == g for p, g in neg) / len(neg):.3f}")
    big = sorted((c for c, v in f.items() if v[3] >= 20), key=lambda c: -f[c][3])
    print("   ", {c: round(f[c][0], 2) for c in big})

print("\n== Extraction misses ==")
unp = [k for k in finals if k not in pred]
ran = {k[0] for k in pred}
print(f"finals with no prediction: {len(unp)}; of which citing opinion WAS run: "
      f"{sum(k[0] in ran for k in unp)}")
print("  labels:", collections.Counter(base(finals[k]["final_treatment"])
                                        for k in unp).most_common(4))
