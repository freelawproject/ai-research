"""Phase 1 batch runner: cl_fetch JSON → tagged artifact JSON.

Loops over data/cl_fetch/*.json, runs assemble_cluster, writes
data/tagged/{cluster_id}.json. Runs a self-check on each output (every id's
offset points at its <cited> opening tag, citation_string follows verbatim, ids
sequential) and writes all findings to disk for investigation:

    data/assemble_report.json   per-cluster summary + self-check problems +
                                unmatched-miss details (full, machine-readable)
    data/assemble_issues.log    human-readable; only clusters with self-check
                                problems or unmatched misses

Runs locally in the ai-research env (BS4 only — no DB). Run fetch_cl_data.py in
the courtlistener container first to populate data/cl_fetch/.

Usage:  python run_assemble.py
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "citator-pipeline")
)

from utils.assemble_tagged_text import assemble_cluster  # noqa: E402

DATA_DIR = Path(__file__).parent / "data"
FETCH_DIR = DATA_DIR / "cl_fetch"
TAGGED_DIR = DATA_DIR / "tagged"
REPORT_PATH = DATA_DIR / "assemble_report.json"
ISSUES_LOG_PATH = DATA_DIR / "assemble_issues.log"

# How much surrounding text to capture when an offset check fails.
_CTX = 60


def self_check(artifact: dict) -> list[dict]:
    """Verify offsets/citation_strings/id-sequence. Returns structured problems
    (each a dict with enough context to investigate from the report alone)."""
    problems: list[dict] = []
    # Key by source_opinion_id — opinion_type is NOT unique within a cluster
    # (multiple concurrences/dissents share a type).
    tagged_by_opinion = {
        o["source_opinion_id"]: o["tagged_text"] for o in artifact["opinions"]
    }
    for cid, meta in artifact["id_to_metadata_map"].items():
        text = tagged_by_opinion.get(meta["source_opinion_id"], "")
        off = meta["offset"]
        open_tag = f'<cited id="{cid}" group="{meta["group_id"]}">'
        actual_at_off = text[off : off + len(open_tag)]
        if actual_at_off != open_tag:
            problems.append(
                {
                    "type": "offset_not_at_tag",
                    "cited_id": cid,
                    "opinion_type": meta["opinion_type"],
                    "source_opinion_id": meta["source_opinion_id"],
                    "offset": off,
                    "citation_string": meta["citation_string"],
                    "expected": open_tag,
                    "actual": text[off : off + len(open_tag) + _CTX],
                }
            )
            continue
        after = off + len(open_tag)
        cstr = meta["citation_string"]
        if text[after : after + len(cstr)] != cstr:
            problems.append(
                {
                    "type": "citation_string_not_after_tag",
                    "cited_id": cid,
                    "opinion_type": meta["opinion_type"],
                    "source_opinion_id": meta["source_opinion_id"],
                    "offset": off,
                    "expected": cstr[:_CTX],
                    "actual": text[after : after + len(cstr) + _CTX],
                }
            )
    ids = sorted(int(k) for k in artifact["id_to_metadata_map"])
    if ids != list(range(artifact["n_tags"])):
        problems.append(
            {
                "type": "ids_not_sequential",
                "expected": f"0..{artifact['n_tags'] - 1}",
                "actual_count": len(ids),
                "actual_min_max": [ids[0], ids[-1]] if ids else [],
            }
        )
    return problems


def main() -> None:
    TAGGED_DIR.mkdir(parents=True, exist_ok=True)
    fetch_files = sorted(FETCH_DIR.glob("*.json"))
    if not fetch_files:
        print(f"No cl_fetch JSON files in {FETCH_DIR}. Run fetch_cl_data.py first.")
        sys.exit(1)

    report: dict[str, dict] = {}
    total_tags = 0
    total_misses = 0
    n_clean = 0
    n_problem = 0

    for fp in fetch_files:
        cl_fetch = json.loads(fp.read_text())
        artifact = assemble_cluster(cl_fetch)
        cid = str(artifact["cluster_id"])

        out_path = TAGGED_DIR / f"{cid}.json"
        out_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2))

        problems = self_check(artifact)
        report[cid] = {
            "n_tags": artifact["n_tags"],
            "n_opinions": len(artifact["opinions"]),
            "n_unmatched_misses": artifact["n_unmatched_misses"],
            "empty_html": artifact["empty_html"],
            "problems": problems,
            "unmatched_miss_details": artifact.get("unmatched_miss_details", []),
        }

        total_tags += artifact["n_tags"]
        total_misses += artifact["n_unmatched_misses"]
        has_problem = bool(problems)
        n_clean += not has_problem
        n_problem += has_problem
        status = "OK" if not has_problem else f"PROBLEMS ({len(problems)})"
        print(
            f"  {cid}: {artifact['n_tags']} tags, "
            f"{len(artifact['opinions'])} opinions, "
            f"{artifact['n_unmatched_misses']} misses, "
            f"empty_html={artifact['empty_html']} — {status}"
        )

    # ── Write machine-readable report ───────────────────────────────────
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    # ── Write human-readable issues log (problems + misses only) ────────
    lines: list[str] = []
    for cid, r in sorted(report.items(), key=lambda kv: int(kv[0])):
        if not r["problems"] and not r["unmatched_miss_details"]:
            continue
        lines.append(f"=== cluster {cid} ===")
        lines.append(
            f"  n_tags={r['n_tags']} n_opinions={r['n_opinions']} "
            f"n_misses={r['n_unmatched_misses']} empty_html={r['empty_html']}"
        )
        for p in r["problems"]:
            lines.append(f"  [PROBLEM] {p['type']}")
            for k, v in p.items():
                if k == "type":
                    continue
                lines.append(f"      {k}: {v!r}")
        for m in r["unmatched_miss_details"]:
            lines.append(
                f"  [MISS] opinion={m.get('opinion_type')} "
                f"(op_id={m.get('citing_opinion_id')}) "
                f"cite={m.get('citation_string')!r}"
            )
        lines.append("")
    ISSUES_LOG_PATH.write_text("\n".join(lines))

    # ── Summary ─────────────────────────────────────────────────────────
    print()
    print("=== Phase 1 assemble summary ===")
    print(f"Clusters processed:    {len(fetch_files):>6,}")
    print(f"Clean / with problems: {n_clean:>4,} / {n_problem}")
    print(f"Total tags:            {total_tags:>6,}")
    print(f"Total unmatched miss:  {total_misses:>6,}")
    print(f"Report:    {REPORT_PATH}")
    print(f"Issues log:{ISSUES_LOG_PATH}")
    print(f"Tagged:    {TAGGED_DIR}")


if __name__ == "__main__":
    main()
