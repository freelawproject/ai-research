"""OpenAI Batch API runner for the citation seeding pass (GPT models).

Mirror of bedrock_batch.py for OpenAI: same inputs (data/citation_seed/inputs/
{cid}.txt from `citation_seed.py prepare`), same outputs
(<out-dir>/{cid}.response.md + {cid}.edits.json), same bookkeeping file
(data/citation_seed/bedrock_jobs.json, key = batch name, provider "openai").
Client pattern follows prior_experiments/experiments_04012026/utils/gpt_utils.py (OpenAI(),
key from $OPENAI_KEY).

    bash openai_batch.sh models                       # gpt-5* model ids visible to the key
    bash openai_batch.sh export --name dev_gpt --ids $(cat data/citation_seed/dev_ids.txt) --model <id>
    bash openai_batch.sh submit --name dev_gpt        # upload file + create batch (24h window)
    bash openai_batch.sh status | wait --name dev_gpt | fetch --name dev_gpt
    bash openai_batch.sh run --name dev_gpt --ids ... --model <id>   # real-time instead of batch
    python3 lib/citation_seed.py score --ids ... --out-dir data/citation_seed/dev_gpt

The OpenAI Batch API has no minimum batch size (limits: 50,000 requests /
200 MB per file), so dev-only runs need no padding.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from openai import OpenAI

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bedrock_batch import (  # noqa: E402
    ROOT, SEED, INPUTS, BATCHES, JOBS, DEFAULT_PROMPT, load_json, save_json, jobs, extract_edits_json,
)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
from candidates import candidates_block  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "..", "..", "citator-pipeline"))
from utils.batch_utils import prompt_sha  # noqa: E402

DEFAULT_MODEL = os.environ.get("CITSEED_OPENAI_MODEL", "")
TERMINAL = ("completed", "failed", "expired", "cancelled")


BEDROCK_GPT = "us.openai.gpt-5.6-luna"
BEDROCK_MAX_TOKENS = 64000   # Bedrock's cap for GPT-5.6, reasoning included (OpenAI's is 128K)
_EFFORT_FIELDS = [lambda e: {"reasoning": {"effort": e}}, lambda e: {"reasoning_effort": e}]


def bedrock_client():
    """Same model, different transport: GPT-5.6 Luna on Bedrock Converse with the
    dev-env SSO session (what experiments_09112026 runs its gate on) — no OpenAI key."""
    session = boto3.Session(profile_name=os.environ.get("AWS_PROFILE", "dev-env"))
    return session.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-west-2"),
                          config=Config(read_timeout=1500, connect_timeout=30,
                                        retries={"max_attempts": 8, "mode": "adaptive"}))


def converse_once(c, model, prompt, user, max_tokens, effort):
    """One Converse call. Bedrock rejects temperature alongside reasoning, and the
    reasoning-effort field name differs by model version, so the two known
    shapes are probed once and the working one is kept for the rest of the run."""
    kwargs = dict(modelId=model, system=[{"text": prompt}],
                  messages=[{"role": "user", "content": [{"text": user}]}],
                  inferenceConfig={"maxTokens": max_tokens})
    if not effort or effort == "none":
        return c.converse(**kwargs)
    last = None
    for i, make in enumerate(list(_EFFORT_FIELDS)):
        try:
            kwargs["additionalModelRequestFields"] = make(effort)
            r = c.converse(**kwargs)
            if i:                                   # remember the shape that worked
                _EFFORT_FIELDS.insert(0, _EFFORT_FIELDS.pop(i))
            return r
        except ClientError as e:
            if e.response["Error"]["Code"] == "ValidationException" and i < len(_EFFORT_FIELDS) - 1:
                last = e
                continue
            raise
    raise last


def client():
    key = os.getenv("OPENAI_KEY")
    if not key:
        sys.exit("OPENAI_KEY is not set (same variable prior_experiments/experiments_04012026/utils/gpt_utils.py uses)")
    return OpenAI(api_key=key)


def build_request(cid, prompt, text, model, max_tokens, effort):
    body = {
        "model": model,
        "messages": [{"role": "system", "content": prompt},
                     {"role": "user", "content": text}],
        "max_completion_tokens": max_tokens,
    }
    if effort and effort != "none":
        body["reasoning_effort"] = effort  # GPT-5 family; temperature is not accepted with reasoning
    else:
        body["temperature"] = 0
    return {"custom_id": str(cid), "method": "POST", "url": "/v1/chat/completions", "body": body}


# ------------------------------------------------------------------ models
def cmd_models(a):
    ids = sorted(m.id for m in client().models.list().data if a.filter.lower() in m.id.lower())
    print("\n".join(ids) or f"no models matching {a.filter!r}")


# ------------------------------------------------------------------ export
MAX_FILE_BYTES = 190 * 1024 * 1024   # OpenAI Batch input file limit is 200 MB


def _resolve_prompt(a):
    if not os.path.exists(a.prompt) and os.path.exists(os.path.join(ROOT, a.prompt)):
        a.prompt = os.path.join(ROOT, a.prompt)


def cmd_export(a):
    _resolve_prompt(a)
    """Write one or more batch JSONL files. Files are split so each stays under
    MAX_FILE_BYTES (and under --part-size requests); each part is registered as
    its own batch name `<name>_pNN` sharing one out_dir, so score/apply see a
    single result directory."""
    model = a.model or DEFAULT_MODEL
    if not model:
        sys.exit("no model id: pass --model or set CITSEED_OPENAI_MODEL (see `models`)")
    prompt = open(a.prompt, encoding="utf-8").read()
    os.makedirs(BATCHES, exist_ok=True)
    missing = [c for c in a.ids if not os.path.exists(os.path.join(INPUTS, f"{c}.txt"))]
    if missing:
        sys.exit(f"{len(missing)} ids have no prepared input (run `python3 lib/citation_seed.py prepare --ids ...`): "
                 + " ".join(missing[:20]))
    out_dir = os.path.relpath(a.out_dir or os.path.join(SEED, a.name), ROOT)
    js = jobs()
    parts, cur, cur_bytes, n_chars, part_no = [], [], 0, 0, 0

    def flush():
        nonlocal cur, cur_bytes, part_no
        if not cur:
            return
        part_no += 1
        pname = f"{a.name}_p{part_no:02d}" if (a.part_size or len(a.ids) > len(cur) or part_no > 1) else a.name
        path = os.path.join(BATCHES, f"{pname}.openai.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for line in cur:
                f.write(line)
        ids = [json.loads(l)["custom_id"] for l in cur]
        js[pname] = {"name": pname, "provider": "openai", "parent": a.name, "ids": ids, "model": model,
                     "prompt": os.path.relpath(a.prompt, ROOT), "prompt_sha": prompt_sha(prompt),
                     "reasoning_effort": a.effort, "max_tokens": a.max_tokens, "candidates": bool(a.candidates),
                     "jsonl": os.path.relpath(path, ROOT), "bytes": os.path.getsize(path),
                     "exported": dt.datetime.now().isoformat(timespec="seconds"), "out_dir": out_dir}
        parts.append((pname, len(ids), os.path.getsize(path)))
        cur, cur_bytes = [], 0

    for cid in a.ids:
        text = open(os.path.join(INPUTS, f"{cid}.txt"), encoding="utf-8").read()
        user = text
        if a.candidates:
            block = candidates_block(text)
            if block:
                user = text.rstrip() + "\n\n" + block + "\n"
        line = json.dumps(build_request(cid, prompt, user, model, a.max_tokens, a.effort), ensure_ascii=False) + "\n"
        b = len(line.encode("utf-8"))
        if cur and (cur_bytes + b > MAX_FILE_BYTES or (a.part_size and len(cur) >= a.part_size)):
            flush()
        cur.append(line)
        cur_bytes += b
        n_chars += len(prompt) + len(user)
    flush()
    save_json(JOBS, js)
    print(f"{len(a.ids)} requests in {len(parts)} file(s) (~{n_chars/3.6/1e6:.2f}M input tokens); model={model}; "
          f"prompt={os.path.relpath(a.prompt, ROOT)}; reasoning_effort={a.effort}; max_completion_tokens={a.max_tokens}; "
          f"candidates={bool(a.candidates)}; out_dir={out_dir}")
    for pname, n, by in parts:
        print(f"  {pname}: {n} requests, {by/1e6:.1f} MB")


# ------------------------------------------------------------------ submit
def cmd_submit(a):
    js = jobs()
    meta = js.get(a.name) or sys.exit(f"no export named {a.name}")
    if meta.get("provider") != "openai":
        sys.exit(f"{a.name} is not an OpenAI export")
    c = client()
    with open(os.path.join(ROOT, meta["jsonl"]), "rb") as f:
        up = c.files.create(file=f, purpose="batch")
    b = c.batches.create(input_file_id=up.id, endpoint="/v1/chat/completions", completion_window="24h",
                         metadata={"task": "citation_seed", "batch": a.name, "prompt": meta["prompt"]})
    meta.update({"input_file_id": up.id, "batch_id": b.id, "status": b.status,
                 "submitted": dt.datetime.now().isoformat(timespec="seconds")})
    save_json(JOBS, js)
    print(f"submitted OpenAI batch {b.id} ({b.status}); {len(meta['ids'])} requests; model {meta['model']}")


# ------------------------------------------------------------------ status
def _refresh(c, meta):
    b = c.batches.retrieve(meta["batch_id"])
    meta["status"] = b.status
    meta["counts"] = dict(b.request_counts) if b.request_counts else {}
    meta["output_file_id"] = b.output_file_id
    meta["error_file_id"] = b.error_file_id
    meta["checked"] = dt.datetime.now().isoformat(timespec="seconds")
    return b


def cmd_status(a):
    js = jobs()
    names = [a.name] if a.name else [n for n, m in js.items() if m.get("provider") == "openai"]
    if not names:
        print("no OpenAI batches tracked in", JOBS)
        return
    c = client()
    for n in names:
        meta = js[n]
        if not meta.get("batch_id"):
            print(f"{n:24} exported only ({len(meta.get('ids', []))} requests, {meta.get('model')})")
            continue
        b = _refresh(c, meta)
        print(f"{n:24} {b.status:12} {meta.get('model','')}  {meta.get('counts')}  submitted {meta.get('submitted','')}")
    save_json(JOBS, js)


def cmd_wait(a):
    js = jobs()
    meta = js[a.name]
    c = client()
    while True:
        b = _refresh(c, meta)
        save_json(JOBS, js)
        print(dt.datetime.now().strftime("%H:%M:%S"), b.status, meta.get("counts"))
        if b.status in TERMINAL:
            break
        time.sleep(a.every)


# ------------------------------------------------------------------ fetch
def cmd_fetch(a):
    js = jobs()
    meta = js.get(a.name) or sys.exit(f"unknown batch {a.name}")
    c = client()
    b = _refresh(c, meta)
    save_json(JOBS, js)
    if b.status != "completed" and not a.force:
        sys.exit(f"batch status is {b.status} {meta.get('counts')}; use --force to fetch whatever exists")
    out_dir = os.path.join(ROOT, a.out_dir or meta["out_dir"])
    raw_dir = os.path.join(BATCHES, f"{a.name}.openai.out")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(raw_dir, exist_ok=True)
    n_ok, n_err, n_dropped, errors, usage = 0, 0, 0, {}, meta.setdefault("usage", {})
    for fid, name in ((meta.get("output_file_id"), "output.jsonl"), (meta.get("error_file_id"), "errors.jsonl")):
        if not fid:
            continue
        content = c.files.content(fid).text
        with open(os.path.join(raw_dir, name), "w", encoding="utf-8") as f:
            f.write(content)
        if name == "errors.jsonl":
            for line in content.splitlines():
                if line.strip():
                    rec = json.loads(line)
                    errors[rec.get("custom_id")] = rec.get("error") or rec.get("response", {}).get("body")
                    n_err += 1
            continue
        for line in content.splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            cid = rec.get("custom_id")
            resp = rec.get("response") or {}
            body = resp.get("body") or {}
            if rec.get("error") or resp.get("status_code", 200) != 200 or not body.get("choices"):
                n_err += 1
                errors[cid] = rec.get("error") or body.get("error") or f"status {resp.get('status_code')}"
                continue
            ch = body["choices"][0]
            text = (ch.get("message") or {}).get("content") or ""
            with open(os.path.join(out_dir, f"{cid}.response.md"), "w", encoding="utf-8") as f:
                f.write(text)
            try:
                edits = extract_edits_json(text)
                if not a.no_postfilter:
                    src = open(os.path.join(INPUTS, f"{cid}.txt"), encoding="utf-8").read()
                    edits, dropped = postfilter_adds(edits, src)
                    if dropped:
                        n_dropped += dropped
            except Exception as e:
                n_err += 1
                errors[cid] = f"unparseable edits ({ch.get('finish_reason')}): {e}"
                continue
            with open(os.path.join(out_dir, f"{cid}.edits.json"), "w", encoding="utf-8") as f:
                json.dump(edits, f, indent=1, ensure_ascii=False)
            n_ok += 1
            u = body.get("usage", {})
            usage[cid] = {"in": u.get("prompt_tokens"), "out": u.get("completion_tokens"),
                          "reasoning": ((u.get("completion_tokens_details") or {}).get("reasoning_tokens")),
                          "finish": ch.get("finish_reason")}
    meta.update({"fetched": dt.datetime.now().isoformat(timespec="seconds"), "n_ok": n_ok, "n_err": n_err})
    if errors:
        save_json(os.path.join(BATCHES, f"{a.name}.openai.errors.json"), errors)
    save_json(JOBS, js)
    tin = sum((v.get("in") or 0) for v in usage.values())
    tout = sum((v.get("out") or 0) for v in usage.values())
    print(f"{n_ok} ok, {n_err} errors, post-filter dropped {n_dropped} adds -> {out_dir}  (usage: {tin:,} in / {tout:,} out tokens)")
    if errors:
        print(f"errors in {os.path.join(BATCHES, a.name + '.openai.errors.json')}: " + " ".join(str(k) for k in list(errors)[:20]))
    rel = os.path.relpath(out_dir, ROOT)
    print(f"next: python3 lib/citation_seed.py score|apply --ids <ids> --out-dir {rel}")


# ------------------------------------------------------------------ real-time
_TAG = re.compile(r"<c\b[^>]*>")
_CITE_AFTER = re.compile(r"^[,;:\s]*(?:<c\b|\d{1,4}\s+[A-Z][A-Za-z.\s]{0,12}\d|No\.\s*\d|\d{2,4}-\d{2,5}\s*\()")


def postfilter_adds(edits, opinion_text):
    """Deterministic guard for a weak model's judgment on two mechanical cases:
    (1) an added name that is immediately followed by a reporter citation, a
    tagged span, a docket number or a public-domain cite is a fragment of a
    name that introduces its own citation -> drop; (2) duplicate adds (same
    text + before) -> keep one. Returns (edits, n_dropped)."""
    seen, kept, dropped = set(), [], 0
    plain = opinion_text
    for e in edits.get("add", []):
        key = (e.get("text"), e.get("before"), e.get("opinion"))
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        text, before = e.get("text") or "", e.get("before") or ""
        if text and not re.search(r"\d", text) and "supra" not in text.lower():  # name-only adds only
            i = plain.find(before + text) if before else -1
            if i >= 0:
                after = plain[i + len(before) + len(text): i + len(before) + len(text) + 60]
                if _CITE_AFTER.match(after):
                    dropped += 1
                    continue
        kept.append(e)
    edits["add"] = kept
    return edits, dropped


def _one(c, cid, prompt, model, max_tokens, effort, out_dir, postfilter=True, with_candidates=False,
         provider="openai"):
    text = open(os.path.join(INPUTS, f"{cid}.txt"), encoding="utf-8").read()
    user = text
    if with_candidates:
        block = candidates_block(text)
        if block:
            user = text.rstrip() + "\n\n" + block + "\n"
    if provider == "bedrock":
        r = converse_once(c, model, prompt, user, max_tokens, effort)
        blocks = r["output"]["message"]["content"]
        content = "".join(b.get("text", "") for b in blocks if "text" in b)
        u = r.get("usage", {})
        rec = {"in": u.get("inputTokens"), "out": u.get("outputTokens"),
               "reasoning": None, "finish": r.get("stopReason")}
    else:
        body = build_request(cid, prompt, user, model, max_tokens, effort)["body"]
        resp = c.chat.completions.create(**body)
        ch = resp.choices[0]
        content = ch.message.content or ""
        u = resp.usage
        rec = {"in": u.prompt_tokens, "out": u.completion_tokens,
               "reasoning": getattr(getattr(u, "completion_tokens_details", None), "reasoning_tokens", None),
               "finish": ch.finish_reason}
    with open(os.path.join(out_dir, f"{cid}.response.md"), "w", encoding="utf-8") as f:
        f.write(content)
    edits = extract_edits_json(content)  # raises on failure -> caller records the error
    if postfilter:
        edits, rec["dropped_adds"] = postfilter_adds(edits, text)
    with open(os.path.join(out_dir, f"{cid}.edits.json"), "w", encoding="utf-8") as f:
        json.dump(edits, f, indent=1, ensure_ascii=False)
    return rec


def cmd_run(a):
    """Real-time inference (no 24h window, no minimum): N concurrent chat
    completions, same request body / outputs / bookkeeping as the batch path."""
    bedrock = a.provider == "bedrock"
    model = a.model or (BEDROCK_GPT if bedrock else DEFAULT_MODEL)
    if not model:
        sys.exit("no model id: pass --model or set CITSEED_OPENAI_MODEL")
    # the .sh wrapper cd's into runners/; accept prompt paths written relative to the triage root
    if not os.path.exists(a.prompt) and os.path.exists(os.path.join(ROOT, a.prompt)):
        a.prompt = os.path.join(ROOT, a.prompt)
    max_tokens = a.max_tokens
    if bedrock and max_tokens > BEDROCK_MAX_TOKENS:
        print(f"note: Bedrock caps GPT-5.6 output at {BEDROCK_MAX_TOKENS:,} tokens (reasoning included); using that")
        max_tokens = BEDROCK_MAX_TOKENS
    prompt = open(a.prompt, encoding="utf-8").read()
    out_dir = os.path.join(ROOT, a.out_dir or os.path.join(SEED, a.name))
    os.makedirs(out_dir, exist_ok=True)
    todo = [c for c in a.ids if a.redo or not os.path.exists(os.path.join(out_dir, f"{c}.edits.json"))]
    missing = [c for c in todo if not os.path.exists(os.path.join(INPUTS, f"{c}.txt"))]
    if missing:
        sys.exit(f"{len(missing)} ids have no prepared input: " + " ".join(missing[:20]))
    if bedrock:
        c = bedrock_client()
    else:
        c = client().with_options(max_retries=6, timeout=1800)

    def save_meta(meta):  # other runs may be saving concurrently: merge only our key
        cur = jobs()
        cur[a.name] = meta
        save_json(JOBS, cur)

    js = jobs()
    meta = js.setdefault(a.name, {})
    meta.update({"name": a.name, "provider": "bedrock-converse" if bedrock else "openai", "mode": "realtime",
                 "ids": list(a.ids), "model": model,
                 "prompt": os.path.relpath(a.prompt, ROOT), "prompt_sha": prompt_sha(prompt),
                 "reasoning_effort": a.effort, "max_tokens": max_tokens, "workers": a.workers, "candidates": bool(a.candidates),
                 "started": meta.get("started") or dt.datetime.now().isoformat(timespec="seconds"),
                 "out_dir": os.path.relpath(out_dir, ROOT)})
    usage = meta.setdefault("usage", {})
    errors = meta.setdefault("errors", {})
    print(f"{len(todo)} to run ({len(a.ids) - len(todo)} already done) with {model}, {a.workers} workers -> {out_dir}")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(_one, c, cid, prompt, model, max_tokens, a.effort, out_dir, not a.no_postfilter, a.candidates,
                          a.provider): cid for cid in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            cid = futs[fut]
            try:
                usage[cid] = fut.result()
                errors.pop(cid, None)
                r = usage[cid]
                print(f"[{i}/{len(todo)}] {cid}: {r['in']:,} in / {r['out']:,} out (reasoning {r['reasoning']}) {r['finish']}"
                      + (f" dropped {r['dropped_adds']} adds" if r.get("dropped_adds") else ""))
            except Exception as e:  # keep going; the response.md (if written) stays for inspection
                errors[cid] = str(e)[:300]
                print(f"[{i}/{len(todo)}] {cid}: ERROR {str(e)[:120]}")
            save_meta(meta)
    meta["finished"] = dt.datetime.now().isoformat(timespec="seconds")
    save_meta(meta)
    tin = sum((v.get("in") or 0) for v in usage.values())
    tout = sum((v.get("out") or 0) for v in usage.values())
    print(f"done in {time.time() - t0:,.0f}s: {len(usage)} ok, {len(errors)} errors; usage {tin:,} in / {tout:,} out tokens")
    if errors:
        print("errors: " + " ".join(errors))
    print(f"next: python3 lib/citation_seed.py score --ids <ids> --out-dir {os.path.relpath(out_dir, ROOT)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("models", help="list model ids visible to the key")
    p.add_argument("--filter", default="gpt-5")
    p = sub.add_parser("export", help="build the batch JSONL from prepared inputs")
    p.add_argument("--name", required=True)
    p.add_argument("--ids", nargs="+", required=True)
    p.add_argument("--model", help="model id (default $CITSEED_OPENAI_MODEL)")
    p.add_argument("--prompt", default=DEFAULT_PROMPT)
    p.add_argument("--effort", default="medium", help="reasoning_effort: none | minimal | low | medium | high")
    p.add_argument("--max-tokens", type=int, default=32000, help="max_completion_tokens (includes reasoning tokens)")
    p.add_argument("--candidates", action="store_true", help="append the mechanical <candidates> block to each input")
    p.add_argument("--part-size", type=int, help="max requests per file (files also split at ~190 MB)")
    p.add_argument("--out-dir")
    p = sub.add_parser("run", help="real-time inference over the ids (concurrent chat completions)")
    p.add_argument("--name", required=True)
    p.add_argument("--ids", nargs="+", required=True)
    p.add_argument("--model", help="model id (default $CITSEED_OPENAI_MODEL)")
    p.add_argument("--prompt", default=DEFAULT_PROMPT)
    p.add_argument("--effort", default="medium")
    p.add_argument("--max-tokens", type=int, default=128000)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--redo", action="store_true", help="re-run ids that already have edits.json")
    p.add_argument("--no-postfilter", action="store_true", help="disable the fragment/duplicate add guard")
    p.add_argument("--candidates", action="store_true", help="append the mechanical <candidates> block (candidates.py) to each input")
    p.add_argument("--provider", choices=["openai", "bedrock"], default="openai",
                   help="bedrock = the same GPT-5.6 Luna through Bedrock Converse (dev-env SSO, no OPENAI_KEY; 64K output cap)")
    p.add_argument("--out-dir")
    p = sub.add_parser("submit")
    p.add_argument("--name", required=True)
    p = sub.add_parser("status")
    p.add_argument("--name")
    p = sub.add_parser("wait")
    p.add_argument("--name", required=True)
    p.add_argument("--every", type=int, default=300)
    p = sub.add_parser("fetch")
    p.add_argument("--name", required=True)
    p.add_argument("--out-dir")
    p.add_argument("--force", action="store_true")
    p.add_argument("--no-postfilter", action="store_true", help="disable the fragment/duplicate add guard on fetched edits")
    a = ap.parse_args()
    {"models": cmd_models, "export": cmd_export, "submit": cmd_submit, "run": cmd_run,
     "status": cmd_status, "wait": cmd_wait, "fetch": cmd_fetch}[a.cmd](a)


if __name__ == "__main__":
    main()
