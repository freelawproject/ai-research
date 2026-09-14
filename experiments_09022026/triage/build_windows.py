"""Build encoder training/eval windows from tagged opinion text.

Unit = one passage of the citing opinion with ONE target case marked. A
passage is the paragraph holding a mention plus --context paragraphs either
side (default 1), capped at --window-chars (default 4,000 ≈ 1K tokens);
mentions whose paragraph blocks overlap share a passage. When the context
paragraphs push the block over the cap they are trimmed (sentence-aligned,
budget split between the sides, ≥ MIN_CONTEXT chars a side or nothing) rather
than dropped; only a single paragraph over the cap is split into chunks. Rendered with
`[T] … [/T]` around every mention of the target and other citations left as
text (or `[CITE]` with --mask-other).
A header line names the citing case and the target (from the seed dir's
manifest.json).

Labels:
  --gold           benchmark finals (treatments_most_negative.csv) joined on
                   (citing cluster, cited cluster id); quotes from
                   treatments.csv locate the evidence window. Pairs whose only
                   non-neutral label is "as recognized by" are dropped.
  --seed FILE      seeder output JSON {cid: {"citedCases":[{inventoryId,
                   passages:[{quote, treatment, ...}]}], "citedByIds":[...]}}
                   (the parsed, per-cluster form written by the collect step).
                   EVERY passage quote locates a positive window carrying THAT
                   passage's treatment; the pair's treatment is the most
                   severe across its passages.
Window label: 1 = positive window of a treated pair (overlaps the quote, or
all windows when no quote is locatable — flagged weak=1), -1 = other windows
of a treated pair (masked out of training), 0 = window of a Cited-by pair.

Input is a prepare_seed_inputs.py output dir (tagged/ + inventory/).

    python build_windows.py --seed-dir data/seed/dev_kimi --gold --split dev --out data/windows/dev.jsonl
"""
import argparse
import csv
import json
import re
from pathlib import Path

from tagged_text import mentions, strip_tags

BENCH = Path("/Users/rachel/Desktop/flp/citator-benchmark/data")
SEVERITY = {"Reversed by": "Stop", "Reversed and remanded by": "Stop", "Vacated by": "Stop", "Vacated and remanded by": "Stop",
            "Overruled by": "Stop", "Abrogated by": "Stop", "Questioned by": "Stop",
            "Reversed in part; Vacated in part by": "Warning", "Affirmed in part; Reversed in part by": "Warning",
            "Affirmed in part; Vacated in part by": "Warning", "Disapproved by": "Warning", "Limited by": "Warning",
            "Modified by": "Caution", "Remanded by": "Caution", "Cert. granted by": "Caution", "Criticized by": "Caution",
            "Distinguished by": "Caution", "Declined to follow by": "Caution",
            "Dismissed by": "Neutral", "Cited by": "Neutral", "Cert. denied by": "Neutral", "Affirmed by": "Neutral"}
SEVERITY_RANK = {"Neutral": 0, "Caution": 1, "Warning": 2, "Stop": 3}


def severity_of(t):
    return SEVERITY.get((t or "").replace(" as recognized by", " by"), "Neutral")
SPLITS = Path(__file__).parent / "inputs" / "splits.csv"
_SECTION_OPEN = re.compile(r"<(lead|concurrence|dissent|plurality|combined|addendum|remittitur|rehearing|onthemerits|onmotion|trialcourt)>")
_TAG = re.compile(r"<[^>]+>")
_PARA = re.compile(r"\n\s*\n")
_WSN = re.compile(r"\s+")


def norm(s):
    return _WSN.sub(" ", s.replace("’", "'").replace("“", '"').replace("”", '"').replace("—", "-").replace("–", "-")).strip()


def locate_quote(plain, quote):
    """Char span of `quote` in plain text (whitespace-normalized search on a
    stable prefix/middle fragment); None if not found."""
    if not quote:
        return None
    q = norm(quote).strip(" .…")
    pn = norm(plain)
    # map normalized offsets back approximately by searching the raw plain text too
    for L in (80, 50, 30):
        for start in (0, max(0, len(q) // 2 - L // 2)):
            frag = q[start:start + L]
            if len(frag) < 20:
                continue
            i = plain.find(frag)
            if i >= 0:
                return i, i + len(frag)
            j = pn.find(frag)
            if j >= 0:  # normalized hit: rescale to raw offset
                k = int(j * len(plain) / max(1, len(pn)))
                return k, k + len(frag)
    return None


def section_at(text, pos):
    last = None
    for m in _SECTION_OPEN.finditer(text, 0, pos):
        last = m.group(1)
    return last or "lead"


def snap(text, start, end):
    """Expand/shrink to paragraph boundaries within 20% slack; failing that,
    to whitespace so a passage never starts or ends mid-word."""
    slack = (end - start) // 5
    a = text.rfind("\n\n", max(0, start - slack), start + 1)
    b = text.find("\n\n", end - 1, min(len(text), end + slack))
    if a >= 0:
        start = a + 2
    else:
        ws = text.rfind(" ", max(0, start - 80), start + 1)
        start = ws + 1 if ws >= 0 else max(0, start)
    if b >= 0:
        end = b
    else:
        ws = text.find(" ", end - 1, min(len(text), end + 80))
        end = ws if ws >= 0 else min(len(text), end)
    return start, end


def render(text, start, end, target_group, mask_other):
    seg = text[start:end]
    def rep(m):
        gid, inner = m.group(1), _TAG.sub("", m.group(2))
        if gid == target_group:
            return f"[T]{inner}[/T]"
        return "[CITE]" if mask_other else inner
    seg = re.sub(r'<citedCase group="(\d+)">(.*?)</citedCase>', rep, seg, flags=re.S)
    seg = _TAG.sub("", seg)
    return norm_paragraphs(seg)


def norm_paragraphs(s):
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r"\n\s*\n\s*", "\n\n", s).strip()


def paragraph_bounds(text):
    """[(start, end)] of paragraphs (blank-line separated) over the tagged text."""
    bounds, start = [], 0
    for m in _PARA.finditer(text):
        if m.start() > start:
            bounds.append((start, m.start()))
        start = m.end()
    if start < len(text):
        bounds.append((start, len(text)))
    return bounds


def windows_for_group(text, ments, window_chars, paras=None, context=1):
    """Passages for one target. Mentions in the same paragraph always share a
    passage; each passage is its paragraph block ± `context` paragraphs, widened
    until each side holds ≥ MIN_CONTEXT_CHARS (fragment blocks);
    consecutive blocks merge while the merged block fits `window_chars`. A
    block over the cap keeps as much sentence-aligned context on each side as
    fits (`_trim_context`); a single paragraph over the cap is split into
    chunks centered on its mentions (never cutting one off).
    Returns [(start, end, [mention idx])]."""
    paras = paras if paras is not None else paragraph_bounds(text)

    def para_index(pos):
        lo, hi = 0, len(paras) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if paras[mid][1] <= pos:
                lo = mid + 1
            else:
                hi = mid
        return lo

    # 1) cluster mentions by paragraph
    by_para = {}
    for i, m in enumerate(ments):
        by_para.setdefault(para_index(m["start"]), []).append(i)
    # 2) blocks with context; merge neighbours while under the cap.
    #    Context = `context` paragraphs each side, widened paragraph by
    #    paragraph until at least MIN_CONTEXT_CHARS of text sit on each side of
    #    the mention paragraph (CourtListener sets string cites and headings as
    #    their own blocks, so one "paragraph" is often a 40-char fragment),
    #    without crossing into another writing or over the cap.
    blocks = []
    for pi in sorted(by_para):
        a, b = max(0, pi - context), min(len(paras) - 1, pi + context)
        core_s, core_e = paras[pi]
        sec = section_at(text, core_s)
        while a > 0 and core_s - paras[a][0] < MIN_CONTEXT_CHARS \
                and paras[b][1] - paras[a - 1][0] <= window_chars and section_at(text, paras[a - 1][0]) == sec:
            a -= 1
        while b < len(paras) - 1 and paras[b][1] - core_e < MIN_CONTEXT_CHARS \
                and paras[b + 1][1] - paras[a][0] <= window_chars and section_at(text, paras[b + 1][1]) == sec:
            b += 1
        s, e, idxs = paras[a][0], paras[b][1], by_para[pi]
        if blocks and s <= blocks[-1][1] and max(blocks[-1][1], e) - blocks[-1][0] <= window_chars:
            blocks[-1] = (blocks[-1][0], max(blocks[-1][1], e), blocks[-1][2] + idxs, blocks[-1][3] + [pi])
        else:
            blocks.append((s, e, idxs, [pi]))
    spans = []
    for s, e, idxs, pis in blocks:
        if e - s > window_chars:                    # over the cap with full context paragraphs
            core_s, core_e = paras[pis[0]][0], paras[pis[-1]][1]
            if core_e - core_s > window_chars:      # a single huge paragraph: split it at sentence
                spans.extend(_split_long_block(text, core_s, core_e, [ments[i] for i in idxs], idxs, window_chars))
                continue
            # keep as much of the neighbouring paragraphs as fits (sentence-aligned)
            # instead of dropping them: a citation at the edge of its paragraph
            # otherwise loses exactly the sentences that discuss it
            s, e = _trim_context(text, core_s, core_e, s, e, window_chars)
        spans.append((s, e, idxs))
    return spans


_SENT_BREAK = re.compile(r"(?<=[.?!;:])\s+(?=[A-Z\[\"“(])")


MIN_CONTEXT = 300        # trimmed context under this many chars on a side is dropped
MIN_CONTEXT_CHARS = 600  # widen the context paragraphs until each side has at least this much text


def _trim_context(text, core_s, core_e, s, e, window_chars):
    """Shrink the context paragraphs around the mention block [core_s, core_e)
    so the passage fits window_chars: the budget is split between the two
    sides (a side with less available text gives its share to the other),
    the left context starts at a sentence start, the right context ends at a
    sentence end. A side that would get under MIN_CONTEXT chars is dropped."""
    budget = window_chars - (core_e - core_s)
    if budget < MIN_CONTEXT:
        return core_s, core_e
    left_avail, right_avail = core_s - s, e - core_e
    left = min(left_avail, budget // 2)
    right = min(right_avail, budget - left)
    left = min(left_avail, budget - right)
    ns, ne = core_s, core_e
    if left >= MIN_CONTEXT:
        start = core_s - left
        m = _SENT_BREAK.search(text, start, core_s)   # first sentence start inside the kept region
        if m and m.end() < core_s:
            ns = m.end()
        else:
            ws = text.find(" ", start, core_s)
            ns = ws + 1 if ws >= 0 else start
    if right >= MIN_CONTEXT:
        end = core_e + right
        last = None
        for m in _SENT_BREAK.finditer(text, core_e, end):  # last sentence end inside the kept region
            last = m.start()
        if last is not None and last > core_e:
            ne = last
        else:
            ws = text.rfind(" ", core_e, end)
            ne = ws if ws > core_e else end
    return ns, ne


def _split_long_block(text, s, e, ments, idxs, window_chars):
    """Chunk an over-long paragraph into sentence-aligned pieces of at most
    window_chars, each around a run of consecutive mentions (a mention is
    never cut). Greedy: extend the current chunk while the next mention still
    fits; otherwise close it at the sentence break before that mention."""
    breaks = [s] + [m.end() for m in _SENT_BREAK.finditer(text, s, e)] + [e]
    def floor_(pos):   # last sentence start <= pos (block start if none)
        return max((b for b in breaks if b <= pos), default=s)
    def ceil_(pos):    # first sentence end >= pos (block end if none, e.g. a mention crossing the block end)
        return min((b for b in breaks if b >= pos), default=e)
    out, cur, cur_start = [], [], None
    for m, i in zip(ments, idxs):
        m_s, m_e = floor_(m["start"]), ceil_(m["end"])
        if cur and m_e - cur_start > window_chars:
            out.append((cur_start, ceil_(ments[idxs.index(cur[-1])]["end"]), cur))
            cur, cur_start = [], None
        if cur_start is None:
            cur_start = max(s, m_s - window_chars // 4)   # a little lead-in before the first mention
            cur_start = floor_(cur_start)
        cur.append(i)
    if cur:
        out.append((cur_start, ceil_(ments[idxs.index(cur[-1])]["end"]), cur))
    # pad short chunks with following context, up to the cap / block end
    padded = []
    for cs, ce, ci in out:
        if ce - cs < window_chars:
            ce = min(e, ceil_(min(e, cs + window_chars)) if cs + window_chars < e else e)
        padded.append((cs, ce, ci))
    return padded


def load_gold(split):
    ids = {r["cluster_id"] for r in csv.DictReader(open(SPLITS)) if r["split"] == split}
    finals, quotes = {}, {}
    for r in csv.DictReader(open(BENCH / "treatments_most_negative.csv")):
        if r["citing_case_cluster_id"] in ids and r["final_treatment"]:
            finals[(r["citing_case_cluster_id"], r["cited_case_cluster_id"])] = r["final_treatment"]
    for r in csv.DictReader(open(BENCH / "treatments.csv")):
        if r["citing_cluster_id"] in ids and r["final_quote"]:
            k = (r["citing_cluster_id"], r["cited_cluster_id"])
            quotes[k] = [(r["final_quote"], finals.get(k, r["final_treatment"]))]
    return ids, finals, quotes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-dir", required=True, help="prepare_seed_inputs.py output dir (tagged/, inventory/)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--gold", action="store_true")
    g.add_argument("--seed", help="parsed seeder output JSON")
    g.add_argument("--all-pairs", action="store_true",
                   help="UNLABELED: every (passage, target) for every inventory entry with a cited cluster "
                        "id — the input to the passage-level seeder (prepare_passage_inputs.py)")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--window-chars", type=int, default=4000, help="cap per passage, ~1K tokens")
    ap.add_argument("--context", type=int, default=1, help="paragraphs of context either side of the mention's paragraph")
    ap.add_argument("--mask-other", action="store_true")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    sd = Path(a.seed_dir)

    if a.all_pairs:
        ids = {p.stem for p in sd.glob("tagged/*.txt")}
        finals, quotes, label_src = {}, {}, "none"
    elif a.gold:
        ids, finals, quotes = load_gold(a.split)
        label_src = "gold"
    else:
        seed = json.load(open(a.seed))
        ids = set(seed)
        finals, quotes = {}, {}
        for cid, res in seed.items():
            inv = {str(e["id"]): e for e in json.load(open(sd / "inventory" / f"{cid}.json"))}
            for e in res.get("citedCases", []):
                ie = inv.get(str(e["inventoryId"]))
                if ie and ie.get("cited_cluster_id"):
                    k = (cid, str(ie["cited_cluster_id"]))
                    ps = e.get("passages") or ([{"quote": e.get("quote"), "treatment": e.get("treatment")}]
                                               if e.get("treatment") else [])
                    ps = [p_ for p_ in ps if p_.get("treatment") and p_["treatment"] != "Cited by"]
                    if not ps:
                        continue
                    finals[k] = max((p_["treatment"] for p_ in ps), key=lambda t: SEVERITY_RANK.get(severity_of(t), 0))
                    quotes[k] = [(p_.get("quote") or "", p_["treatment"]) for p_ in ps]
            for gid in res.get("citedByIds", []):
                ie = inv.get(str(gid))
                if ie and ie.get("cited_cluster_id"):
                    finals.setdefault((cid, str(ie["cited_cluster_id"])), "Cited by")
        label_src = "seed"

    manifest = json.load(open(sd / "manifest.json")).get("clusters", {})
    rows, stats = [], {"pairs": 0, "pos_pairs": 0, "dropped_relref": 0, "no_label": 0,
                       "windows": 0, "pos": 0, "masked": 0, "neg": 0, "unlabeled": 0, "weak_pairs": 0}
    for tpath in sorted(sd.glob("tagged/*.txt"), key=lambda p: int(p.stem)):
        cid = tpath.stem
        if cid not in ids:
            continue
        text = tpath.read_text(encoding="utf-8")
        inv = json.load(open(sd / "inventory" / f"{cid}.json"))
        ments = mentions(text)
        plain, back = strip_tags(text)
        fwd = {b: i for i, b in enumerate(back)}  # tagged offset -> plain offset (approx by nearest)
        paras = paragraph_bounds(text)
        by_group = {}
        for i, m in enumerate(ments):
            by_group.setdefault(m["group"], []).append(m)
        for e in inv:
            gid, ccid = str(e["id"]), e.get("cited_cluster_id")
            if gid not in by_group or (ccid is None and not a.all_pairs):
                continue  # labels join on cluster id; unlabeled passages need none
            key = (cid, str(ccid) if ccid is not None else f"{cid}:g{gid}")
            if a.all_pairs:
                finals[key] = None
            elif key not in finals:
                stats["no_label"] += 1
                continue
            t = finals[key]
            if t and t != "Cited by" and t.endswith(" as recognized by"):
                stats["dropped_relref"] += 1
                continue
            stats["pairs"] += 1
            positive = bool(t) and t != "Cited by"
            stats["pos_pairs"] += positive
            # every located passage quote -> (tagged start, tagged end, that passage's treatment)
            evid = []
            if positive:
                for q, t_p in quotes.get(key, []):
                    qspan = locate_quote(plain, q)
                    if qspan:
                        evid.append((back[min(qspan[0], len(back) - 1)], back[min(qspan[1], len(back) - 1)], t_p))
            weak = positive and not evid
            stats["weak_pairs"] += weak
            for w_i, (s, en, idxs) in enumerate(windows_for_group(text, by_group[gid], a.window_chars, paras, a.context)):
                w_treat = t
                if t is None:
                    label = None
                elif positive:
                    hits = [t_p for qs, qe, t_p in evid if qs < en and qe > s]
                    if not evid:
                        label = 1
                    elif hits:
                        label = 1
                        w_treat = max(hits, key=lambda x: SEVERITY_RANK.get(severity_of(x), 0))
                    else:
                        label = -1
                else:
                    label = 0
                stats["windows"] += 1
                stats["unlabeled" if label is None else "pos" if label == 1 else "masked" if label == -1 else "neg"] += 1
                cm = manifest.get(cid, {})
                header = (f"Citing case: {cm.get('case_name', '')} ({cm.get('court', '')} {cm.get('year', '')}) | "
                          f"Target case: {e.get('name', '') or '(unnamed)'} {'; '.join(e.get('citations', [])[:2])}")
                rows.append({
                    "window_id": f"{cid}:{gid}:{w_i}", "cluster_id": cid, "group": gid,
                    "cited_cluster_id": ccid, "cited_ref": e.get("cited_ref", ""),
                    "target_name": e.get("name", ""),
                    "target_citation": "; ".join(e.get("citations", [])[:2]),
                    "section": section_at(text, s),
                    "label": label, "treatment": w_treat, "pair_treatment": t,
                    "severity": severity_of(w_treat) if label == 1 else ("Neutral" if label == 0 else ""),
                    "split": a.split if a.gold else ("train" if not a.all_pairs else ""),
                    "weak": int(weak), "label_source": label_src,
                    "n_target_mentions": sum(1 for m in by_group[gid] if s <= m["start"] < en),
                    "start": s, "end": en,
                    "header": header,
                    "text": header + "\n\n" + render(text, s, en, gid, a.mask_other),
                })
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote {out}: {stats}")


if __name__ == "__main__":
    main()
