"""Bedrock batch-inference runner for the citation seeding pass.

Thin CLI over the pipeline's existing batch plumbing
(`citator-pipeline/utils/batch_utils.py`: dev-env session, S3 bucket + IAM role
from `CITATOR_S3_BUCKET` / `CITATOR_BATCH_ROLE_ARN`, `submit_batch_job`,
`check_job_status`, S3 helpers, per-run manifest). It only adds what is
specific to this task: building one record per opinion from
`data/citation_seed/inputs/{cid}.txt` with the fixer prompt as the system
prompt, and writing results back in the layout `citation_seed.py score|apply`
already read:

    <out-dir>/{cid}.response.md   full model text (scan + name_sweep + edits)
    <out-dir>/{cid}.edits.json    the bare JSON edit object

S3 layout follows the pipeline convention (one run_id per batch name):

    s3://{CITATOR_S3_BUCKET}/Citator/runs/{run_id}/
        manifest.json
        citseed/input.jsonl
        citseed/raw/{jobId}/input.jsonl.out

Run through the wrapper (checks the SSO session + env vars, supplies deps):

    bash bedrock_batch.sh models                       # Anthropic model ids in the region
    bash bedrock_batch.sh export --name train_a --ids $(cat data/citation_seed/batch_x_ids.txt)
    bash bedrock_batch.sh submit --name train_a        # upload + create job
    bash bedrock_batch.sh status                       # all tracked jobs
    bash bedrock_batch.sh fetch  --name train_a        # download + write response.md / edits.json
    python3 lib/citation_seed.py score --ids ... --out-dir data/citation_seed/train_a   # or apply --write

Bedrock batch needs >= 100 records per job; `export` warns below that.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time

from json_repair import repair_json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "..", "..", "citator-pipeline"))
from utils.batch_utils import (  # noqa: E402
    S3_BUCKET, BATCH_ROLE_ARN, generate_run_id, prompt_sha, s3_run_prefix, s3_uri,
    upload_file, list_keys, download_to_file, submit_batch_job, check_job_status,
    write_manifest, update_manifest,
)
from utils.bedrock_converse_utils import session, AWS_REGION  # noqa: E402

SEED = os.environ.get("CITSEED_OUT") or os.path.join(ROOT, "data", "citation_seed")  # same override as citation_seed.py
INPUTS = os.path.join(SEED, "inputs")
BATCHES = os.path.join(SEED, "batches")
JOBS = os.path.join(SEED, "bedrock_jobs.json")
DEFAULT_PROMPT = os.path.join(ROOT, "prompts", "triage_citation_fixer_v5.md")
DEFAULT_MODEL = os.environ.get("CITSEED_MODEL_ID", "")  # e.g. us.anthropic.claude-opus-... (see `models`)

MIN_RECORDS = 100  # Bedrock batch-inference minimum per job
ANTHROPIC_VERSION = "bedrock-2023-05-31"
# model families where `budget_tokens` is rejected and thinking must be adaptive
ADAPTIVE_FAMILIES = ("opus-5", "opus-4-7", "opus-4-8", "sonnet-5", "fable")
TERMINAL = ("Completed", "PartiallyCompleted", "Failed", "Stopped", "Expired")
# Hard output ceilings per model (max_tokens is a required field in the Anthropic
# Messages body, so "no cap" = the model maximum). Probed 2026-09-10 via
# InvokeModel validation errors; falls back to 64000 for unknown models.
# Kimi: max_tokens shares the 262,144-token context with the input (Bedrock rejects
# input + max_tokens > 262144), so leave ~130K for the prompt.
MODEL_MAX_TOKENS = {"opus-5": 128000, "sonnet-4-6": 128000, "sonnet-5": 128000, "kimi-k2.5": 128000, "haiku-4-5": 64000}


def model_max_tokens(model_id):
    for k, v in MODEL_MAX_TOKENS.items():
        if k in (model_id or ""):
            return v
    return 64000


# ------------------------------------------------------------------ helpers
def load_json(p, default):
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1)


def jobs():
    return load_json(JOBS, {})


def s3_input_key(run_id):
    return f"{s3_run_prefix(run_id)}/citseed/input.jsonl"


def s3_raw_prefix(run_id):
    return f"{s3_run_prefix(run_id)}/citseed/raw/"


def thinking_block(model_id, mode):
    """mode: 'auto' | 'adaptive' | 'none' | '<int budget>'."""
    if mode == "none":
        return None
    if mode == "adaptive" or (mode == "auto" and any(f in model_id for f in ADAPTIVE_FAMILIES)):
        return {"type": "adaptive"}
    budget = 8000 if mode == "auto" else int(mode)
    return {"type": "enabled", "budget_tokens": budget}


def build_record(cid, prompt, text, max_tokens, thinking, fmt="anthropic"):
    """Bedrock batch parses modelInput as the model's native schema.
    fmt="anthropic": Messages body (like build_sonnet_record in batch_utils).
    fmt="chat": OpenAI-style chat body for Kimi K2.5 and other non-Anthropic
    models — no system role (MODEL_CAPS says Kimi lacks system_prompt), so the
    prompt is prepended to the user content, like build_classification_record."""
    if fmt == "chat":
        body = {"messages": [{"role": "user", "content": f"{prompt}\n\n{text}"}],
                "max_tokens": max_tokens, "temperature": 0}
        return {"recordId": str(cid), "modelInput": body}
    body = {
        "anthropic_version": ANTHROPIC_VERSION,
        "max_tokens": max_tokens,
        "system": prompt,
        "messages": [{"role": "user", "content": [{"type": "text", "text": text}]}],
    }
    if thinking:
        body["thinking"] = thinking  # temperature must be left unset with thinking
    else:
        body["temperature"] = 0
    return {"recordId": str(cid), "modelInput": body}


def record_format(model_id):
    return "anthropic" if "anthropic" in (model_id or "") else "chat"


def extract_edits_json(text):
    """Same extraction as citation_seed.parse_edits, on a string."""
    m = re.search(r"<edits>\s*(?:```(?:json)?)?\s*(\{.*\})\s*(?:```)?\s*</edits>", text, re.S)
    txt = m.group(1) if m else text
    txt = re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip()
    if not m:  # no <edits> block: take the outermost {...} in the text
        i, j = txt.find("{"), txt.rfind("}")
        if i < 0 or j < 0:
            raise ValueError("no JSON object in response")
        txt = txt[i:j + 1]
    try:
        e = json.loads(txt)
    except json.JSONDecodeError:
        # malformed JSON (a missing comma, a stray quote): repair like the
        # pipeline's parse_utils does, then insist on a dict
        e = json.loads(repair_json(txt))
        if not isinstance(e, dict):
            raise
    for k in ("remove", "regroup", "merge", "add"):
        e.setdefault(k, [])
    for k in ("names", "roles"):
        e.setdefault(k, {})
    e.setdefault("notes", "")
    return e


# ------------------------------------------------------------------ models
def cmd_models(a):
    br = session.client("bedrock", region_name=AWS_REGION)
    rows = [m for m in br.list_foundation_models(byProvider="anthropic")["modelSummaries"]
            if not a.filter or a.filter.lower() in m["modelId"].lower()]
    for m in sorted(rows, key=lambda m: m["modelId"]):
        print(f"{m['modelId']:60} {m.get('modelName', ''):32} {','.join(m.get('inferenceTypesSupported', []))}")
    print("\nBatch jobs take the cross-region inference-profile form used elsewhere in the pipeline "
          "(us.<modelId>, e.g. us.anthropic.claude-sonnet-4-6). Set CITSEED_MODEL_ID or pass --model.")


# ------------------------------------------------------------------ export
def cmd_export(a):
    prompt = open(a.prompt, encoding="utf-8").read()
    os.makedirs(BATCHES, exist_ok=True)
    model = a.model or DEFAULT_MODEL
    fmt = a.format or record_format(model)
    thinking = thinking_block(model, a.thinking) if fmt == "anthropic" else None
    max_tokens = a.max_tokens or model_max_tokens(model)
    missing = [c for c in a.ids if not os.path.exists(os.path.join(INPUTS, f"{c}.txt"))]
    if missing:
        sys.exit(f"{len(missing)} ids have no prepared input (run `python3 lib/citation_seed.py prepare --ids ...`): "
                 + " ".join(missing[:20]))
    path = os.path.join(BATCHES, f"{a.name}.jsonl")
    n_chars = 0
    with open(path, "w", encoding="utf-8") as f:
        for cid in a.ids:
            text = open(os.path.join(INPUTS, f"{cid}.txt"), encoding="utf-8").read()
            f.write(json.dumps(build_record(cid, prompt, text, max_tokens, thinking, fmt), ensure_ascii=False) + "\n")
            n_chars += len(prompt) + len(text)
    js = jobs()
    if a.name in js:  # re-export: forget the previous submission so submit gets a fresh run_id / S3 prefix
        js[a.name] = {k: v for k, v in js[a.name].items() if k in ("name", "out_dir")}
    js.setdefault(a.name, {}).update({
        "name": a.name, "ids": list(a.ids), "prompt": os.path.relpath(a.prompt, ROOT), "prompt_sha": prompt_sha(prompt),
        "thinking": thinking, "format": fmt, "model": model or None, "max_tokens": max_tokens, "jsonl": os.path.relpath(path, ROOT),
        "exported": dt.datetime.now().isoformat(timespec="seconds"),
        "out_dir": os.path.relpath(a.out_dir or os.path.join(SEED, a.name), ROOT)})
    save_json(JOBS, js)
    print(f"{len(a.ids)} records -> {path} (~{n_chars/3.6/1e6:.2f}M input tokens); "
          f"prompt={js[a.name]['prompt']}; format={fmt}; thinking={thinking}; max_tokens={max_tokens}")
    if len(a.ids) < MIN_RECORDS:
        print(f"WARNING: Bedrock batch jobs need at least {MIN_RECORDS} records; this file has {len(a.ids)}. "
              f"Add more ids (e.g. a train batch) to the same export.")


# ------------------------------------------------------------------ submit
def cmd_submit(a):
    js = jobs()
    if a.name not in js or "jsonl" not in js[a.name]:
        sys.exit(f"no export named {a.name}; run `export --name {a.name} --ids ...` first")
    meta = js[a.name]
    model = a.model or meta.get("model") or DEFAULT_MODEL
    if not model:
        sys.exit("no model id: pass --model or set CITSEED_MODEL_ID (see `models`)")
    if not (S3_BUCKET and BATCH_ROLE_ARN):
        sys.exit("CITATOR_S3_BUCKET and CITATOR_BATCH_ROLE_ARN must be exported (same as the citator-pipeline batch runs)")
    if len(meta["ids"]) < MIN_RECORDS and not a.force:
        sys.exit(f"{len(meta['ids'])} records < Bedrock minimum {MIN_RECORDS}; re-export with more ids (or --force to try anyway)")
    run_id = meta.get("run_id") or generate_run_id()
    upload_file(os.path.join(ROOT, meta["jsonl"]), s3_input_key(run_id))
    job_name = f"citseed-{a.name}-{int(time.time())}".replace("_", "-")[:63]
    job_arn = submit_batch_job(job_name, s3_uri(s3_input_key(run_id)), s3_uri(s3_raw_prefix(run_id)), model_id=model)
    write_manifest(run_id, {"task": "citation_seed", "batch": a.name, "model_id": model, "prompt": meta["prompt"],
                            "prompt_sha": meta["prompt_sha"], "n_records": len(meta["ids"]), "job_arn": job_arn,
                            "job_name": job_name, "thinking": meta["thinking"], "max_tokens": meta["max_tokens"]})
    meta.update({"run_id": run_id, "job_arn": job_arn, "job_name": job_name, "model": model, "bucket": S3_BUCKET,
                 "submitted": dt.datetime.now().isoformat(timespec="seconds"), "status": "Submitted"})
    save_json(JOBS, js)
    print(f"submitted {job_name}\n  run_id: {run_id}\n  arn: {job_arn}\n  model: {model}\n"
          f"  input: {s3_uri(s3_input_key(run_id))}\n  output: {s3_uri(s3_raw_prefix(run_id))}")


# ------------------------------------------------------------------ status
def _refresh(meta):
    st = check_job_status(meta["job_arn"])
    meta["status"], meta["message"] = st["status"], st.get("message", "")
    meta["checked"] = dt.datetime.now().isoformat(timespec="seconds")
    return st


def cmd_status(a):
    js = jobs()
    names = [a.name] if a.name else list(js)
    if not names:
        print("no batches tracked in", JOBS)
        return
    for n in names:
        meta = js[n]
        if not meta.get("job_arn"):
            print(f"{n:24} exported only ({len(meta.get('ids', []))} records, {meta.get('prompt')})")
            continue
        st = _refresh(meta)
        print(f"{n:24} {st['status']:18} {meta.get('model', '')}  submitted {meta.get('submitted', '')}  {st.get('message', '')}")
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


# ------------------------------------------------------------------ fetch
def cmd_fetch(a):
    js = jobs()
    meta = js.get(a.name) or sys.exit(f"unknown batch {a.name}")
    if not meta.get("job_arn"):
        sys.exit(f"{a.name} was never submitted")
    st = _refresh(meta)
    save_json(JOBS, js)
    if st["status"] not in ("Completed", "PartiallyCompleted") and not a.force:
        sys.exit(f"job status is {st['status']} ({st.get('message', '')}); use --force to fetch whatever exists")
    out_dir = os.path.join(ROOT, a.out_dir or meta["out_dir"])
    raw_dir = os.path.join(BATCHES, f"{a.name}.out")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(raw_dir, exist_ok=True)
    keys = list_keys(s3_raw_prefix(meta["run_id"]))
    n_ok, n_err, errors, usage = 0, 0, {}, meta.setdefault("usage", {})
    for k in keys:
        if k.endswith("/") or not (k.endswith(".jsonl.out") or k.endswith(".json.out")):
            continue  # folder placeholders and anything that is not a batch output
        local = os.path.join(raw_dir, os.path.basename(k))
        download_to_file(k, local)
        if not k.endswith(".jsonl.out"):
            continue
        for line in open(local, encoding="utf-8"):
            if not line.strip():
                continue
            rec = json.loads(line)
            cid = rec.get("recordId")
            if "error" in rec or "modelOutput" not in rec:
                n_err += 1
                errors[cid] = rec.get("error", "no modelOutput")
                continue
            mo = rec["modelOutput"]
            if "choices" in mo:  # OpenAI-style chat output (Kimi)
                text = "\n".join((c.get("message") or {}).get("content") or "" for c in mo["choices"])
            else:
                text = "\n".join(b.get("text", "") for b in mo.get("content", []) if b.get("type") == "text")
            with open(os.path.join(out_dir, f"{cid}.response.md"), "w", encoding="utf-8") as f:
                f.write(text)
            try:
                edits = extract_edits_json(text)
            except Exception as e:  # keep response.md for inspection; count as error
                n_err += 1
                errors[cid] = f"unparseable edits: {e}"
                continue
            with open(os.path.join(out_dir, f"{cid}.edits.json"), "w", encoding="utf-8") as f:
                json.dump(edits, f, indent=1, ensure_ascii=False)
            n_ok += 1
            u = mo.get("usage", {})
            usage[cid] = {"in": u.get("input_tokens", u.get("prompt_tokens")), "out": u.get("output_tokens", u.get("completion_tokens"))}
    meta.update({"fetched": dt.datetime.now().isoformat(timespec="seconds"), "n_ok": n_ok, "n_err": n_err})
    if errors:
        save_json(os.path.join(BATCHES, f"{a.name}.errors.json"), errors)
    save_json(JOBS, js)
    tin = sum((v.get("in") or 0) for v in usage.values())
    tout = sum((v.get("out") or 0) for v in usage.values())
    update_manifest(meta["run_id"], {"fetched": meta["fetched"], "n_ok": n_ok, "n_err": n_err,
                                     "input_tokens": tin, "output_tokens": tout})
    print(f"{n_ok} ok, {n_err} errors -> {out_dir}  (usage: {tin:,} in / {tout:,} out tokens)")
    if errors:
        print(f"errors in {os.path.join(BATCHES, a.name + '.errors.json')}: " + " ".join(list(errors)[:20]))
    missing = [c for c in meta["ids"] if not os.path.exists(os.path.join(out_dir, f"{c}.edits.json"))]
    if missing:
        print(f"{len(missing)} ids without edits.json (re-export these as a new batch): " + " ".join(missing[:30]))
    rel = os.path.relpath(out_dir, ROOT)
    print(f"next: python3 lib/citation_seed.py score --ids <ids> --out-dir {rel}   # dev/test\n"
          f"      python3 lib/citation_seed.py apply --write --ids <ids> --out-dir {rel}   # train")


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("models", help="list Anthropic model ids available in the region")
    p.add_argument("--filter", default="opus")
    p = sub.add_parser("export", help="build the batch JSONL from prepared inputs")
    p.add_argument("--name", required=True, help="batch name (files, S3 manifest, job name)")
    p.add_argument("--ids", nargs="+", required=True)
    p.add_argument("--prompt", default=DEFAULT_PROMPT)
    p.add_argument("--model", help="model id (chooses the record format and thinking; recorded for submit)")
    p.add_argument("--format", choices=["anthropic", "chat"], help="record format (default: from model id)")
    p.add_argument("--thinking", default="auto", help="auto | adaptive | none | <budget tokens> (anthropic format only)")
    p.add_argument("--max-tokens", type=int, default=None, help="default: the model's hard maximum (MODEL_MAX_TOKENS)")
    p.add_argument("--out-dir", help="where fetch writes results (default data/citation_seed/<name>)")
    p = sub.add_parser("submit", help="upload the JSONL and create the Bedrock batch job")
    p.add_argument("--name", required=True)
    p.add_argument("--model", help="model id (default $CITSEED_MODEL_ID)")
    p.add_argument("--force", action="store_true", help="submit even below the 100-record minimum")
    p = sub.add_parser("status", help="refresh and print job status")
    p.add_argument("--name")
    p = sub.add_parser("wait", help="poll one job until it finishes")
    p.add_argument("--name", required=True)
    p.add_argument("--every", type=int, default=300, help="seconds between polls")
    p = sub.add_parser("fetch", help="download results and write {cid}.response.md / .edits.json")
    p.add_argument("--name", required=True)
    p.add_argument("--out-dir")
    p.add_argument("--force", action="store_true")
    a = ap.parse_args()
    {"models": cmd_models, "export": cmd_export, "submit": cmd_submit,
     "status": cmd_status, "wait": cmd_wait, "fetch": cmd_fetch}[a.cmd](a)


if __name__ == "__main__":
    main()
