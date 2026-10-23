"""Step 1 — can each candidate decision model take a whole Dahl opinion?

Reads the Dahl citing clusters from the external benchmark, loads the 0911 gate inputs
(tagged opinion + cited-case inventory), counts tokens with two tokenizers, and checks the
fit against every candidate's context window. Writes data/opinion_length.csv and prints a
markdown table for the readme.
"""
import csv
import json
import os
import re
import sys

import tiktoken
from transformers import AutoTokenizer

HERE = os.path.dirname(os.path.abspath(__file__))
EXT_CSV = os.path.join(HERE, "..", "..", "external-benchmark", "output", "external_benchmark.csv")
INPUTS = os.path.join(HERE, "..", "experiments_09112026", "data", "inputs")
OUT_CSV = os.path.join(HERE, "data", "opinion_length.csv")
QWEN_TOKENIZER = "Qwen/Qwen3-0.6B"  # same 151K vocab as the Qwen3.x backbones of mammoth/elephant/pplx-decider

# Per-question limit = state + one question (Jev); per-request = state + all questions.
# Models with one figure apply it to the whole request.
MODELS = [
    # name, per_request, per_question, note
    ("jev-1.14 default", 64_000, 32_000, "262K with the long-context header"),
    ("jev-1.14 long-ctx", 262_000, 262_000, "slower, less accurate per TypeSafe"),
    ("mammoth-1", 16_384, 16_384, "refused, not truncated, past the cap"),
    ("elephant-1", 16_384, 16_384, "refused, not truncated, past the cap"),
    ("pplx-decider-v1.2", 262_144, 262_144, "trained to ~32K"),
    ("d1-ultra", 32_768, 32_768, ""),
]
TAG = re.compile(r"<[^>]+>")
QUESTION_TOKENS = 40  # rough size of one yes/no question ("Does the Citing Case treat <case N> as more than merely cited?")


def dahl_ids():
    with open(EXT_CSV, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["source"] == "dahl"]
    ids = {}
    for r in rows:
        ids.setdefault(r["citing_cluster_id"], r["citing_cl_case_name"])
    return ids


def load(cid):
    with open(os.path.join(INPUTS, f"{cid}.tagged.txt"), encoding="utf-8") as f:
        tagged = f.read()
    with open(os.path.join(INPUTS, f"{cid}.inventory.json"), encoding="utf-8") as f:
        inv = json.load(f)
    return tagged, inv


def fits(tokens, n_questions, per_request, per_question):
    one = tokens + QUESTION_TOKENS
    all_q = tokens + n_questions * QUESTION_TOKENS
    return one <= per_question and all_q <= per_request


def main():
    enc = tiktoken.get_encoding("o200k_base")
    qwen = AutoTokenizer.from_pretrained(QWEN_TOKENIZER)

    rows = []
    for cid, name in dahl_ids().items():
        tagged, inv = load(cid)
        plain = TAG.sub("", tagged)
        n_q = len(inv)
        r = {
            "cluster_id": cid, "case_name": name, "cited_cases": n_q,
            "chars_plain": len(plain), "chars_tagged": len(tagged),
            "o200k_plain": len(enc.encode(plain)), "o200k_tagged": len(enc.encode(tagged)),
        }
        r["qwen_plain"] = len(qwen.encode(plain))
        r["qwen_tagged"] = len(qwen.encode(tagged))
        tok = r["qwen_tagged"]
        for m, per_req, per_q, _ in MODELS:
            r[f"fit:{m}"] = "yes" if fits(tok, n_q, per_req, per_q) else "no"
        rows.append(r)
    rows.sort(key=lambda r: r["o200k_tagged"])

    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    tokcol = "qwen_tagged"
    print(f"Fit uses the tagged opinion in {tokcol} tokens + {QUESTION_TOKENS} tokens per cited-case question.\n")
    print("| cluster | case | cited cases | o200k plain | o200k tagged | qwen tagged | " + " | ".join(m for m, *_ in MODELS) + " |")
    print("|---|---|---|---|---|---|" + "---|" * len(MODELS))
    for r in rows:
        print(f"| {r['cluster_id']} | {r['case_name']} | {r['cited_cases']} | {r['o200k_plain']:,} | {r['o200k_tagged']:,} | "
              f"{r['qwen_tagged']:,} | " + " | ".join(r[f"fit:{m}"] for m, *_ in MODELS) + " |")
    print()
    print("| model | opinions that fit whole | of 17 |")
    print("|---|---|---|")
    for m, *_ in MODELS:
        n = sum(r[f"fit:{m}"] == "yes" for r in rows)
        print(f"| {m} | {n} | {n}/17 |")
    toks = [r[tokcol] for r in rows]
    print(f"\nmedian {tokcol}: {sorted(toks)[len(toks)//2]:,}; max {max(toks):,}; total {sum(toks):,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
