"""Passage-level treatment seeding (step 3 of the triage annotator) with an
Anthropic model on Bedrock batch — the `seed_run.py` the checklist calls for.

The passages come from the LIVE annotator viewer (`/api/passages/{cid}`), so
every passage id (`{cid}:{group}:{i}`), text and text hash is exactly what the
Passages page renders and what `build_windows.py` emits for the encoder. The
model sees the passage prompt (`passage_prompt.py`) as the system prompt and
`<passage>header + text</passage>` as the user turn, and answers one JSON
object per passage. `apply` writes that object into the `seed` slot of
`data/annotator/data/passage_reviews/{cid}.json`; human reviews are never
touched.

    bash seed_passages.sh prepare --split dev            # viewer -> windows.jsonl + batch.jsonl
    bash seed_passages.sh submit  --name opus_dev        # upload + Bedrock batch job
    bash seed_passages.sh status | wait --name opus_dev
    bash seed_passages.sh fetch   --name opus_dev        # -> outputs/{name}/seeds.jsonl
    bash seed_passages.sh apply   --name opus_dev        # -> passage_reviews/{cid}.json (seed slot)
    bash seed_passages.sh score   --name opus_dev        # pair-level vs benchmark gold (dev/test)

Layout under data/seed_passages/<name>/ (default name = opus_<split>):
    windows.jsonl   one row per passage: rid (batch recordId), window_id, cluster_id,
                    group, header, text, text_hash, section, n_mentions, cited_cluster_id
    batch.jsonl     Bedrock records (recordId = rid: short, alphanumeric)
    raw/            downloaded .jsonl.out files
    seeds.jsonl     parsed model output per passage (+ usage, rationale)
    errors.json     records that failed or did not parse
Jobs bookkeeping: data/seed_passages/bedrock_jobs.json.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.request

import boto3
from json_repair import repair_json

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "..", "..", "citator-pipeline"))
sys.path.insert(0, ROOT)
from utils.batch_utils import (  # noqa: E402
    S3_BUCKET, BATCH_ROLE_ARN, generate_run_id, prompt_sha, s3_run_prefix, s3_uri,
    upload_file, list_keys, download_to_file, submit_batch_job, check_job_status,
    write_manifest, update_manifest,
)
import utils.batch_utils as _bu  # noqa: E402
import utils.bedrock_converse_utils as _bc  # noqa: E402
from passage_prompt import PASSAGE_SCHEMA, VERSION as PROMPT_VERSION, build_passage_prompt  # noqa: E402

if os.environ.get("AWS_ACCESS_KEY_ID"):
    # The pipeline hard-codes boto3.Session(profile_name="dev-env"); when the SSO
    # token has expired but the CLI still holds cached role credentials
    # (`eval "$(aws configure export-credentials --profile dev-env --format env)"`),
    # let those env credentials drive every client instead.
    _bc.session = _bu.session = boto3.Session()
from build_windows import SEVERITY_RANK, severity_of  # noqa: E402
from tagged_text import from_revised_html  # noqa: E402

ANNOT = os.environ.get("CITSEED_ANNOT") or os.path.join(ROOT, "data", "annotator")
VIEWER = os.environ.get("CITSEED_VIEWER", "http://127.0.0.1:8125")
BASE = os.path.join(ROOT, "data", "seed_passages")
JOBS = os.path.join(BASE, "bedrock_jobs.json")
SPLITS = os.path.join(ROOT, "inputs", "splits.csv")
BENCH = "/Users/rachel/Desktop/flp/citator-benchmark/data"
DEFAULT_MODEL = os.environ.get("CITSEED_MODEL_ID", "us.anthropic.claude-opus-5")
MIN_RECORDS = 100
ANTHROPIC_VERSION = "bedrock-2023-05-31"
ADAPTIVE_FAMILIES = ("opus-5", "opus-4-7", "opus-4-8", "sonnet-5", "fable")
NO_SAMPLING_FAMILIES = ADAPTIVE_FAMILIES  # temperature etc. rejected (400)
TERMINAL = ("Completed", "PartiallyCompleted", "Failed", "Stopped", "Expired")
LABELS = ("treatment", "cited_by", "posture", "not_about_target")
WINDOW_CHARS, CONTEXT = 4000, 1  # must match the viewer's _passage_model / build_windows defaults


# ------------------------------------------------------------------ helpers
def load_json(p, default):
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
    os.replace(tmp, p)


def jobs():
    return load_json(JOBS, {})


def split_ids(split):
    return [r["cluster_id"] for r in csv.DictReader(open(SPLITS)) if r["split"] == split]


def run_dir(name):
    return os.path.join(BASE, name)


def read_windows(name):
    with open(os.path.join(run_dir(name), "windows.jsonl"), encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def text_hash(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]  # same as the viewer


def api(path):
    with urllib.request.urlopen(f"{VIEWER}{path}", timeout=120) as r:
        return json.load(r)


def group_cited_ids(cid):
    """{group id: cited_cluster_id} from the annotator's revised html (gold
    coreference); the viewer builds its passages from the same grouping."""
    p = os.path.join(ANNOT, "data", "revised_html", f"{cid}.html")
    if not os.path.exists(p):
        return {}
    _text, inv = from_revised_html(open(p, encoding="utf-8").read())
    return {str(e["id"]): e.get("cited_cluster_id") for e in inv}


def build_record(rid, system_prompt, header, text, model, max_tokens, thinking):
    body = {
        "anthropic_version": ANTHROPIC_VERSION,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": [{"type": "text", "text": f"<passage>\n{header}\n\n{text}\n</passage>\n\nJSON:"}]}],
    }
    if thinking == "adaptive" or (thinking == "auto" and any(f in model for f in ADAPTIVE_FAMILIES)):
        body["thinking"] = {"type": "adaptive"}
    elif thinking not in ("none", "auto"):
        body["thinking"] = {"type": "enabled", "budget_tokens": int(thinking)}
    elif thinking == "auto":  # older family: budgeted thinking
        body["thinking"] = {"type": "enabled", "budget_tokens": 4000}
    if "thinking" not in body and not any(f in model for f in NO_SAMPLING_FAMILIES):
        body["temperature"] = 0
    return {"recordId": rid, "modelInput": body}


# ------------------------------------------------------------------ prepare
def cmd_prepare(a):
    name = a.name or f"opus_{a.split}"
    out = run_dir(name)
    os.makedirs(out, exist_ok=True)
    ids = a.ids or split_ids(a.split)
    system_prompt = build_passage_prompt()
    model = a.model or DEFAULT_MODEL
    rows, n_groups, chars = [], 0, 0
    for cid in ids:
        cited = group_cited_ids(cid)
        groups = api(f"/api/passages/{cid}")["groups"]
        for g in groups:
            gid = g["group"]
            r = api(f"/api/passages/{cid}?num={gid}")
            n_groups += 1
            for i, p in enumerate(r["passages"]):
                rid = f"p{len(rows):05d}"
                rows.append({"rid": rid, "window_id": f"{cid}:{gid}:{i}", "cluster_id": cid, "group": gid,
                             "cited_cluster_id": cited.get(gid), "header": r["header"], "text": p["text"],
                             "text_hash": text_hash(p["text"]), "section": p.get("section"),
                             "n_mentions": p.get("n_mentions")})
                chars += len(system_prompt) + len(r["header"]) + len(p["text"])
        print(f"{cid}: {len(groups)} groups -> {sum(1 for x in rows if x['cluster_id'] == cid)} passages", file=sys.stderr)
    with open(os.path.join(out, "windows.jsonl"), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(os.path.join(out, "batch.jsonl"), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(build_record(r["rid"], system_prompt, r["header"], r["text"], model, a.max_tokens, a.thinking),
                               ensure_ascii=False) + "\n")
    js = jobs()
    js[name] = {"name": name, "split": a.split, "ids": ids, "n_records": len(rows), "model": model,
                "max_tokens": a.max_tokens, "thinking": a.thinking, "prompt_version": PROMPT_VERSION,
                "prompt_sha": prompt_sha(system_prompt), "viewer": VIEWER,
                "prepared": dt.datetime.now().isoformat(timespec="seconds")}
    save_json(JOBS, js)
    with open(os.path.join(out, "prompt.md"), "w", encoding="utf-8") as f:
        f.write(system_prompt)
    est_in = chars / 3.6 * 1.3  # newer tokenizer
    print(f"{name}: {len(ids)} clusters, {n_groups} groups, {len(rows)} passages -> {out}\n"
          f"  ~{est_in / 1e6:.1f}M input tokens (~${est_in / 1e6 * 2.5:.0f} at $2.50/M batch) + output; "
          f"model {model}, max_tokens {a.max_tokens}, thinking {a.thinking}")
    if len(rows) < MIN_RECORDS:
        print(f"WARNING: {len(rows)} records < Bedrock minimum {MIN_RECORDS}")


# ------------------------------------------------------------------ submit / status / fetch
def cmd_submit(a):
    js = jobs()
    meta = js.get(a.name) or sys.exit(f"no prepared run named {a.name}")
    model = a.model or meta["model"]
    if not (S3_BUCKET and BATCH_ROLE_ARN):
        sys.exit("CITATOR_S3_BUCKET and CITATOR_BATCH_ROLE_ARN must be exported")
    if meta["n_records"] < MIN_RECORDS and not a.force:
        sys.exit(f"{meta['n_records']} records < {MIN_RECORDS}")
    run_id = meta.get("run_id") or generate_run_id()
    key_in = f"{s3_run_prefix(run_id)}/passages/input.jsonl"
    raw = f"{s3_run_prefix(run_id)}/passages/raw/"
    upload_file(os.path.join(run_dir(a.name), "batch.jsonl"), key_in)
    job_name = f"citpass-{a.name}-{int(time.time())}".replace("_", "-")[:63]
    job_arn = submit_batch_job(job_name, s3_uri(key_in), s3_uri(raw), model_id=model)
    write_manifest(run_id, {"task": "passage_seed", "batch": a.name, "model_id": model, "prompt": meta["prompt_version"],
                            "prompt_sha": meta["prompt_sha"], "n_records": meta["n_records"], "job_arn": job_arn,
                            "job_name": job_name, "thinking": meta["thinking"], "max_tokens": meta["max_tokens"]})
    meta.update({"run_id": run_id, "job_arn": job_arn, "job_name": job_name, "model": model, "s3_input": key_in,
                 "s3_raw": raw, "submitted": dt.datetime.now().isoformat(timespec="seconds"), "status": "Submitted"})
    save_json(JOBS, js)
    print(f"submitted {job_name}\n  run_id {run_id}\n  arn {job_arn}\n  model {model}\n  output {s3_uri(raw)}")


def _refresh(meta):
    st = check_job_status(meta["job_arn"])
    meta["status"], meta["message"] = st["status"], st.get("message", "")
    meta["checked"] = dt.datetime.now().isoformat(timespec="seconds")
    return st


def cmd_status(a):
    js = jobs()
    for n in ([a.name] if a.name else list(js)):
        meta = js[n]
        if not meta.get("job_arn"):
            print(f"{n:16} prepared only ({meta['n_records']} records, {meta['model']})")
            continue
        st = _refresh(meta)
        print(f"{n:16} {st['status']:18} {meta['model']}  submitted {meta.get('submitted', '')}  {st.get('message', '')}")
    save_json(JOBS, js)


def cmd_wait(a):
    js = jobs()
    meta = js[a.name]
    while True:
        st = _refresh(meta)
        save_json(JOBS, js)
        print(dt.datetime.now().strftime("%H:%M:%S"), st["status"], st.get("message", ""))
        if st["status"] in TERMINAL:
            break
        time.sleep(a.every)


_FENCE = re.compile(r"^```(?:json)?|```$", re.M)


def parse_output(text):
    """(seed dict, rationale) from the model text: outermost {...} parsed
    (repaired if needed); any prose before it is the rationale."""
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j < 0:
        raise ValueError("no JSON object")
    rationale = _FENCE.sub("", text[:i]).strip()
    body = _FENCE.sub("", text[i:j + 1]).strip()
    try:
        obj = json.loads(body)
    except json.JSONDecodeError:
        obj = json.loads(repair_json(body))
    if not isinstance(obj, dict):
        raise ValueError("JSON is not an object")
    label = obj.get("label")
    if label not in LABELS:
        raise ValueError(f"bad label {label!r}")
    seed = {k: obj.get(k) for k in ("label", "treatment", "caseHistory", "actingCase", "evidence", "confidence")}
    if label != "treatment":
        seed["treatment"] = "Cited by"
    seed["rationale"] = rationale[:300]
    return seed, rationale


def cmd_fetch(a):
    js = jobs()
    meta = js.get(a.name) or sys.exit(f"unknown run {a.name}")
    st = _refresh(meta)
    save_json(JOBS, js)
    if st["status"] not in ("Completed", "PartiallyCompleted") and not a.force:
        sys.exit(f"job status is {st['status']} ({st.get('message', '')}); --force to fetch whatever exists")
    out = run_dir(a.name)
    raw_dir = os.path.join(out, "raw")
    os.makedirs(raw_dir, exist_ok=True)
    by_rid = {r["rid"]: r for r in read_windows(a.name)}
    seeds, errors, tin, tout = [], {}, 0, 0
    for k in list_keys(meta["s3_raw"]):
        if not k.endswith(".jsonl.out"):
            continue
        local = os.path.join(raw_dir, os.path.basename(k))
        download_to_file(k, local)
        for line in open(local, encoding="utf-8"):
            if not line.strip():
                continue
            rec = json.loads(line)
            rid = rec.get("recordId")
            w = by_rid.get(rid)
            if w is None:
                errors[rid] = "unknown recordId"
                continue
            if "error" in rec or "modelOutput" not in rec:
                errors[w["window_id"]] = rec.get("error", "no modelOutput")
                continue
            mo = rec["modelOutput"]
            text = "\n".join(b.get("text", "") for b in mo.get("content", []) if b.get("type") == "text")
            u = mo.get("usage", {})
            tin += u.get("input_tokens") or 0
            tout += u.get("output_tokens") or 0
            try:
                seed, _ = parse_output(text)
            except Exception as e:
                errors[w["window_id"]] = f"unparseable ({mo.get('stop_reason')}): {e} :: {text[-200:]}"
                continue
            seeds.append({"window_id": w["window_id"], "cluster_id": w["cluster_id"], "group": w["group"],
                          "cited_cluster_id": w["cited_cluster_id"], "text_hash": w["text_hash"], "seed": seed,
                          "usage": {"in": u.get("input_tokens"), "out": u.get("output_tokens")},
                          "stop_reason": mo.get("stop_reason")})
    with open(os.path.join(out, "seeds.jsonl"), "w", encoding="utf-8") as f:
        for s in seeds:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    if errors:
        save_json(os.path.join(out, "errors.json"), errors)
    meta.update({"fetched": dt.datetime.now().isoformat(timespec="seconds"), "n_ok": len(seeds), "n_err": len(errors),
                 "input_tokens": tin, "output_tokens": tout})
    save_json(JOBS, js)
    update_manifest(meta["run_id"], {"fetched": meta["fetched"], "n_ok": len(seeds), "n_err": len(errors),
                                     "input_tokens": tin, "output_tokens": tout})
    labels = {}
    for s in seeds:
        labels[s["seed"]["label"]] = labels.get(s["seed"]["label"], 0) + 1
    print(f"{len(seeds)} ok, {len(errors)} errors -> {out}/seeds.jsonl  ({tin:,} in / {tout:,} out tokens; "
          f"~${tin / 1e6 * 2.5 + tout / 1e6 * 12.5:.0f} at $2.50/$12.50)\n  labels: {labels}")
    if errors:
        print(f"  errors in {out}/errors.json: " + " ".join(list(errors)[:10]))


# ------------------------------------------------------------------ apply
def cmd_apply(a):
    js = jobs()
    meta = js.get(a.name) or sys.exit(f"unknown run {a.name}")
    out = run_dir(a.name)
    seeds = [json.loads(l) for l in open(os.path.join(out, "seeds.jsonl"), encoding="utf-8") if l.strip()]
    rev_dir = os.path.join(ANNOT, "data", "passage_reviews")
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    bdir = os.path.join(BASE, "backups", f"passage_reviews_{stamp}")
    by_cid = {}
    for s in seeds:
        by_cid.setdefault(s["cluster_id"], []).append(s)
    n_new = n_replaced = n_kept_review = 0
    for cid, ss in by_cid.items():
        path = os.path.join(rev_dir, f"{cid}.json")
        data = load_json(path, {})
        if data:
            os.makedirs(bdir, exist_ok=True)
            shutil.copy2(path, os.path.join(bdir, f"{cid}.json"))
        for s in ss:
            rec = data.setdefault(s["window_id"], {"seed": None, "review": None, "text_hash": s["text_hash"]})
            if rec.get("seed") and not a.overwrite:
                continue
            n_replaced += bool(rec.get("seed"))
            n_new += not rec.get("seed")
            n_kept_review += bool(rec.get("review"))
            rec["seed"] = {**s["seed"], "model": meta["model"], "prompt_version": meta["prompt_version"],
                           "run": a.name, "at": stamp}
            rec["text_hash"] = s["text_hash"]  # the text this seed was produced on
        if a.write:
            os.makedirs(rev_dir, exist_ok=True)
            save_json(path, data)
    meta["applied"] = stamp if a.write else meta.get("applied")
    save_json(JOBS, js)
    print(f"{'WROTE' if a.write else 'DRY RUN'}: {len(by_cid)} clusters, {n_new} seeds new, {n_replaced} replaced, "
          f"{n_kept_review} passages already had a human review (kept)"
          + (f"; backups in {bdir}" if a.write and os.path.exists(bdir) else ""))


# ------------------------------------------------------------------ score
def _gold(split):
    ids = set(split_ids(split))
    finals = {}
    for r in csv.DictReader(open(os.path.join(BENCH, "treatments_most_negative.csv"))):
        if r["citing_case_cluster_id"] in ids and r["final_treatment"]:
            finals[(r["citing_case_cluster_id"], r["cited_case_cluster_id"])] = r["final_treatment"]
    return finals


def cmd_score(a):
    """Pair level (citing cluster, cited cluster) against the benchmark
    finals: a pair is predicted POSITIVE when any of its passages is seeded
    `treatment` (Citing Reference only unless --relref). Direct-history gold
    pairs are reported separately (the gate excludes them)."""
    meta = jobs().get(a.name) or sys.exit(f"unknown run {a.name}")
    split = a.split or meta["split"]
    gold = _gold(split)
    seeds = [json.loads(l) for l in open(os.path.join(run_dir(a.name), "seeds.jsonl"), encoding="utf-8") if l.strip()]
    pred = {}  # pair -> most severe seeded treatment
    for s in seeds:
        if s["cited_cluster_id"] is None:
            continue
        k = (str(s["cluster_id"]), str(s["cited_cluster_id"]))
        sd = s["seed"]
        pred.setdefault(k, [])
        if sd["label"] == "treatment" and (a.relref or sd.get("caseHistory") != "Related Reference") \
                and not (sd.get("treatment") or "").endswith(" as recognized by"):
            pred[k].append(sd.get("treatment") or "Other")
    relref = lambda t: t.endswith(" as recognized by")  # noqa: E731
    dh = {"Affirmed by", "Reversed by", "Reversed and remanded by", "Vacated by", "Vacated and remanded by", "Modified by",
          "Remanded by", "Dismissed by", "Cert. granted by", "Cert. denied by", "Affirmed in part; Reversed in part by",
          "Affirmed in part; Vacated in part by", "Reversed in part; Vacated in part by"}
    tp = fp = fn = tn = exact = 0
    missed, wrong, dh_pairs, uncovered = [], [], 0, 0
    for k, t in gold.items():
        if relref(t):
            continue  # dropped from the gate like build_windows does
        if t in dh:
            dh_pairs += 1
            continue
        if k not in pred:
            uncovered += 1  # gold pair whose cited case has no seeded passage (grouping / cluster-id gap)
            continue
        gpos = t != "Cited by"
        ppos = bool(pred[k])
        if gpos and ppos:
            tp += 1
            best = max(pred[k], key=lambda x: SEVERITY_RANK.get(severity_of(x), 0))
            exact += best == t
            if best != t:
                wrong.append((k, t, best))
        elif gpos:
            fn += 1
            missed.append((k, t))
        elif ppos:
            fp += 1
        else:
            tn += 1
    p = tp / (tp + fp) if tp + fp else 0
    r = tp / (tp + fn) if tp + fn else 0
    f1 = 2 * p * r / (p + r) if p + r else 0
    print(f"{a.name} vs {split} gold (Citing Reference pairs; DH {dh_pairs} and 'as recognized by' excluded; "
          f"{uncovered} gold pairs uncovered by seeds)\n"
          f"  positives gold {tp + fn} / predicted {tp + fp}: P {p:.3f} R {r:.3f} F1 {f1:.3f}  (tn {tn})\n"
          f"  exact treatment on hits: {exact}/{tp}")
    if a.verbose:
        for k, t in missed:
            print("  MISSED", k, t)
        for k, t, b in wrong:
            print("  WRONG ", k, "gold", t, "pred", b)
    labels = {}
    for s in seeds:
        labels[s["seed"]["label"]] = labels.get(s["seed"]["label"], 0) + 1
    flagged = sum(v for k, v in labels.items() if k == "treatment")
    print(f"  passages: {labels}  flagged rate {flagged / max(1, len(seeds)):.1%}")


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", help="pull passages from the viewer, write windows.jsonl + batch.jsonl")
    p.add_argument("--split", default="dev")
    p.add_argument("--ids", nargs="*")
    p.add_argument("--name")
    p.add_argument("--model", default=None)
    p.add_argument("--max-tokens", type=int, default=6000)
    p.add_argument("--thinking", default="auto", help="auto | adaptive | none | <budget>")
    p = sub.add_parser("submit")
    p.add_argument("--name", required=True)
    p.add_argument("--model")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("status")
    p.add_argument("--name")
    p = sub.add_parser("wait")
    p.add_argument("--name", required=True)
    p.add_argument("--every", type=int, default=300)
    p = sub.add_parser("fetch")
    p.add_argument("--name", required=True)
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("apply", help="write seeds into passage_reviews (dry run without --write)")
    p.add_argument("--name", required=True)
    p.add_argument("--write", action="store_true")
    p.add_argument("--overwrite", action="store_true", help="replace existing seeds (reviews are always kept)")
    p = sub.add_parser("score")
    p.add_argument("--name", required=True)
    p.add_argument("--split")
    p.add_argument("--relref", action="store_true", help="count Related Reference seeds as positives too")
    p.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    {"prepare": cmd_prepare, "submit": cmd_submit, "status": cmd_status, "wait": cmd_wait,
     "fetch": cmd_fetch, "apply": cmd_apply, "score": cmd_score}[a.cmd](a)


if __name__ == "__main__":
    main()
