"""Run one stage of the two-stage citator (gate or labeler) as a named run.

  prepare  --stage gate  --name g_kimi_dev  --model kimi   --split dev
  prepare  --stage label --name l_son_dev   --model sonnet --split dev --from-gate g_kimi_dev [--min-confidence low]
  prepare  --stage label --name l_opus_orac --model opus   --split dev --from-gold        # oracle gate (gold positives)
  submit   --name NAME                  Bedrock batch (>= 100 records; Kimi / Sonnet / Opus / Haiku)
  status   [--name NAME]
  fetch    --name NAME                  download batch output, then parse
  run      --name NAME [--workers 4]    on-demand (InvokeModel / Converse); resumable; the only path for GPT
  parse    --name NAME                  raw -> outputs/{cid}.json + summary.json (tokens, cost, truncations)

Layout: data/runs/NAME/{meta.json, input.jsonl, inputs/{rid}.txt, raw/{rid}.json, outputs/{cid}.json, summary.json}
Record ids are the cluster id, or {cid}_page_{n} when the opinion body exceeds PAGE_CHARS.
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import random
import re
import sys
import time

from botocore.config import Config
from botocore.exceptions import ClientError, ReadTimeoutError, ConnectionError as BotoConnectionError

from common import (MODELS, PAGE_CHARS, PAGE_OVERLAP, RUNS, STAGE_PROMPTS, cluster_ids, cost_usd, is_cr,
                    load_gold, prompt_text, read_json, write_json)
from build_inputs import inventory, stage_gate_parts, stage_label_parts
from goldmap import resolve_gold_to_inventory
from records import (CONF_RANK, build_body, merge_gate, merge_label, norm_gate, norm_label,
                     parse_model_output, result_from)
from utils.batch_utils import (S3_BUCKET, BATCH_ROLE_ARN, check_job_status, download_to_file, generate_run_id,
                               list_keys, prompt_sha, s3_run_prefix, s3_uri, submit_batch_job, update_manifest,
                               upload_file, write_manifest)
from utils.bedrock_converse_utils import AWS_REGION, session
from utils.preprocess import split_opinion_to_pages

MIN_BATCH_RECORDS = 100
RETRYABLE = {"ThrottlingException", "ModelNotReadyException", "ServiceUnavailableException",
             "InternalServerException", "ModelTimeoutException", "TooManyRequestsException"}


def run_dir(name):
    return os.path.join(RUNS, name)


def meta_path(name):
    return os.path.join(run_dir(name), "meta.json")


def load_meta(name):
    m = read_json(meta_path(name))
    if m is None:
        sys.exit(f"unknown run {name!r} (no {meta_path(name)})")
    return m


def save_meta(name, m):
    write_json(meta_path(name), m)


# ── prepare ──
def flagged_from_gate(gate_names, cids, min_conf):
    """Union of the flags of one or more gate runs (comma-separated), at >= min_conf."""
    names = [n for n in gate_names.split(",") if n]
    metas = [load_meta(n) for n in names]
    out, skipped = {}, {}
    for cid in cids:
        outs = [read_json(os.path.join(run_dir(n), "outputs", f"{cid}.json")) for n in names]
        if all(o is None for o in outs):
            skipped[cid] = "no gate output"
            continue
        ids = sorted({f["id"] for o in outs if o for f in o["flagged"]
                      if CONF_RANK[f["confidence"]] >= CONF_RANK[min_conf]})
        if ids:
            out[cid] = ids
        else:
            skipped[cid] = "no flags"
    return out, skipped, {"prompt_sha": [m["prompt_sha"] for m in metas], "names": names}


def flagged_from_gold(cids):
    out, skipped = {}, {}
    gold = load_gold(cids)
    for cid in cids:
        inv = inventory(cid)
        rows = gold.get(cid, [])
        mp = resolve_gold_to_inventory(inv, rows)
        ids = sorted({mp[i] for i, r in enumerate(rows) if i in mp and is_cr(r["final"])})
        if ids:
            out[cid] = ids
        else:
            skipped[cid] = "no gold citing-reference positives"
    return out, skipped


def cmd_prepare(a):
    d = run_dir(a.name)
    if os.path.exists(meta_path(a.name)) and not a.force:
        sys.exit(f"run {a.name!r} exists; use --force to rebuild its inputs")
    os.makedirs(os.path.join(d, "inputs"), exist_ok=True)
    prompt_file = a.prompt or STAGE_PROMPTS[a.stage]
    system = prompt_text(prompt_file)
    cids = a.ids or cluster_ids(a.split)
    m = MODELS[a.model]
    flagged, skipped, gate_meta = None, {}, None
    if a.stage == "label":
        if a.from_gate:
            flagged, skipped, gate_meta = flagged_from_gate(a.from_gate, cids, a.min_confidence)
        elif a.from_gold:
            flagged, skipped = flagged_from_gold(cids)
        else:
            sys.exit("--stage label needs --from-gate NAME or --from-gold")
        cids = [c for c in cids if c in flagged]
    records, chars = [], 0
    with open(os.path.join(d, "input.jsonl"), "w", encoding="utf-8") as f:
        for cid in cids:
            prefix, body = (stage_gate_parts(cid, signals=a.signals) if a.stage == "gate"
                            else stage_label_parts(cid, flagged[cid]))
            pages = split_opinion_to_pages(body, PAGE_CHARS, PAGE_OVERLAP)
            for i, pg in enumerate(pages, 1):
                rid = cid if len(pages) == 1 else f"{cid}_page_{i}"
                user = f"{prefix}<opinion>\n{pg}\n</opinion>"
                with open(os.path.join(d, "inputs", f"{rid}.txt"), "w", encoding="utf-8") as uf:
                    uf.write(user)
                rec = build_body(a.model, a.stage, system, user, thinking=a.thinking, max_tokens=a.max_tokens,
                                 effort=a.effort)
                f.write(json.dumps({"recordId": rid, "modelInput": rec}, ensure_ascii=False) + "\n")
                records.append({"rid": rid, "cid": cid, "page": i, "n_pages": len(pages), "chars": len(user)})
                chars += len(user) + len(system)
    meta = {
        "name": a.name, "stage": a.stage, "model": a.model, "model_id": m["id"], "family": m["family"],
        "split": a.split, "ids_given": bool(a.ids), "prompt": prompt_file, "prompt_sha": prompt_sha(system),
        "thinking": a.thinking, "max_tokens": a.max_tokens or m["max_tokens"], "effort": a.effort,
        "signals": a.signals,
        "from_gate": a.from_gate, "from_gold": a.from_gold, "min_confidence": a.min_confidence,
        "gate_prompt_sha": gate_meta["prompt_sha"] if gate_meta else None,
        "flagged": flagged, "skipped": skipped, "cids": cids, "records": records,
        "mode": None, "prepared": dt.datetime.now().isoformat(timespec="seconds"),
    }
    save_meta(a.name, meta)
    tok = chars / 4 * (1.3 if a.model == "opus" else 1.0)
    out_guess = len(records) * (700 if a.stage == "gate" else 2500)
    price = m["batch_price"] if m["batch"] and m["batch_price"] else m["price"]
    print(f"{a.name}: {a.stage} / {a.model} / {a.split}: {len(cids)} clusters -> {len(records)} records "
          f"({sum(r['n_pages'] > 1 for r in records)} paginated), {chars:,} input chars ≈ {tok / 1e6:.2f}M tokens; "
          f"rough cost ≈ ${tok / 1e6 * price[0] + out_guess / 1e6 * price[1]:.2f} "
          f"({'batch' if m['batch'] else 'on-demand'} rate, output guessed)")
    if skipped:
        print(f"  {len(skipped)} clusters skipped: " + ", ".join(f"{k}={v}" for k, v in list(skipped.items())[:8])
              + (" …" if len(skipped) > 8 else ""))
    if len(records) < MIN_BATCH_RECORDS:
        print(f"  NOTE: {len(records)} < {MIN_BATCH_RECORDS} records — Bedrock batch will reject this; "
              f"use `run --name {a.name}` (on-demand) instead")


# ── batch ──
def _s3_prefix(meta):
    return f"{s3_run_prefix(meta['run_id'])}/twostage/{meta['name']}"


def cmd_submit(a):
    meta = load_meta(a.name)
    m = MODELS[meta["model"]]
    if not m["batch"]:
        sys.exit(f"{meta['model']} ({m['id']}) is not batch-eligible on Bedrock; use `run`")
    if len(meta["records"]) < MIN_BATCH_RECORDS and not a.force:
        sys.exit(f"{len(meta['records'])} records < {MIN_BATCH_RECORDS}; use `run` or --force")
    if not S3_BUCKET or not BATCH_ROLE_ARN:
        sys.exit("export CITATOR_S3_BUCKET and CITATOR_BATCH_ROLE_ARN first")
    if meta.get("job_arn") and not a.force:
        sys.exit(f"already submitted as {meta['job_arn']}; --force to resubmit")
    meta["run_id"] = meta.get("run_id") or generate_run_id()
    prefix = _s3_prefix(meta)
    in_uri = upload_file(os.path.join(run_dir(a.name), "input.jsonl"), f"{prefix}/input.jsonl")
    out_uri = s3_uri(f"{prefix}/raw/")
    job_name = re.sub(r"[^A-Za-z0-9-]", "-", f"citator-2s-{meta['stage']}-{a.name}")[:40] + f"-{int(time.time())}"
    job_arn = submit_batch_job(job_name, in_uri, out_uri, model_id=m["id"])
    meta.update({"mode": "batch", "job_arn": job_arn, "job_name": job_name, "s3_input": in_uri,
                 "s3_raw_prefix": f"{prefix}/raw/", "submitted": dt.datetime.now().isoformat(timespec="seconds")})
    save_meta(a.name, meta)
    write_manifest(meta["run_id"], {"experiment": "experiments_09112026", "run": a.name, "stage": meta["stage"],
                                    "model": m["id"], "prompt_sha": meta["prompt_sha"],
                                    "n_records": len(meta["records"]), "job_arn": job_arn})
    print(f"submitted {job_name}\n  {job_arn}\n  input {in_uri}")


def _status(meta):
    st = check_job_status(meta["job_arn"])
    meta["status"] = st.get("status")
    meta["status_message"] = st.get("message", "")
    return st


def cmd_status(a):
    names = [a.name] if a.name else sorted(os.listdir(RUNS)) if os.path.exists(RUNS) else []
    for n in names:
        meta = read_json(meta_path(n))
        if not meta:
            continue
        if meta.get("job_arn"):
            st = _status(meta)
            save_meta(n, meta)
            print(f"{n:28s} {meta['stage']:5s} {meta['model']:6s} batch    {st.get('status', '?'):18s} "
                  f"{len(meta['records'])} records  {st.get('message', '')[:60]}")
        else:
            done = len([f for f in os.listdir(os.path.join(run_dir(n), "raw"))]) if os.path.exists(
                os.path.join(run_dir(n), "raw")) else 0
            print(f"{n:28s} {meta['stage']:5s} {meta['model']:6s} {meta.get('mode') or 'prepared':8s} "
                  f"{done}/{len(meta['records'])} raw")


def cmd_fetch(a):
    meta = load_meta(a.name)
    if not meta.get("job_arn"):
        sys.exit("never submitted")
    st = _status(meta)
    save_meta(a.name, meta)
    if st.get("status") not in ("Completed", "PartiallyCompleted") and not a.force:
        sys.exit(f"job status {st.get('status')} ({st.get('message', '')}); --force to fetch what exists")
    raw_dir = os.path.join(run_dir(a.name), "raw")
    os.makedirs(raw_dir, exist_ok=True)
    n = 0
    for key in list_keys(meta["s3_raw_prefix"]):
        if not key.endswith(".jsonl.out"):
            continue
        local = os.path.join(run_dir(a.name), "batch_out", os.path.basename(key))
        os.makedirs(os.path.dirname(local), exist_ok=True)
        download_to_file(key, local)
        with open(local, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rec = json.loads(line)
                    rec.pop("modelInput", None)
                    write_json(os.path.join(raw_dir, f"{rec['recordId']}.json"), rec)
                    n += 1
    meta["fetched"] = dt.datetime.now().isoformat(timespec="seconds")
    save_meta(a.name, meta)
    update_manifest(meta["run_id"], {"fetched": meta["fetched"], "n_records_out": n})
    print(f"fetched {n} records -> {raw_dir}")
    cmd_parse(a)


# ── on-demand ──
def _client():
    return session.client("bedrock-runtime", region_name=AWS_REGION,
                          config=Config(read_timeout=1500, connect_timeout=30, retries={"max_attempts": 0}))


def _jsonable(o):
    """Converse responses can carry bytes (redacted reasoning content); make the raw record serializable."""
    if isinstance(o, bytes):
        return f"<{len(o)} bytes>"
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_jsonable(v) for v in o]
    return o


def invoke_once(client, model_id, body):
    if body.get("_converse"):
        kwargs = dict(modelId=model_id, system=[{"text": body["system"]}],
                      messages=[{"role": "user", "content": [{"text": body["user"]}]}],
                      inferenceConfig={"maxTokens": body["max_tokens"]})
        eff = body.get("reasoning_effort")
        variants = [{"reasoning": {"effort": eff}}, {"reasoning_effort": eff}] if eff else [None]
        last = None
        for extra in variants:
            try:
                if extra:
                    kwargs["additionalModelRequestFields"] = extra
                r = client.converse(**kwargs)
                r.pop("ResponseMetadata", None)
                if extra:
                    r["_request_fields"] = extra
                return _jsonable(r)
            except ClientError as e:
                if e.response["Error"]["Code"] == "ValidationException" and extra is not None and len(variants) > 1:
                    last = e
                    continue
                raise
        raise last
    r = client.invoke_model(modelId=model_id, body=json.dumps(body), contentType="application/json",
                            accept="application/json")
    return json.loads(r["body"].read())


def invoke_with_retry(client, model_id, body, tries=7):
    for attempt in range(tries):
        try:
            return invoke_once(client, model_id, body), None
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code not in RETRYABLE or attempt == tries - 1:
                return None, f"{code}: {e.response['Error'].get('Message', '')[:300]}"
        except (ReadTimeoutError, BotoConnectionError) as e:
            if attempt == tries - 1:
                return None, f"{type(e).__name__}: {str(e)[:200]}"
        time.sleep(min(90, 3 * 2 ** attempt) + random.uniform(0, 2))
    return None, "exhausted retries"


def cmd_run(a):
    meta = load_meta(a.name)
    m = MODELS[meta["model"]]
    raw_dir = os.path.join(run_dir(a.name), "raw")
    os.makedirs(raw_dir, exist_ok=True)
    bodies = {}
    with open(os.path.join(run_dir(a.name), "input.jsonl"), encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            bodies[rec["recordId"]] = rec["modelInput"]
    todo = []
    for r in meta["records"]:
        p = os.path.join(raw_dir, f"{r['rid']}.json")
        prev = read_json(p)
        if prev is None or (a.retry_errors and prev.get("error")):
            todo.append(r["rid"])
    if a.limit:
        todo = todo[: a.limit]
    print(f"{a.name}: {len(todo)} of {len(meta['records'])} records to run on {m['id']} with {a.workers} workers")
    if not todo:
        cmd_parse(a)
        return
    meta["mode"] = meta.get("mode") or "ondemand"
    meta["run_started"] = meta.get("run_started") or dt.datetime.now().isoformat(timespec="seconds")
    save_meta(a.name, meta)
    client = _client()
    t0 = time.time()
    done = [0]

    def work(rid):
        out, err = invoke_with_retry(client, m["id"], bodies[rid])
        rec = {"recordId": rid, "modelOutput": out} if out is not None else {"recordId": rid, "error": err}
        write_json(os.path.join(raw_dir, f"{rid}.json"), rec)
        done[0] += 1
        if done[0] % 10 == 0 or done[0] == len(todo):
            print(f"  {done[0]}/{len(todo)} done, {time.time() - t0:.0f}s" + (f"  last error: {err}" if err else ""))
        return rid, err

    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        results = list(ex.map(work, todo))
    errs = [(rid, e) for rid, e in results if e]
    print(f"finished: {len(todo) - len(errs)} ok, {len(errs)} errors in {time.time() - t0:.0f}s")
    for rid, e in errs[:10]:
        print("  ERROR", rid, e)
    cmd_parse(a)


# ── fill: re-query flagged ids a labeler left out ──
def cmd_fill(a):
    """Label stage only. For every cluster whose output lacks some flagged ids, add a record that
    carries just those ids (same prompt/model), run it, and re-parse; merge_label keeps the union."""
    meta = load_meta(a.name)
    if meta["stage"] != "label":
        sys.exit("fill is for label runs")
    system = prompt_text(meta["prompt"])
    out_dir = os.path.join(run_dir(a.name), "outputs")
    existing = {r["rid"] for r in meta["records"]}
    n_new = 0
    with open(os.path.join(run_dir(a.name), "input.jsonl"), "a", encoding="utf-8") as f:
        for cid, ids in meta["flagged"].items():
            o = read_json(os.path.join(out_dir, f"{cid}.json"))
            have = {c["id"] for c in o["citedCases"]} if o else set()
            missing = sorted(set(ids) - have)
            if not missing:
                continue
            k = 1
            while f"{cid}_fill{k}" in existing:
                k += 1
            rid = f"{cid}_fill{k}"
            prefix, body = stage_label_parts(cid, missing)
            user = f"{prefix}<opinion>\n{body}\n</opinion>"
            with open(os.path.join(run_dir(a.name), "inputs", f"{rid}.txt"), "w", encoding="utf-8") as uf:
                uf.write(user)
            rec = build_body(meta["model"], "label", system, user, thinking=meta.get("thinking", "model"),
                             max_tokens=meta.get("max_tokens"))
            f.write(json.dumps({"recordId": rid, "modelInput": rec}, ensure_ascii=False) + "\n")
            meta["records"].append({"rid": rid, "cid": cid, "page": 1, "n_pages": 1, "chars": len(user),
                                    "fill_ids": missing})
            existing.add(rid)
            n_new += 1
    save_meta(a.name, meta)
    print(f"{a.name}: {n_new} clusters had flagged ids without a label; added {n_new} fill records")
    if n_new:
        cmd_run(a)


# ── parse ──
def cmd_parse(a):
    meta = load_meta(a.name)
    raw_dir = os.path.join(run_dir(a.name), "raw")
    out_dir = os.path.join(run_dir(a.name), "outputs")
    os.makedirs(out_dir, exist_ok=True)
    by_cid, missing, errors, truncated, stops = {}, [], {}, [], {}
    tin = tout = 0
    for r in meta["records"]:
        rec = read_json(os.path.join(raw_dir, f"{r['rid']}.json"))
        if rec is None:
            missing.append(r["rid"])
            continue
        if rec.get("error") or "modelOutput" not in rec:
            errors[r["rid"]] = str(rec.get("error", "no modelOutput"))[:300]
            continue
        parsed = parse_model_output(rec["modelOutput"])
        tin += parsed["in_tokens"]
        tout += parsed["out_tokens"]
        stops[parsed["stop_reason"]] = stops.get(parsed["stop_reason"], 0) + 1
        if parsed["stop_reason"] in ("max_tokens", "length"):
            truncated.append(r["rid"])
        try:
            result = result_from(parsed)
            page = norm_gate(result) if meta["stage"] == "gate" else norm_label(result)
        except (ValueError, json.JSONDecodeError, TypeError) as e:
            errors[r["rid"]] = f"unparseable: {e}"
            with open(os.path.join(out_dir, f"{r['rid']}.badtext.txt"), "w", encoding="utf-8") as f:
                f.write(parsed["text"])
            continue
        by_cid.setdefault(r["cid"], []).append(page)
    for cid, pages in by_cid.items():
        merged = merge_gate(pages) if meta["stage"] == "gate" else merge_label(pages)
        merged["cid"] = cid
        write_json(os.path.join(out_dir, f"{cid}.json"), merged)
    batch = meta.get("mode") == "batch"
    summary = {
        "n_records": len(meta["records"]), "n_ok": len(meta["records"]) - len(missing) - len(errors),
        "n_missing": len(missing), "n_errors": len(errors), "errors": errors, "truncated": truncated,
        "stop_reasons": stops, "input_tokens": tin, "output_tokens": tout,
        "cost_usd": round(cost_usd(meta["model"], tin, tout, batch), 2), "rate": "batch" if batch else "on-demand",
        "n_clusters_out": len(by_cid),
    }
    if meta["stage"] == "gate":
        n_flag = sum(len(read_json(os.path.join(out_dir, f"{c}.json"))["flagged"]) for c in by_cid)
        n_inv = sum(len(inventory(c)) for c in by_cid)
        nv = [read_json(os.path.join(out_dir, f"{c}.json")).get("n_verdicts") for c in by_cid]
        if any(v is not None for v in nv):
            summary["verdict_coverage"] = round(sum(v or 0 for v in nv) / n_inv, 3) if n_inv else None
        summary.update({"clusters_with_flags": sum(1 for c in by_cid if read_json(
            os.path.join(out_dir, f"{c}.json"))["flagged"]), "flagged_ids": n_flag, "inventory_ids": n_inv})
    else:
        labs = {}
        for c in by_cid:
            for e in read_json(os.path.join(out_dir, f"{c}.json"))["citedCases"]:
                labs[e["treatment"]] = labs.get(e["treatment"], 0) + 1
        summary["label_counts"] = labs
    write_json(os.path.join(run_dir(a.name), "summary.json"), summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "errors"}, indent=1))
    if errors:
        print(f"{len(errors)} record errors, e.g.: " + "; ".join(f"{k}: {v[:80]}" for k, v in list(errors.items())[:5]))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("prepare")
    s.add_argument("--stage", required=True, choices=["gate", "label"])
    s.add_argument("--name", required=True)
    s.add_argument("--model", required=True, choices=sorted(MODELS))
    s.add_argument("--split", default="dev", choices=["dev", "test", "all"])
    s.add_argument("--ids", nargs="*", help="explicit cluster ids (smoke tests)")
    s.add_argument("--prompt", help="prompt file (default: prompts/<stage>_v1.md)")
    s.add_argument("--thinking", default="model", help="model | none | adaptive | <budget tokens> (Anthropic only)")
    s.add_argument("--max-tokens", type=int, default=None)
    s.add_argument("--effort", default=None, help="GPT reasoning effort override: low | medium | high")
    s.add_argument("--signals", action="store_true", help="gate: add contrast-word hints per inventory entry")
    s.add_argument("--from-gate", help="label stage: gate run(s) whose flags to label; comma-separated = union")
    s.add_argument("--from-gold", action="store_true", help="label stage: oracle gate = gold citing-reference positives")
    s.add_argument("--min-confidence", default="low", choices=["low", "medium", "high"])
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_prepare)
    for name, fn in (("submit", cmd_submit), ("fetch", cmd_fetch), ("parse", cmd_parse)):
        q = sub.add_parser(name)
        q.add_argument("--name", required=True)
        q.add_argument("--force", action="store_true")
        q.set_defaults(func=fn)
    st = sub.add_parser("status")
    st.add_argument("--name")
    st.set_defaults(func=cmd_status)
    for name, fn in (("run", cmd_run), ("fill", cmd_fill)):
        r = sub.add_parser(name)
        r.add_argument("--name", required=True)
        r.add_argument("--workers", type=int, default=4)
        r.add_argument("--limit", type=int, default=0, help="run at most N pending records (smoke)")
        r.add_argument("--retry-errors", action="store_true")
        r.set_defaults(func=fn)
    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
