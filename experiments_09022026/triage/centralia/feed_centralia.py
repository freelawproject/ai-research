"""Feed centralia's readings back into the annotator.

Rule (Rachel, 2026-09-03): when centralia parsed the court PDF cleanly
(status == "valid"), the annotator shows centralia's html instead of
CourtListener's; otherwise the CL html_with_citations stays as it is.
centralia emits no citation tags, so its writings are run through eyecite
to SEED `<span class="citation" data-id="N">` tags — no resolution to CL
cluster ids (the annotator groups them). Spans that eyecite resolves to the
same resource (full cite + its short forms / id. / supra) share a data-id so
the Grouping page seeds them into one group.

Per fed cluster:
  data/opinion_html/{cid}.cl.json   the original CL payload (kept, once)
  data/opinion_html/{cid}.json      REPLACED: one entry per centralia writing
                                    {id, type (CL type), source: "centralia",
                                    author, html (eyecite-tagged), text}
  grouping_overrides/{cid}.json     centralia.fed_back = true (+ fed_at)

Skips clusters the annotator has already touched (assignments / manual
spans present) unless --force, since a new text orphans the old spans.
`--revert` puts the CL payload back.

    cd flp/centralia && uv run --with eyecite python <triage>/feed_centralia.py [--ids …] [--force] [--revert]
  (or plain `python3 feed_centralia.py` where eyecite is importable)
"""
import argparse
import csv
import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from eyecite import annotate_citations, clean_text, get_citations, resolve_citations
from eyecite.models import (FullCaseCitation, IdCitation, ReferenceCitation,
                            ShortCaseCitation, SupraCitation)

logging.getLogger("eyecite").setLevel(logging.ERROR)
HERE = Path(__file__).resolve().parent.parent
CASE_TYPES = (FullCaseCitation, ShortCaseCitation, IdCitation, SupraCitation, ReferenceCitation)
CL_TYPE = {"majority": "020lead", "per-curiam": "020lead", "order": "020lead", "plurality": "025plurality",
           "concurrence": "030concurrence", "concurring-in-part-and-dissenting-in-part": "035concurrenceinpart",
           "dissent": "040dissent"}
SEED_BASE = 800000   # synthetic data-id namespace, never a real CL opinion id
# centralia page-break markers, two renderings seen: <span class="pg" title="page N">N</span>
# and <div class="pgbreak" data-pg="N">p. N</div>. Keep the boundary, drop the visible text.
PG_MARK = re.compile(r'<span class="pg"[^>]*title="page (\d+)"[^>]*>\s*(?:p\.\s*)?\d*\s*</span>')
PG_BREAK = re.compile(r'<div class="pgbreak"[^>]*data-pg="(\d+)"[^>]*>\s*(?:p\.\s*)?\d*\s*</div>')


def tag_opinions(htmls):
    """eyecite over ALL writings of one opinion at once; returns
    ([tagged_html per writing], n_spans, n_resources).

    Seeding rule: a full case citation and the short forms / Id. / supra
    that eyecite RESOLVES to it share one data-id (one seed group) — across
    writings, so a dissent's short cite links to the majority's full cite.
    Anything unresolved gets NO data-id (the viewer seeds it alone). Resolution
    runs over all citations (statutes included) so an Id. after a statute is
    not attached to the previous case; only case-headed resources get an id.
    Ids are unique per opinion (resolving per writing and restarting the
    counter collided group 1 of the majority with group 1 of the concurrence).
    """
    # centralia marks each page boundary with <span class="pg" title="page N">N</span>;
    # the digit is a reviewer aid, not opinion text — keep the boundary, drop the text
    htmls = [PG_BREAK.sub(r'<span class="pg" data-page="\1"></span>',
                          PG_MARK.sub(r'<span class="pg" data-page="\1"></span>', h)) for h in htmls]
    texts = [clean_text(h, ["html", "all_whitespace"]) for h in htmls]
    sep = "\n\n"
    offsets, pos = [], 0
    for t in texts:
        offsets.append(pos)
        pos += len(t) + len(sep)
    combined = sep.join(texts)
    all_cites = get_citations(combined)
    rid, n_res = {}, 0
    for res, cs in resolve_citations(all_cites).items():
        if not isinstance(getattr(res, "citation", None), CASE_TYPES):
            continue
        n_res += 1
        for c in cs:
            rid[id(c)] = n_res
    cites = [c for c in all_cites if isinstance(c, CASE_TYPES)]
    out = []
    for i, (t, h) in enumerate(zip(texts, htmls)):
        lo, hi = offsets[i], offsets[i] + len(t)
        ann = []
        for c in cites:
            s0, e0 = c.span()
            if lo <= s0 < hi:
                attr = f' data-id="{SEED_BASE + rid[id(c)]}"' if id(c) in rid else ""
                ann.append(((s0 - lo, e0 - lo), f'<span class="citation"{attr}>', "</span>"))
        out.append(annotate_citations(t, ann, source_text=h, unbalanced_tags="skip") if ann else h)
    return out, len(cites), n_res


def load_overrides(root, cid):
    p = root / "data" / "grouping_overrides" / f"{cid}.json"
    return (json.load(open(p)) if p.exists() else {}), p


def save_overrides(p, ov):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ov, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(HERE / "data" / "annotator"))
    ap.add_argument("--centralia-out", default=str(HERE / "data" / "centralia" / "out"))
    ap.add_argument("--ids", nargs="*", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--revert", action="store_true")
    ap.add_argument("--refeed", action="store_true",
                    help="re-tag clusters already fed (still skips ones annotated by hand)")
    ap.add_argument("--strip-markers", action="store_true",
                    help="in-place: empty centralia page-break markers in ALL fed payloads without re-tagging "
                         "(safe for hand-annotated clusters: citation spans and their order are untouched)")
    a = ap.parse_args()
    root, cout = Path(a.root), Path(a.centralia_out)
    html_dir = root / "data" / "opinion_html"

    if a.strip_markers:
        n = 0
        for p in sorted(html_dir.glob("*.json")):
            if p.name.endswith(".cl.json"):
                continue
            d = json.load(open(p, encoding="utf-8"))
            if d.get("origin") != "centralia+eyecite":
                continue
            changed = False
            for o in d["opinions"]:
                h2 = PG_BREAK.sub(r'<span class="pg" data-page="\1"></span>',
                                  PG_MARK.sub(r'<span class="pg" data-page="\1"></span>', o["html"]))
                if h2 != o["html"]:
                    o["html"], changed = h2, True
            if changed:
                p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
                n += 1
        print(f"stripped page-number markers in {n} fed payload(s)")
        return
    if a.revert:
        ids = a.ids or [p.stem[:-3] for p in html_dir.glob("*.cl.json")]
        for cid in ids:
            bak = html_dir / f"{cid}.cl.json"
            if bak.exists():
                shutil.copyfile(bak, html_dir / f"{cid}.json")
                ov, p = load_overrides(root, cid)
                if ov.get("centralia"):
                    ov["centralia"].pop("fed_back", None)
                    ov["centralia"].pop("fed_at", None)
                    save_overrides(p, ov)
                print(f"[{cid}] reverted to CL html")
        return

    valid = []
    for p in sorted(cout.glob("*.json"), key=lambda p: int(p.stem)):
        if a.ids and p.stem not in a.ids:
            continue
        d = json.load(open(p))
        if d.get("status") == "valid" and d.get("opinions"):
            valid.append((p.stem, d))
    print(f"{len(valid)} clusters with a valid centralia reading")
    report = []
    for cid, d in valid:
        ov, ovp = load_overrides(root, cid)
        if (ov.get("centralia") or {}).get("fed_back") and not (a.force or a.refeed):
            continue
        if (ov.get("assignments") or ov.get("manual")) and not a.force:
            print(f"[{cid}] SKIP: already annotated on the CL text (use --force to replace)")
            continue
        cl_path, bak = html_dir / f"{cid}.json", html_dir / f"{cid}.cl.json"
        if cl_path.exists() and not bak.exists():
            shutil.copyfile(cl_path, bak)
        cl_spans = 0
        if bak.exists():
            cl_spans = sum(o["html"].count('class="citation') for o in json.load(open(bak))["opinions"])
        writings = sorted(d["opinions"], key=lambda o: o.get("order", 0))
        tagged, n_spans, n_res = tag_opinions([o.get("html", "") for o in writings])
        opinions = []
        for k, (o, html) in enumerate(zip(writings, tagged)):
            opinions.append({"id": int(f"{cid}{k + 1:02d}"), "type": CL_TYPE.get(o.get("type"), "020lead"),
                             "source": "centralia", "centralia_type": o.get("type"),
                             "author": o.get("author", ""), "pages": o.get("pages"), "html": html,
                             "text": o.get("text", "")})
        payload = {"cluster_id": int(cid), "opinions": opinions, "origin": "centralia+eyecite",
                   "centralia_versions": d.get("versions"), "pdf_source": d.get("_source", {}).get("pdf_source"),
                   "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        cl_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        ov.setdefault("centralia", {})["fed_back"] = True
        ov["centralia"]["fed_at"] = payload["fetched"]
        save_overrides(ovp, ov)
        report.append((cid, len(opinions), n_spans, n_res, cl_spans))
        print(f"[{cid}] fed: {len(opinions)} writing(s), eyecite spans {n_spans} (resources {n_res}) vs CL spans {cl_spans}")
    log = HERE / "data" / "centralia" / "fed_back.csv"
    new = not log.exists()
    with open(log, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["cluster_id", "n_writings", "eyecite_spans", "eyecite_resources", "cl_spans", "fed_at"])
        for r in report:
            w.writerow([*r, datetime.now(timezone.utc).isoformat(timespec="seconds")])
    print(f"fed {len(report)} clusters; log {log}")


if __name__ == "__main__":
    main()
