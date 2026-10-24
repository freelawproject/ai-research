"""Step 3 — send request bodies to Clairic on Workers AI, with resume, concurrency and retries.

  run_clairic.py run --name d1 --requests data/requests/d1.jsonl --dry-run     # prints the first body, sends nothing
  run_clairic.py run --name d1 --requests data/requests/d1.jsonl --limit 2     # smoke
  run_clairic.py run --name d1 --requests data/requests/d1.jsonl [--workers 2]
  run_clairic.py status --name d1

Endpoint: POST {base}/accounts/$CF_ACCOUNT_ID/ai/run/@cf/cloudflare/clairic, Authorization: Bearer $CF_API_TOKEN
(token needs Workers AI Read + Edit; CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID are accepted too). run.sh loads
.env from this folder. Nothing is sent without both variables in the environment.
Outputs land in data/runs/{name}/outputs.jsonl, one line per request key:
  {"key", "answers", "truncated", "usage", "state_text" (only when it differs from the state sent),
   "request_id", "elapsed", "error"}
Re-running skips keys already answered. Price estimate = usage.input_tokens × $0.10/M (whether the
server counts every parallel pass in input_tokens is what the smoke measures).
"""
import argparse
import asyncio
import json
import os
import random
import sys
import time

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "data", "runs")
DEFAULT_BASE = "https://api.cloudflare.com/client/v4"
MODEL = "@cf/cloudflare/clairic"
RETRY_STATUS = {429, 500, 502, 503, 504, 529}


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def done_keys(out_path):
    if not os.path.exists(out_path):
        return set()
    return {r["key"] for r in read_jsonl(out_path) if not r.get("error")}


def unwrap(payload):
    """Workers AI REST wraps the model output as {"result": ..., "success": bool, "errors": [...]}."""
    if isinstance(payload, dict) and "result" in payload:
        if payload.get("success") is False:
            raise ValueError(f"success=false: {json.dumps(payload.get('errors'))[:300]}")
        return payload["result"]
    return payload


async def send(client, sem, rec, url, headers, retries, out_f, lock, stats):
    async with sem:
        t0 = time.time()
        err, result, req_id = None, None, None
        for attempt in range(retries + 1):
            try:
                r = await client.post(url, json=rec["body"], headers=headers, timeout=180)
                req_id = r.headers.get("cf-ray")
                if r.status_code == 200:
                    result = unwrap(r.json())
                    err = None
                    break
                err = f"HTTP {r.status_code}: {r.text[:300]}"
                if r.status_code not in RETRY_STATUS:
                    break
                wait = float(r.headers.get("retry-after") or 0) or min(60, 2 ** attempt + random.random())
            except (httpx.HTTPError, ValueError) as e:
                err = f"{type(e).__name__}: {e}"
                wait = min(60, 2 ** attempt + random.random())
            await asyncio.sleep(wait)
        out = {"key": rec["key"], "answers": None, "truncated": None, "usage": None, "state_text": None,
               "request_id": req_id, "elapsed": round(time.time() - t0, 3), "error": err}
        if result:
            out.update(answers=result.get("answers"), truncated=result.get("truncated"), usage=result.get("usage"))
            st = result.get("state_text")
            if st is not None and st != rec["body"]["state"]:
                out["state_text"] = st
        async with lock:
            out_f.write(json.dumps(out, ensure_ascii=False) + "\n")
            out_f.flush()
            stats["done"] += 1
            stats["errors"] += bool(err)
            stats["input_tokens"] += (out["usage"] or {}).get("input_tokens") or 0
            stats["truncated"] += bool(out["truncated"])
            if stats["done"] % 10 == 0 or stats["done"] == stats["total"]:
                print(f"  {stats['done']}/{stats['total']} done, {stats['errors']} errors, "
                      f"{stats['truncated']} truncated, {stats['input_tokens']:,} input tokens", flush=True)


async def run(a):
    recs = read_jsonl(a.requests)
    run_dir = os.path.join(RUNS, a.name)
    out_path = os.path.join(run_dir, "outputs.jsonl")
    skip = done_keys(out_path)
    todo = [r for r in recs if r["key"] not in skip]
    if a.limit:
        todo = todo[: a.limit]
    account = os.environ.get("CF_ACCOUNT_ID") or os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    url = f"{a.base_url.rstrip('/')}/accounts/{account or '{CF_ACCOUNT_ID}'}/ai/run/{MODEL}"
    print(f"{a.name}: {len(recs):,} requests, {len(skip):,} already done, {len(todo):,} to send → {url}")
    if a.dry_run:
        print(json.dumps(todo[0]["body"] if todo else {}, ensure_ascii=False, indent=1)[:3000])
        have_token = bool(os.environ.get(a.api_key_env) or os.environ.get("CLOUDFLARE_API_TOKEN"))
        print(f"\n--dry-run: nothing sent. credentials: token {'present' if have_token else 'MISSING'}, "
              f"account id {'present' if account else 'MISSING'}")
        return 0
    token = os.environ.get(a.api_key_env) or os.environ.get("CLOUDFLARE_API_TOKEN")
    if not token or not account:
        missing = [n for n, v in (("CF_API_TOKEN (or CLOUDFLARE_API_TOKEN)", token),
                                  ("CF_ACCOUNT_ID (or CLOUDFLARE_ACCOUNT_ID)", account)) if not v]
        print(f"missing in the environment: {', '.join(missing)}; nothing sent.", file=sys.stderr)
        return 2
    if not todo:
        print("nothing to do.")
        return 0
    os.makedirs(run_dir, exist_ok=True)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    meta = {"name": a.name, "requests": os.path.relpath(a.requests, HERE), "model": MODEL, "base_url": a.base_url,
            "prompt": recs[0].get("prompt") if recs else None, "n_requests": len(recs),
            "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(os.path.join(run_dir, "meta.json"), "w") as f:
        json.dump(meta, f, indent=1)
    stats = {"done": 0, "errors": 0, "input_tokens": 0, "truncated": 0, "total": len(todo)}
    sem, lock = asyncio.Semaphore(a.workers), asyncio.Lock()
    t0 = time.time()
    with open(out_path, "a", encoding="utf-8") as out_f:
        async with httpx.AsyncClient() as client:
            await asyncio.gather(*(send(client, sem, r, url, headers, a.retries, out_f, lock, stats) for r in todo))
    el = time.time() - t0
    print(f"done in {el / 60:.1f} min; {stats['errors']} errors; {stats['truncated']} truncated; "
          f"{stats['input_tokens']:,} input tokens (≈ ${stats['input_tokens'] * a.price_per_m / 1e6:.3f} at ${a.price_per_m}/M)")
    return 0


def status(a):
    out_path = os.path.join(RUNS, a.name, "outputs.jsonl")
    if not os.path.exists(out_path):
        print(f"{a.name}: no outputs yet")
        return 0
    rows = read_jsonl(out_path)
    ok = [r for r in rows if not r.get("error")]
    toks = sum((r.get("usage") or {}).get("input_tokens") or 0 for r in ok)
    passes = sorted((r.get("usage") or {}).get("passes") or 0 for r in ok)
    print(f"{a.name}: {len(rows):,} rows, {len(ok):,} ok, {len(rows) - len(ok):,} errors, "
          f"{sum(bool(r.get('truncated')) for r in ok)} truncated, {toks:,} input tokens, "
          f"passes max {passes[-1] if passes else 0}, state_text differs on {sum(r.get('state_text') is not None for r in ok)}")
    errs = {}
    for r in rows:
        if r.get("error"):
            errs[r["error"][:80]] = errs.get(r["error"][:80], 0) + 1
    for e, n in sorted(errs.items(), key=lambda x: -x[1])[:5]:
        print(f"  {n:4d}  {e}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    p.add_argument("--name", required=True)
    p.add_argument("--requests", required=True)
    p.add_argument("--base-url", default=DEFAULT_BASE)
    p.add_argument("--api-key-env", default="CF_API_TOKEN")
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--retries", type=int, default=4)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--price-per-m", type=float, default=0.10)
    p.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("status")
    s.add_argument("--name", required=True)
    a = ap.parse_args()
    return asyncio.run(run(a)) if a.cmd == "run" else status(a)


if __name__ == "__main__":
    sys.exit(main())
