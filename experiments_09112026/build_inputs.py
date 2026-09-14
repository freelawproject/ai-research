"""Opinion inputs for the two-stage experiment.

  bash run.sh build_inputs.py fetch [--split all]   # CL html for every dev/test cluster -> data/opinions/{cid}.json
  bash run.sh build_inputs.py centralia             # valid 0903 centralia readings -> eyecite-seeded payloads + coverage guard
  bash run.sh build_inputs.py build [--split all]   # -> data/inputs/{cid}.tagged.txt + {cid}.inventory.json
  bash run.sh build_inputs.py show --cid 1722 [--stage label --flag 3,7]   # print a stage input

`fetch` copies the benchmark viewer's cached CourtListener payload when one exists
(citator-benchmark, then the triage annotator when its copy is the CourtListener
original) and otherwise calls the CourtListener API with the token from
citator-benchmark/.env. `build` converts each payload with the triage
`tagged_text.from_opinion_html`: every eyecite span becomes
`<citedCase group="N">…</citedCase>` where N is shared by all spans linked to the
same CourtListener cluster (un-linked spans get their own N), and the inventory
lists one entry per N with the cited cluster id, name and citation strings.
"""
import argparse
import csv
import html as htmlmod
import json
import os
import re
import sys
import time

import requests

from common import (CL_ENV, DATA, INPUTS, OPINIONS, OPINION_CACHES, TRIAGE, cluster_ids, load_citing_meta,
                    normalize_citation, read_json, read_text, write_json)
from feed_centralia import CL_TYPE, tag_opinions  # experiments_09022026/triage; needs eyecite (run.sh supplies it)
from tagged_text import (_MENTION, _clean_prose, from_opinion_html, from_revised_html,  # experiments_09022026/triage
                         inventory_block)

OPINIONS_CENTRALIA = os.path.join(DATA, "opinions_centralia")     # {cid}.json centralia+eyecite payloads
CENTRALIA_RUNS = os.path.join(TRIAGE, "data", "centralia", "runs.csv")
CENTRALIA_OUT = os.path.join(TRIAGE, "data", "centralia", "out")
CENTRALIA_FEED = os.path.join(DATA, "centralia_feed.json")
COVERAGE_MIN = 0.85   # centralia plain text must be at least this share of the CL plain text
# corrected citation grouping (revised html): the 283 GPT-fixed clusters in this experiment's
# annotator root, and the 99 verified-gold clusters in the triage annotator root
REVISED_DIRS = [
    (os.path.join(DATA, "annotator_bench", "data", "revised_html"), "revised:gpt"),
    (os.path.join(TRIAGE, "data", "annotator", "data", "revised_html"), "revised:gold"),
]

CL_API = "https://www.courtlistener.com/api/rest/v4"
OPINION_TYPE_ORDER = {"020lead": 0, "025plurality": 1, "030concurrence": 2,
                      "035concurrenceinpart": 3, "040dissent": 4, "010combined": 5, "015unamimous": 6}


# ── fetch ──
def cl_token():
    tok = os.environ.get("COURTLISTENER_API_TOKEN", "")
    if not tok and os.path.exists(CL_ENV):
        for line in read_text(CL_ENV).splitlines():
            if line.startswith("COURTLISTENER_API_TOKEN="):
                tok = line.split("=", 1)[1].strip().strip('"').strip("'")
    return tok


def fetch_courtlistener(cid, token):
    """Same payload shape as the benchmark viewer's cache: {cluster_id, opinions:[{id,type,source,html}], origin, fetched}."""
    r = requests.get(f"{CL_API}/opinions/",
                     params={"cluster__id": cid, "fields": "id,type,html_with_citations,html,plain_text"},
                     headers={"Authorization": f"Token {token}"}, timeout=60)
    r.raise_for_status()
    results = sorted(r.json().get("results", []),
                     key=lambda op: (OPINION_TYPE_ORDER.get(op.get("type", ""), 100), op.get("type", "")))
    opinions = []
    for op in results:
        html = op.get("html_with_citations") or op.get("html") or ""
        source = ("html_with_citations" if op.get("html_with_citations") else "html" if op.get("html")
                  else "plain_text")
        if not html:
            html = "<pre class='plain'>" + (op.get("plain_text") or "") + "</pre>"
        opinions.append({"id": op["id"], "type": op.get("type", ""), "source": source, "html": html})
    return {"cluster_id": int(cid), "opinions": opinions, "origin": "courtlistener",
            "fetched": time.strftime("%Y-%m-%d")}


def cached_payload(cid):
    """A CourtListener-original payload from one of the viewer caches, or None."""
    for i, cache in enumerate(OPINION_CACHES):
        p = os.path.join(cache, f"{cid}.json")
        if not os.path.exists(p):
            continue
        payload = read_json(p)
        # the triage annotator cache may hold centralia-fed text (eyecite seeds, no CL links)
        if i > 0 and payload.get("origin") not in (None, "courtlistener"):
            continue
        if not any(op.get("source", "html_with_citations") == "html_with_citations" for op in payload["opinions"]):
            continue
        return payload
    return None


def cmd_fetch(a):
    os.makedirs(OPINIONS, exist_ok=True)
    cids = a.ids or cluster_ids(a.split)
    token = cl_token()
    n_have = n_cache = n_fetch = 0
    failed = []
    for cid in cids:
        dst = os.path.join(OPINIONS, f"{cid}.json")
        if os.path.exists(dst) and not a.refetch:
            n_have += 1
            continue
        payload = None if a.refetch else cached_payload(cid)
        if payload is not None:
            write_json(dst, payload)
            n_cache += 1
            continue
        if not token:
            failed.append((cid, "no COURTLISTENER_API_TOKEN"))
            continue
        try:
            write_json(dst, fetch_courtlistener(cid, token))
            n_fetch += 1
            time.sleep(a.sleep)
        except requests.RequestException as e:
            failed.append((cid, str(e)[:120]))
    print(f"{len(cids)} clusters: {n_have} already present, {n_cache} copied from caches, {n_fetch} fetched, "
          f"{len(failed)} failed")
    for cid, why in failed:
        print("  FAILED", cid, why)


# ── centralia ──
def _plain_len(payload):
    ops = payload["opinions"]
    seps = [o for o in ops if not str(o.get("type", "")).startswith("010")]
    return sum(len(_clean_prose(o.get("html", ""))) for o in (seps or ops)), len(seps or ops)


def cmd_centralia(a):
    """For clusters whose 0903 centralia reading is `valid`, build an eyecite-seeded payload
    (the triage feeder's tag_opinions) and record whether it covers the CL text well enough
    to replace it. Reads experiments_09022026/triage/data/centralia/{runs.csv,out/}."""
    os.makedirs(OPINIONS_CENTRALIA, exist_ok=True)
    cids = set(a.ids or cluster_ids(a.split))
    last = {}
    with open(CENTRALIA_RUNS, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            last[r["cluster_id"]] = r
    feed = read_json(CENTRALIA_FEED, {})
    counts = {"valid": 0, "used": 0, "short": 0, "fewer_writings": 0, "no_reading": 0}
    for cid in sorted(cids, key=int):
        r = last.get(cid)
        if not r or r["status"] != "valid":
            continue
        counts["valid"] += 1
        d = read_json(os.path.join(CENTRALIA_OUT, f"{cid}.json"))
        if not d or not d.get("opinions"):
            counts["no_reading"] += 1
            continue
        writings = sorted(d["opinions"], key=lambda o: o.get("order", 0))
        tagged, n_spans, n_res = tag_opinions([o.get("html", "") for o in writings])
        opinions = [{"id": int(f"{cid}{k + 1:02d}"), "type": CL_TYPE.get(o.get("type"), "020lead"),
                     "source": "centralia", "centralia_type": o.get("type"), "author": o.get("author", ""),
                     "html": h} for k, (o, h) in enumerate(zip(writings, tagged))]
        payload = {"cluster_id": int(cid), "opinions": opinions, "origin": "centralia+eyecite",
                   "centralia_versions": d.get("versions"), "fetched": time.strftime("%Y-%m-%d")}
        write_json(os.path.join(OPINIONS_CENTRALIA, f"{cid}.json"), payload)
        cl = read_json(os.path.join(OPINIONS, f"{cid}.json"))
        cl_len, cl_n = _plain_len(cl) if cl else (0, 0)
        c_len, c_n = _plain_len(payload)
        ratio = c_len / cl_len if cl_len else 0.0
        used = ratio >= COVERAGE_MIN and c_n >= cl_n
        if not used:
            counts["short" if ratio < COVERAGE_MIN else "fewer_writings"] += 1
        counts["used"] += used
        feed[cid] = {"used": used, "coverage": round(ratio, 3), "centralia_chars": c_len, "cl_chars": cl_len,
                     "centralia_writings": c_n, "cl_writings": cl_n, "eyecite_spans": n_spans, "resources": n_res,
                     "pdf_source": r.get("pdf_source", "")}
    write_json(CENTRALIA_FEED, feed)
    print(f"centralia: {counts['valid']} valid readings; {counts['used']} pass the coverage guard "
          f"(>= {COVERAGE_MIN:.0%} of CL text, no fewer writings); {counts['short']} too short, "
          f"{counts['fewer_writings']} fewer writings, {counts['no_reading']} without a reading -> {CENTRALIA_FEED}")


def revised_for(cid):
    for d, label in REVISED_DIRS:
        p = os.path.join(d, f"{cid}.html")
        if os.path.exists(p) and os.path.getsize(p) > 0:
            return read_text(p), label
    return None, None


def fill_names_from_cl(inv, cl_inv):
    by_cid = {str(e["cited_cluster_id"]): e for e in cl_inv if e.get("cited_cluster_id") is not None}
    for e in inv:
        if not e.get("name") and e.get("cited_cluster_id") is not None:
            e["name"] = by_cid.get(str(e["cited_cluster_id"]), {}).get("name", "")


def names_from_overrides(cid, inv, label):
    """Groups the corrected export could not tie to a CL cluster carry cited_ref 'grp:{cid}:{gid}';
    their names live in the annotator root's grouping_overrides[group_names]."""
    root = os.path.join(DATA, "annotator_bench") if label == "revised:gpt" else os.path.join(TRIAGE, "data", "annotator")
    ov = read_json(os.path.join(root, "data", "grouping_overrides", f"{cid}.json"), {}) or {}
    names = ov.get("group_names") or {}
    for e in inv:
        ref = str(e.get("cited_ref") or "")
        if not e.get("name") and ref.startswith("grp:"):
            e["name"] = names.get(ref.rsplit(":", 1)[-1], "")


def link_to_cl(inv, cl_inv):
    """Give centralia's eyecite-seeded groups the CL cluster id (and name) of the CL-html group
    whose normalized full citation they share; unique matches only."""
    by_norm = {}
    for e in cl_inv:
        if e.get("cited_cluster_id") is None:
            continue
        for c in e.get("citations", []):
            n = normalize_citation(c)
            if n:
                by_norm.setdefault(n, set()).add(e["id"])
    cl_by_id = {e["id"]: e for e in cl_inv}
    n_linked = 0
    for e in inv:
        cands = set()
        for c in e.get("citations", []):
            n = normalize_citation(c)
            if n:
                cands |= by_norm.get(n, set())
        if len(cands) == 1:
            src = cl_by_id[next(iter(cands))]
            e["cited_cluster_id"] = src["cited_cluster_id"]
            e["cited_ref"] = str(src["cited_cluster_id"])
            e["name"] = e.get("name") or src.get("name", "")
            n_linked += 1
    return n_linked


# ── build ──
# An unlinked, single-mention citation span that directly follows another span with
# nothing but ", " (or "; ") between them is a parallel reporter cite of the same case
# (U.S. / S. Ct. / L. Ed. triads): fold it into the preceding group. Anything with words
# in between ("cert. denied, ", "aff'd, ", "rev'd, ") stays its own authority, as in the
# benchmark convention.
_ADJACENT = re.compile(r'(?<=</citedCase>)([,;]?\s{0,2})<citedCase group="(\d+)">((?=\d)[^<]*)</citedCase>')
_STAR_PAGE = re.compile(r"\*\d{1,4}(?=[A-Za-z(“\"'])")   # "*396which" -> "which"
_OPEN = re.compile(r'<citedCase group="(\d+)">')


def merge_parallel_citations(tagged, inv):
    """Relabel unlinked singleton spans glued to the previous span as that span's group.
    Returns (tagged, inv, n_merged) with mention counts and citation lists carried over."""
    by_id = {e["id"]: e for e in inv}
    singles = {e["id"] for e in inv if e.get("cited_cluster_id") is None and e["n_mentions"] == 1}
    merged = {}

    def rep(m):
        sep, gid, text = m.group(1), int(m.group(2)), m.group(3)
        opens = _OPEN.findall(tagged[: m.start()])
        if gid not in singles or not opens:
            return m.group(0)
        prev = int(opens[-1])
        target = merged.get(prev, prev)
        if target == gid:
            return m.group(0)
        merged[gid] = target
        return f'{sep}<citedCase group="{target}">{text}</citedCase>'

    out = _ADJACENT.sub(rep, tagged)        # lookbehind keeps the closing tag, so chains resolve in one pass
    for gid, target in merged.items():
        src, dst = by_id[gid], by_id[target]
        dst["n_mentions"] += src["n_mentions"]
        for c in src["citations"]:
            if c not in dst["citations"] and len(dst["citations"]) < 6:
                dst["citations"].append(c)
    inv = [e for e in inv if e["id"] not in merged]
    return out, inv, len(merged)


def cmd_build(a):
    os.makedirs(INPUTS, exist_ok=True)
    cids = a.ids or cluster_ids(a.split)
    feed = {} if a.text_source == "cl" else read_json(CENTRALIA_FEED, {})
    summary = {}
    for cid in cids:
        payload = read_json(os.path.join(OPINIONS, f"{cid}.json"))
        if payload is None:
            summary[cid] = {"error": "no payload"}
            continue
        cl_tagged, cl_inv = from_opinion_html(payload)
        source, n_linked_c = "cl", 0
        rev, rev_label = revised_for(cid) if a.grouping == "revised" else (None, None)
        if rev is not None:
            tagged, inv = from_revised_html(rev)
            n_linked_c = link_to_cl([e for e in inv if e.get("cited_cluster_id") is None], cl_inv)
            fill_names_from_cl(inv, cl_inv)
            names_from_overrides(cid, inv, rev_label)
            source = rev_label
        elif feed.get(cid, {}).get("used"):
            c_payload = read_json(os.path.join(OPINIONS_CENTRALIA, f"{cid}.json"))
            tagged, inv = from_opinion_html(c_payload)
            n_linked_c = link_to_cl(inv, cl_inv)
            source = "centralia"
        else:
            tagged, inv = cl_tagged, cl_inv
        tagged = _STAR_PAGE.sub("", tagged)
        n_merged = 0
        if not source.startswith("revised"):          # corrected groupings already hold their parallels
            tagged, inv, n_merged = merge_parallel_citations(tagged, inv)
        for e in inv:
            e["source"] = source
            e["name"] = re.sub(r"\s+", " ", htmlmod.unescape(e.get("name") or "")).strip()
            e["citations"] = [re.sub(r"\s+", " ", htmlmod.unescape(c)).strip() for c in e.get("citations", [])]
        with open(os.path.join(INPUTS, f"{cid}.tagged.txt"), "w", encoding="utf-8") as f:
            f.write(tagged)
        write_json(os.path.join(INPUTS, f"{cid}.inventory.json"), inv)
        summary[cid] = {
            "chars": len(tagged), "groups": len(inv),
            "linked": sum(1 for e in inv if e.get("cited_cluster_id") is not None),
            "mentions": sum(e["n_mentions"] for e in inv), "parallel_merged": n_merged,
            "text_source": source, "centralia_groups_linked_to_cl": n_linked_c,
            "sources": sorted({op.get("source", "") for op in payload["opinions"]}),
        }
    write_json(os.path.join(INPUTS, "summary.json"), summary)
    ok = [s for s in summary.values() if "error" not in s]
    print(f"built {len(ok)}/{len(cids)}; groups {sum(s['groups'] for s in ok):,} "
          f"(linked {sum(s['linked'] for s in ok):,}), mentions {sum(s['mentions'] for s in ok):,}, "
          f"chars {sum(s['chars'] for s in ok):,}; parallel cites merged {sum(s['parallel_merged'] for s in ok):,}; "
          f"text source: " + ", ".join(f"{k} {v}" for k, v in sorted(__import__('collections').Counter(s['text_source'] for s in ok).items())) + "; "
          f"no-citation-html: {sum(1 for s in ok if not s['groups'])}")


# ── stage inputs (used by run_stage.py) ──
_META = None


def citing_header(cid):
    global _META
    if _META is None:
        _META = load_citing_meta()
    m = _META.get(str(cid), {})
    attrs = " ".join(f'{k}="{str(m[k]).replace(chr(34), chr(39))}"' for k in ("name", "court", "year") if m.get(k))
    return f"<citingCase {attrs} />\n" if attrs else ""


def inventory(cid):
    return read_json(os.path.join(INPUTS, f"{cid}.inventory.json"), [])


def tagged(cid):
    return read_text(os.path.join(INPUTS, f"{cid}.tagged.txt"))


# contrast / treatment language that, near a mention, makes a case worth an explicit look
_SIGNAL_WORDS = [
    r"distinguish\w*", r"inapplicable", r"not applicable", r"does not apply", r"do not apply", r"not controlling",
    r"not on point", r"not dispositive", r"does not govern", r"does not control", r"inapposite", r"unlike",
    r"by contrast", r"in contrast", r"contrary", r"however", r"but see", r"but cf\.", r"\bcf\.", r"compare",
    r"declin\w+", r"reject\w*", r"misplaced", r"unpersuasive", r"not persuasive", r"narrow\w*",
    r"\blimit(?:ed|s|ing)?\b(?! period)", r"overrul\w+", r"abrogat\w+", r"questioned", r"questionable",
    r"calls? into question", r"disapprov\w+", r"criticiz\w+", r"no longer", r"misread\w*",
    r"conflict\w*", r"erroneous\w*", r"wrongly", r"relie[sd] on", r"reliance", r"cannot agree",
    r"disagree\w*", r"not to the contrary", r"different", r"qualif\w+",
]
_SIGNAL = re.compile("|".join(_SIGNAL_WORDS), re.I)
SIGNAL_WINDOW = 250


def mention_signals(tagged_text, inv, window=SIGNAL_WINDOW):
    """{inventory id: 'however; by contrast; cf.'} — distinct contrast words within `window` chars of any
    of the case's mentions (plain text, tags stripped). A superset with false positives; the model decides."""
    plain_by_id = {}
    for m in _MENTION.finditer(tagged_text):
        gid = int(m.group(1))
        win = re.sub(r"<[^>]+>", "", tagged_text[max(0, m.start() - window): m.end() + window])
        for h in _SIGNAL.findall(win):
            plain_by_id.setdefault(gid, [])
            key = h.lower().strip()
            if key not in plain_by_id[gid]:
                plain_by_id[gid].append(key)
    return {gid: "; ".join(v[:5]) for gid, v in plain_by_id.items()}


def inventory_block_with_signals(inv, signals):
    lines = ["<citedCaseInventory>"]
    for e in inv:
        name = (e.get("name") or "").replace('"', "'")
        cit = "; ".join(e.get("citations", [])[:3]).replace('"', "'")
        sig = signals.get(e["id"], "")
        lines.append(f'<case id="{e["id"]}" name="{name}" citation="{cit}" mentions="{e["n_mentions"]}"'
                     + (f' signals="{sig}"' if sig else "") + " />")
    lines.append("</citedCaseInventory>")
    return "\n".join(lines)


def stage_gate_parts(cid, signals=False):
    """(prefix, opinion_body): prefix = <citingCase/> + <citedCaseInventory>; body = tagged opinion.
    With signals=True each inventory entry also carries the contrast words found near its mentions.
    The runner paginates the body and prepends the prefix to every page."""
    inv, text = inventory(cid), tagged(cid)
    block = inventory_block_with_signals(inv, mention_signals(text, inv)) if signals else inventory_block(inv)
    return citing_header(cid) + block + "\n", text


def flagged_block(inv, flagged_ids):
    by_id = {e["id"]: e for e in inv}
    lines = ["<flaggedCases>"]
    for i in flagged_ids:
        e = by_id.get(i, {})
        name = (e.get("name") or "").replace('"', "'")
        cit = "; ".join(e.get("citations", [])[:3]).replace('"', "'")
        lines.append(f'<case id="{i}" name="{name}" citation="{cit}" />')
    lines.append("</flaggedCases>")
    return "\n".join(lines)


def stage_label_parts(cid, flagged_ids):
    """(prefix, opinion_body) for the labeler: flagged cases keep their mentions tagged as
    <flaggedCase id="N">…</flaggedCase>; every other citation tag is stripped to its text."""
    want = {str(i) for i in flagged_ids}

    def rep(m):
        gid, inner = m.group(1), m.group(2)
        return f'<flaggedCase id="{gid}">{inner}</flaggedCase>' if gid in want else inner
    body = _MENTION.sub(rep, tagged(cid))
    prefix = citing_header(cid) + flagged_block(inventory(cid), flagged_ids) + "\n"
    return prefix, body


def cmd_show(a):
    if a.stage == "gate":
        prefix, body = stage_gate_parts(a.cid, signals=a.signals)
    else:
        prefix, body = stage_label_parts(a.cid, [int(x) for x in a.flag.split(",") if x])
    sys.stdout.write(prefix + "<opinion>\n" + body[: a.chars] + ("\n…" if len(body) > a.chars else "") + "\n</opinion>\n")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--split", default="all", choices=["dev", "test", "all"])
    f.add_argument("--ids", nargs="*")
    f.add_argument("--refetch", action="store_true", help="ignore caches and existing files; call CourtListener")
    f.add_argument("--sleep", type=float, default=0.3)
    f.set_defaults(func=cmd_fetch)
    c = sub.add_parser("centralia", help="feed the valid 0903 centralia readings (eyecite-seeded) + coverage guard")
    c.add_argument("--split", default="all", choices=["dev", "test", "all"])
    c.add_argument("--ids", nargs="*")
    c.set_defaults(func=cmd_centralia)
    b = sub.add_parser("build")
    b.add_argument("--split", default="all", choices=["dev", "test", "all"])
    b.add_argument("--ids", nargs="*")
    b.add_argument("--text-source", default="auto", choices=["auto", "cl"],
                   help="auto = centralia text where the coverage guard passed, else CL html")
    b.add_argument("--grouping", default="revised", choices=["revised", "eyecite"],
                   help="revised = corrected grouping (GPT-fixed root, then verified gold) where it exists; "
                        "eyecite = CourtListener links only")
    b.set_defaults(func=cmd_build)
    s = sub.add_parser("show")
    s.add_argument("--cid", required=True)
    s.add_argument("--stage", default="gate", choices=["gate", "label"])
    s.add_argument("--flag", default="", help="comma-separated inventory ids for --stage label")
    s.add_argument("--chars", type=int, default=6000)
    s.add_argument("--signals", action="store_true", help="gate: add contrast-word hints to the inventory")
    s.set_defaults(func=cmd_show)
    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
