"""Q5b — Are same-(court, date, docket) duplicates actually different cases?

Standalone step: runs in ai-research (NOT the container), because opinion
embeddings live in the prod S3 bucket.

    uv run python q5b_embedding_similarity.py \
        --data-dir data --threshold 0.97

Inputs (copied into --data-dir from the container):
  q4_duplicate_groups.json  (from q4)
  q5_opinion_ids.json       (from q5a: cluster_id -> [opinion_id, ...])

For each cluster: fetch its opinions' embeddings from S3, take the first N
chunks (default 10) across the cluster's opinions in order, mean-pool -> one
vector. For each duplicate group: pairwise cosine between cluster vectors;
label the group "distinct_cases" if min pairwise cosine < threshold, else
"true_duplicate". The threshold should be calibrated from the reported
distribution + q5_examples.csv, not taken as gospel.

Writes q5_duplicate_content.json + q5_examples.csv into --data-dir.
"""

import argparse
import csv
import json
import os
from concurrent.futures import ThreadPoolExecutor

import boto3
import numpy as np
from botocore.exceptions import ClientError

AWS_PROFILE = os.environ.get("AWS_PROFILE", "dev-env")
BUCKET = os.environ.get("CL_STORAGE_BUCKET", "com-courtlistener-storage")
MODEL = "freelawproject/modernbert-embed-base_finetune_512"
PREFIX = f"embeddings/opinions/{MODEL}/"
FIRST_N_CHUNKS = 10
MAX_WORKERS = 16


def make_s3():
    return boto3.Session(profile_name=AWS_PROFILE).client("s3")


def fetch_opinion_chunks(s3, opinion_id):
    """Return list of (chunk_number, np.array) for one opinion, or [] if missing."""
    key = f"{PREFIX}{opinion_id}.json"
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=key)
    except ClientError:
        return None  # missing / no access
    data = json.loads(obj["Body"].read().decode("utf-8"))
    chunks = data.get("embeddings", []) or []
    out = []
    for c in chunks:
        emb = c.get("embedding")
        if emb:
            out.append((c.get("chunk_number", 0), np.asarray(emb, dtype=np.float32)))
    out.sort(key=lambda x: x[0])
    return out


def cluster_vector(opinion_ids, chunk_cache):
    """Mean-pool the first FIRST_N_CHUNKS chunks across the cluster's opinions."""
    collected = []
    for op_id in opinion_ids:
        chunks = chunk_cache.get(op_id)
        if not chunks:
            continue
        for _, vec in chunks:
            collected.append(vec)
            if len(collected) >= FIRST_N_CHUNKS:
                break
        if len(collected) >= FIRST_N_CHUNKS:
            break
    if not collected:
        return None
    return np.mean(np.vstack(collected), axis=0)


def cosine(a, b):
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--threshold", type=float, default=0.97,
                    help="min pairwise cosine below which a group is 'distinct'")
    args = ap.parse_args()

    with open(os.path.join(args.data_dir, "q4_duplicate_groups.json")) as f:
        groups = json.load(f)
    with open(os.path.join(args.data_dir, "q5_opinion_ids.json")) as f:
        cluster_to_ops = json.load(f)

    # Collect every opinion id we need, fetch embeddings once, in parallel.
    needed = set()
    for side in ("scotus", "circuits"):
        for g in groups.get(side, []):
            for cid in g["cluster_ids"]:
                needed.update(cluster_to_ops.get(str(cid), []))
    print(f"fetching embeddings for {len(needed)} opinions from s3://{BUCKET}/{PREFIX}")

    s3 = make_s3()
    chunk_cache = {}

    def _fetch(op_id):
        return op_id, fetch_opinion_chunks(s3, op_id)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for n, (op_id, chunks) in enumerate(ex.map(_fetch, needed)):
            chunk_cache[op_id] = chunks
            if n and n % 2000 == 0:
                print(f"  {n}/{len(needed)} fetched...")
    missing_ops = sum(1 for v in chunk_cache.values() if not v)
    print(f"opinions with no embedding file: {missing_ops}/{len(needed)}")

    # Per-cluster pooled vectors.
    cluster_vec = {}
    for cid, ops in cluster_to_ops.items():
        cluster_vec[cid] = cluster_vector(ops, chunk_cache)

    results = []
    examples = []
    summary = {"scotus": {}, "circuits": {}}
    for side in ("scotus", "circuits"):
        labels = {"distinct_cases": 0, "true_duplicate": 0,
                  "insufficient_embeddings": 0}
        for g in groups.get(side, []):
            vecs = [(cid, cluster_vec.get(str(cid))) for cid in g["cluster_ids"]]
            vecs = [(cid, v) for cid, v in vecs if v is not None]
            if len(vecs) < 2:
                label, min_cos = "insufficient_embeddings", None
            else:
                pair_cos = [cosine(vecs[i][1], vecs[j][1])
                            for i in range(len(vecs))
                            for j in range(i + 1, len(vecs))]
                min_cos = min(pair_cos)
                label = ("distinct_cases" if min_cos < args.threshold
                         else "true_duplicate")
            labels[label] += 1
            rec = {"side": side, "court": g["court"],
                   "date_filed": g["date_filed"],
                   "docket_number": g["docket_number"],
                   "cluster_ids": g["cluster_ids"],
                   "n_with_embeddings": len(vecs),
                   "min_cosine": min_cos, "label": label}
            results.append(rec)
            if min_cos is not None and len(examples) < 100:
                examples.append(rec)
        summary[side] = labels
        print(f"{side}:", labels)

    with open(os.path.join(args.data_dir, "q5_duplicate_content.json"), "w") as f:
        json.dump({"threshold": args.threshold, "summary": summary,
                   "missing_opinion_embeddings": missing_ops,
                   "groups": results}, f, indent=2)
    with open(os.path.join(args.data_dir, "q5_examples.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["side", "court", "date_filed", "docket_number",
                    "cluster_ids", "n_with_embeddings", "min_cosine", "label"])
        for r in sorted(examples, key=lambda x: x["min_cosine"]):
            w.writerow([r["side"], r["court"], r["date_filed"],
                        r["docket_number"],
                        "|".join(map(str, r["cluster_ids"])),
                        r["n_with_embeddings"],
                        round(r["min_cosine"], 4), r["label"]])
    print("wrote q5_duplicate_content.json + q5_examples.csv")


if __name__ == "__main__":
    main()
