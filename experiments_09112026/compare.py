"""Pipeline comparison table from scored runs.

  bash run.sh compare.py --gate g4_gpt_test,g4b_gpt_test --labels l_opus_test l_opus2_test l_sonnet_test l_sonnet2_test

For each labeler run (and the 0529 baseline on the same pairs), under both conventions:
end-to-end accuracy, false-flag rate on Cited-by pairs, per-class P/R for classes with >= 20
examples, macro F1, positives recovered, and cost per opinion (gate + labeler, on-demand as
measured and at Bedrock batch rates = half for the labeler). Runs score.py in-process.
"""
import argparse
import json
import os
import subprocess
import sys

from common import MODELS, RUNS, cluster_ids, read_json

HERE = os.path.dirname(os.path.abspath(__file__))


def score(label, gate, conv):
    cmd = [sys.executable, os.path.join(HERE, "score.py"), "--label", label, "--gate", gate, "--baseline", "0529_sonnet"]
    if conv:
        cmd.append("--dissent-as-cited")
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    return json.loads(out[: out.rfind("}") + 1])


def cost_per_opinion(gate, label, n_opinions):
    g = sum(read_json(os.path.join(RUNS, n, "summary.json"))["cost_usd"] for n in gate.split(","))
    ls = read_json(os.path.join(RUNS, label, "summary.json"))
    lm = read_json(os.path.join(RUNS, label, "meta.json"))
    l_cost = ls["cost_usd"]
    batch_ok = MODELS[lm["model"]]["batch"]
    return g / n_opinions, l_cost / n_opinions, (g + l_cost) / n_opinions, (g + (l_cost / 2 if batch_ok else l_cost)) / n_opinions


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", required=True)
    ap.add_argument("--labels", nargs="+", required=True)
    a = ap.parse_args()
    first_gate = a.gate.split(",")[0]
    split = read_json(os.path.join(RUNS, first_gate, "meta.json"))["split"]
    n_op = len(cluster_ids(split))
    print(f"split={split} ({n_op} opinions), gate={a.gate}\n")
    for conv in (False, True):
        print(f"## convention: {'dissent/concurrence-only treatment = Cited by' if conv else 'as labeled'}")
        hdr = "| pipeline | acc | positives recovered / exact | false-flag | macro F1 (n cls) | " + " | ".join(f"{c} P/R" for c in ("Distinguished by", "Criticized by")) + " | $/op on-demand | $/op batch |"
        print(hdr); print("|" + "---|" * (hdr.count("|") - 1))
        base_done = False
        for lab in a.labels:
            j = score(lab, a.gate, conv)
            E = j["end_to_end"]; L = j["labeler"]; m = read_json(os.path.join(RUNS, lab, "meta.json"))
            g_c, l_c, tot, tot_b = cost_per_opinion(a.gate, lab, n_op)
            row = (f"| gate → {m['model']} / {os.path.basename(m['prompt'])} | {E['accuracy_vs_majority']:.3f} | "
                   f"{E['positives_recovered']:.2f} / {E['positives_exact']:.2f} | "
                   f"{E['negative_flag_rate_on_cited_by_pairs']:.1%} | {E['macro_f1']:.3f} ({E['macro_f1_classes']}) | "
                   + " | ".join(f"{E['per_class'].get(c, {}).get('p', 0):.2f} / {E['per_class'].get(c, {}).get('r', 0):.2f}" for c in ("Distinguished by", "Criticized by"))
                   + f" | ${tot:.3f} | ${tot_b:.3f} |")
            print(row)
            if not base_done:
                B = j["baseline_0529_sonnet"]
                print(f"| 0529 Sonnet single-stage | {B['accuracy_vs_majority']:.3f} | {B['positives_recovered']:.2f} / {B['positives_exact']:.2f} | {B['negative_flag_rate_on_cited_by_pairs']:.1%} | "
                      f"{B['macro_f1']:.3f} ({B['macro_f1_classes']}) | "
                      + " | ".join(f"{B['per_class'].get(c, {}).get('p', 0):.2f} / {B['per_class'].get(c, {}).get('r', 0):.2f}" for c in ("Distinguished by", "Criticized by"))
                      + " | ≈$0.186 | $0.093 |")
                base_done = True
        print()


if __name__ == "__main__":
    main()
