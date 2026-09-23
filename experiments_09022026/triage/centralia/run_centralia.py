"""Re-extract flagged opinions from their court PDFs with centralia.

Input: every cluster in the annotator root (--all, the normal mode), or the
clusters flagged "needs review" in the viewer (default), or explicit --ids.
After each cluster the result is written back into the annotator's
grouping_overrides/{cid}.json as `centralia: {status, pdf_source, n_opinions,
n_warnings, warnings, ran_at, error}` and `needs_centralia` is SET
AUTOMATICALLY to True when centralia did not return a clean reading
(status != "valid", or no PDF) — so the viewer's Centralia filter shows the
opinions that need a human look, nobody flags them by hand.
For each cluster:
  1. CourtListener API (token from citator-benchmark/.env): the cluster's
     sub-opinions with local_path / download_url, plus filepath_pdf_harvard.
  2. Download the PDF — storage.courtlistener.com/{local_path} first, then
     the Harvard scan (harvard_pdf/{cid}.pdf), then the court's download_url.
  3. centralia.read(pdf, court_id=<CL court id>, allow_pending=True)
  4. Write data/centralia/out/{cid}.json (the full read() dict),
     {cid}.review.html (centralia's reviewer page), and append a row to
     data/centralia/runs.csv (status, pdf source, opinions found, warnings).

Runs inside centralia's own environment (stdlib + centralia only):

    cd <workspace>/centralia && uv run python \\
        <workspace>/ai-research/experiments_09022026/triage/centralia/run_centralia.py \\
        --all [--root ../ai-research/experiments_09022026/triage/data/annotator] [--ids 123 456] [--force] [--no-flags]

Feeding centralia's html back into the annotator (re-tagging citations with
eyecite so the Grouping page has spans) is the next step, not done here.
"""
import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from centralia import CourtNotReleased, UnknownCourt, read, released_courts

HERE = Path(__file__).resolve().parent.parent
FLP = Path(__file__).resolve().parents[4]      # the workspace holding both checkouts
# the citator-benchmark checkout, a sibling of the ai-research checkout;
# CITATOR_BENCH overrides
BENCH_ENV = Path(os.environ.get("CITATOR_BENCH", FLP / "citator-benchmark")) / ".env"
API = "https://www.courtlistener.com/api/rest/v4"
STORAGE = "https://storage.courtlistener.com/"
UA = "flp-citator-triage/centralia-runner"


def cl_token():
    if BENCH_ENV.exists():
        for line in BENCH_ENV.read_text().splitlines():
            if line.startswith("COURTLISTENER_API_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"')
    return os.environ.get("COURTLISTENER_API_TOKEN", "")


def get_json(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Token {token}", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        ctype = r.headers.get("Content-Type", "")
        data = r.read()
        if b"%PDF" not in data[:1024]:
            raise ValueError(f"not a PDF ({ctype})")
        f.write(data)
    return dest


def flagged_ids(root):
    ids = []
    for p in sorted((root / "data" / "grouping_overrides").glob("*.json"), key=lambda p: int(p.stem)):
        if json.load(open(p)).get("needs_centralia"):
            ids.append(p.stem)
    return ids


def court_of(root):
    return {r["cluster_id"]: r for r in csv.DictReader(open(root / "overrides" / "citing_metadata.csv"))}


def write_flag(root, cid, row, warnings):
    """Merge the centralia result into the cluster's grouping override and
    set needs_centralia from it. Atomic replace so a concurrent viewer save
    never sees a half-written file."""
    d = root / "data" / "grouping_overrides"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{cid}.json"
    ov = json.load(open(path)) if path.exists() else {}
    prev = ov.get("centralia") or {}
    ov["centralia"] = {**prev, "status": row["status"], "pdf_source": row["pdf_source"],
                       "n_opinions": row["n_opinions"], "n_warnings": row["n_warnings"],
                       "warnings": warnings[:5], "error": row["error"], "ran_at": row["ran_at"]}
    ov["needs_centralia"] = row["status"] != "valid"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ov, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def pdf_candidates(cid, cluster, opinions):
    seen, out = set(), []
    for o in sorted(opinions, key=lambda o: o.get("type", "")):   # combined/lead first
        lp = (o.get("local_path") or "").strip()
        if lp and lp not in seen:
            seen.add(lp)
            out.append(("storage", STORAGE + lp, o["id"]))
    if cluster.get("filepath_pdf_harvard"):
        out.append(("harvard", STORAGE + cluster["filepath_pdf_harvard"], None))
    for o in opinions:
        du = (o.get("download_url") or "").strip()
        if du.lower().endswith(".pdf") and du not in seen:
            seen.add(du)
            out.append(("court", du, o["id"]))
    return out


def sync_flags(root, out):
    """Repair path: the viewer used to drop unknown override keys on save, so
    a cluster opened in the annotator could lose its centralia record.
    Rebuild all of them from the run log (last row per cluster wins) and
    re-apply fed_back from feed_centralia's log."""
    rows = {}
    for r in csv.DictReader(open(out / "runs.csv", encoding="utf-8")):
        rows[r["cluster_id"]] = r
    fed = {}
    fb = out / "fed_back.csv"
    if fb.exists():
        for r in csv.DictReader(open(fb, encoding="utf-8")):
            fed[r["cluster_id"]] = r["fed_at"]
    for cid, r in rows.items():
        write_flag(root, cid, r, [])
        if cid in fed:
            path = root / "data" / "grouping_overrides" / f"{cid}.json"
            ov = json.load(open(path))
            ov["centralia"]["fed_back"] = True
            ov["centralia"]["fed_at"] = fed[cid]
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(ov, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, path)
    print(f"synced centralia records for {len(rows)} clusters ({len(fed)} marked fed_back)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(HERE / "data" / "annotator"))
    ap.add_argument("--ids", nargs="*", default=None, help="cluster ids (default: flagged in --root)")
    ap.add_argument("--all", action="store_true", help="every cluster in the root's citing_metadata.csv")
    ap.add_argument("--no-flags", action="store_true", help="do not write results into grouping_overrides")
    ap.add_argument("--sleep", type=float, default=0.25, help="pause between CourtListener API calls")
    ap.add_argument("--out", default=str(HERE / "data" / "centralia"))
    ap.add_argument("--force", action="store_true", help="re-run clusters that already have output")
    ap.add_argument("--sync-flags", action="store_true",
                    help="rewrite every override's centralia record from runs.csv (+ fed_back.csv); no fetching")
    a = ap.parse_args()
    if a.sync_flags:
        sync_flags(Path(a.root), Path(a.out))
        return
    root, out = Path(a.root), Path(a.out)
    (out / "pdf").mkdir(parents=True, exist_ok=True)
    (out / "out").mkdir(exist_ok=True)
    token = cl_token()
    if not token:
        sys.exit("no CourtListener token (citator-benchmark/.env or COURTLISTENER_API_TOKEN)")
    meta = court_of(root)
    ids = a.ids or (sorted(meta, key=int) if a.all else flagged_ids(root))
    print(f"{len(ids)} clusters to run; {len(released_courts())} courts released in centralia")
    log_path = out / "runs.csv"
    new_log = not log_path.exists()
    log = open(log_path, "a", newline="", encoding="utf-8")
    w = csv.DictWriter(log, fieldnames=["cluster_id", "court", "released", "pdf_source", "pdf_url",
                                        "status", "n_opinions", "n_warnings", "n_removed", "error", "ran_at"])
    if new_log:
        w.writeheader()

    for cid in ids:
        dest_json = out / "out" / f"{cid}.json"
        if dest_json.exists() and not a.force:
            print(f"[{cid}] done already (use --force to redo)")
            continue
        warnings = []
        court = meta.get(cid, {}).get("court", "")
        row = {"cluster_id": cid, "court": court, "released": court in released_courts(),
               "pdf_source": "", "pdf_url": "", "status": "", "n_opinions": "", "n_warnings": "",
               "n_removed": "", "error": "", "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        try:
            cluster = get_json(f"{API}/clusters/{cid}/?fields=id,case_name,filepath_pdf_harvard,sub_opinions", token)
            opinions = []
            for url in cluster.get("sub_opinions", []):
                opinions.append(get_json(url.rstrip("/") + "/?fields=id,type,local_path,download_url", token))
                time.sleep(a.sleep)
            pdf = None
            for source, url, oid in pdf_candidates(cid, cluster, opinions):
                try:
                    pdf = download(url, out / "pdf" / f"{cid}.pdf")
                    row["pdf_source"], row["pdf_url"] = source, url
                    break
                except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError) as e:
                    print(f"[{cid}]   {source} pdf failed: {e}")
            if pdf is None:
                raise FileNotFoundError("no PDF reachable for this cluster")
            try:
                r = read(str(pdf), court_id=court, allow_pending=True)
            except (CourtNotReleased, UnknownCourt):
                raise
            except Exception as e:  # a centralia-internal error on one PDF must not stop the batch
                raise RuntimeError(f"centralia crashed: {type(e).__name__}: {e}") from e
            warnings = list(r.get("warnings", []))
            row.update(status=r.get("status", ""), n_opinions=len(r.get("opinions", [])),
                       n_warnings=len(warnings), n_removed=len(r.get("removed", [])))
            payload = {k: v for k, v in r.items() if k != "review_html"}
            payload["_source"] = {"cluster_id": cid, "court": court, "pdf_source": row["pdf_source"],
                                  "pdf_url": row["pdf_url"], "case_name": cluster.get("case_name", "")}
            dest_json.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
            if r.get("review_html"):
                (out / "out" / f"{cid}.review.html").write_text(r["review_html"], encoding="utf-8")
            print(f"[{cid}] {court} {row['pdf_source']} -> status={row['status']} opinions={row['n_opinions']} "
                  f"warnings={row['n_warnings']} removed={row['n_removed']}")
        except (CourtNotReleased, UnknownCourt, FileNotFoundError, urllib.error.URLError,
                urllib.error.HTTPError, ValueError, KeyError, RuntimeError, TimeoutError) as e:
            row["status"], row["error"] = "failed", f"{type(e).__name__}: {e}"
            print(f"[{cid}] FAILED {row['error']}")
        w.writerow(row)
        log.flush()
        if not a.no_flags:
            write_flag(root, cid, row, warnings)
    log.close()
    print(f"log: {log_path}")


if __name__ == "__main__":
    main()
