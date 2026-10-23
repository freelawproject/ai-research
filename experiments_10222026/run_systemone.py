"""Generic /v1/systemone runner (TypeSafe schema) with resume, concurrency and retries.

  run_systemone.py run --name jev_mention --requests data/requests/jev-1.14.0_mention.jsonl \
      [--base-url https://api.typesafe.ai] [--api-key-env TYPESAFE_API_KEY] [--auth-scheme Bearer|Api-Key] \
      [--workers 4] [--limit N] [--dry-run] [--price-per-m 0.042]
  run_systemone.py status --name jev_mention

Outputs land in data/runs/{name}/outputs.jsonl, one line per request key:
  {"key", "model", "noul", "answers", "usage", "request_id", "elapsed", "error"}
Re-running skips keys already answered. Nothing is sent without an API key in the environment,
and --dry-run prints the first body instead of sending anything.
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
DEFAULT_BASE = "https://api.typesafe.ai"
RETRY_STATUS = {429, 500, 502, 503, 504, 529}


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def done_keys(out_path):
    if not os.path.exists(out_path):
        return set()
    return {r["key"] for r in read_jsonl(out_path) if not r.get("error")}


async def send(client, sem, rec, base_url, headers, retries, out_f, lock, stats):
    url = base_url.rstrip("/") + "/v1/systemone"
    async with sem:
        t0 = time.time()
        err, resp_json, status, req_id = None, None, None, None
        for attempt in range(retries + 1):
            try:
                r = await client.post(url, json=rec["body"], headers=headers, timeout=120)
                status, req_id = r.status_code, r.headers.get("x-typesafe-request-id")
                if status == 200:
                    resp_json = r.json()
                    err = None
                    break
                err = f"HTTP {status}: {r.text[:300]}"
                if status not in RETRY_STATUS:
                    break
                wait = float(r.headers.get("retry-after") or 0) or min(60, 2 ** attempt + random.random())
            except (httpx.HTTPError, ValueError) as e:
                err = f"{type(e).__name__}: {e}"
                wait = min(60, 2 ** attempt + random.random())
            await asyncio.sleep(wait)
        out = {"key": rec["key"], "model": None, "noul": None, "answers": None, "usage": None,
               "request_id": req_id, "elapsed": round(time.time() - t0, 3), "error": err}
        if resp_json:
            ans = resp_json.get("answers", {})
            out.update(model=resp_json.get("model"), answers=ans, usage=resp_json.get("usage"))
            first = next(iter(ans.values()), {}) if ans else {}
            out["noul"] = first.get("noul")
        async with lock:
            out_f.write(json.dumps(out, ensure_ascii=False) + "\n")
            out_f.flush()
            stats["done"] += 1
            stats["errors"] += bool(err)
            stats["input_tokens"] += (out["usage"] or {}).get("input_tokens") or 0
            if stats["done"] % 100 == 0 or stats["done"] == stats["total"]:
                print(f"  {stats['done']}/{stats['total']} done, {stats['errors']} errors, "
                      f"{stats['input_tokens']:,} input tokens", flush=True)


async def run(a):
    recs = read_jsonl(a.requests)
    run_dir = os.path.join(RUNS, a.name)
    out_path = os.path.join(run_dir, "outputs.jsonl")
    skip = done_keys(out_path)
    todo = [r for r in recs if r["key"] not in skip]
    if a.limit:
        todo = todo[: a.limit]
    meta = {"name": a.name, "requests": os.path.relpath(a.requests, HERE), "base_url": a.base_url,
            "model": recs[0]["body"]["model"] if recs else None, "prompt": recs[0].get("prompt") if recs else None,
            "n_requests": len(recs), "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    print(f"{a.name}: {len(recs):,} requests, {len(skip):,} already done, {len(todo):,} to send → {a.base_url}")
    if a.dry_run:
        print(json.dumps(todo[0]["body"] if todo else {}, ensure_ascii=False, indent=1)[:3000])
        print("\n--dry-run: nothing sent.")
        return 0
    key = os.environ.get(a.api_key_env)
    if not key:
        print(f"no API key in ${a.api_key_env}; nothing sent.", file=sys.stderr)
        return 2
    if not todo:
        print("nothing to do.")
        return 0
    os.makedirs(run_dir, exist_ok=True)
    headers = {"Authorization": f"{a.auth_scheme} {key}", "Content-Type": "application/json"}
    with open(os.path.join(run_dir, "meta.json"), "w") as f:
        json.dump(meta, f, indent=1)
    stats = {"done": 0, "errors": 0, "input_tokens": 0, "total": len(todo)}
    sem, lock = asyncio.Semaphore(a.workers), asyncio.Lock()
    t0 = time.time()
    with open(out_path, "a", encoding="utf-8") as out_f:
        async with httpx.AsyncClient() as client:
            await asyncio.gather(*(send(client, sem, r, a.base_url, headers, a.retries, out_f, lock, stats) for r in todo))
    el = time.time() - t0
    print(f"done in {el / 60:.1f} min; {stats['errors']} errors; {stats['input_tokens']:,} input tokens "
          f"(≈ ${stats['input_tokens'] * a.price_per_m / 1e6:.3f} at ${a.price_per_m}/M)")
    return 0


def status(a):
    out_path = os.path.join(RUNS, a.name, "outputs.jsonl")
    if not os.path.exists(out_path):
        print(f"{a.name}: no outputs yet")
        return 0
    rows = read_jsonl(out_path)
    ok = [r for r in rows if not r.get("error")]
    toks = sum((r.get("usage") or {}).get("input_tokens") or 0 for r in ok)
    print(f"{a.name}: {len(rows):,} rows, {len(ok):,} ok, {len(rows) - len(ok):,} errors, {toks:,} input tokens, "
          f"models {sorted({r.get('model') for r in ok})}")
    errs = {}
    for r in rows:
        if r.get("error"):
            errs[r["error"][:60]] = errs.get(r["error"][:60], 0) + 1
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
    p.add_argument("--api-key-env", default="TYPESAFE_API_KEY")
    p.add_argument("--auth-scheme", default="Bearer", help="Authorization header scheme: Bearer (TypeSafe, OpenRouter, Liquid) or Api-Key (Baseten)")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--retries", type=int, default=4)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--price-per-m", type=float, default=0.042)
    p.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("status")
    s.add_argument("--name", required=True)
    a = ap.parse_args()
    return asyncio.run(run(a)) if a.cmd == "run" else status(a)


if __name__ == "__main__":
    sys.exit(main())
