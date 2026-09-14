"""LLM pass 1 — correct citation extraction + coreference on the annotator's opinions.

    prepare  --ids …  [--seed-state]   numbered `<c id g>` inputs + id maps
    apply    --ids …  [--write]        edits json → override changes (dry-run unless --write)
    score    --ids …                   LLM state vs the gold overrides (dev/test only)
    report   [--ids …]                 eyecite state vs seeded state, per opinion + totals (no gold needed)
    pick     [--n 50]                  next batch of unseeded train opinions -> batch_<stamp>_ids.txt

Occurrence keys, group ids, manual-span anchoring (text + whitespace-insensitive nth)
follow the annotator (citator-benchmark/pipeline/grouping_data.py, app.py) so every
edit maps onto its override schema.

--seed-state ignores the cluster's hand corrections (keeps only the script's statute
strips) — the state the LLM must start from on dev/test, whose overrides hold the gold.
"""
import argparse
import csv
import datetime
import hashlib
import json
import os
import random
import re
import shutil
import sys
from collections import Counter, defaultdict
from html import unescape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tagged_text as tt  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
# Point the harness at another annotator root / seeding dir (e.g. the round-2
# 10K set in experiments_09092026) with CITSEED_ANNOT / CITSEED_OUT.
ANNOT = os.environ.get("CITSEED_ANNOT") or os.path.join(ROOT, "data", "annotator")
OUT = os.environ.get("CITSEED_OUT") or os.path.join(ROOT, "data", "citation_seed")
PROMPT = os.path.join(ROOT, "prompts", "triage_citation_fixer_v1.md")
OVERRIDES = os.path.join(ANNOT, "data", "grouping_overrides")

CIT_SPAN = re.compile(r'<span class="citation(?P<extra>[^"]*)"(?P<attrs>[^>]*)>(?P<inner>.*?)</span>', re.DOTALL)
HREF_ID = re.compile(r'href="/opinion/(\d+)/')
C_HREF = re.compile(r'href="(/c/[^"#]+)')
DATA_ID = re.compile(r'data-id="(\d+)"')
_BLOCK = re.compile(r'</(p|div|blockquote|li|h[1-6])>|<br\s*/?>', re.I)
S1, S2, S3 = "\x01", "\x02", "\x03"


def strip_to_text(html):
    html = _BLOCK.sub("\n", html)
    html = re.sub(r"<[^>]+>", "", html)
    return unescape(html)


def norm_map(text):
    """whitespace-normalized text + map norm index -> original index (port of the viewer's findNth)."""
    norm, mp, last_space = [], [], True
    for i, ch in enumerate(text):
        if ch.isspace():
            if last_space:
                continue
            norm.append(" ")
            mp.append(i)
            last_space = True
        else:
            norm.append(ch)
            mp.append(i)
            last_space = False
    return "".join(norm), mp


def find_nth(text, needle, nth):
    needle = re.sub(r"\s+", " ", needle).strip()
    if not needle:
        return None
    norm, mp = norm_map(text)
    idx = -1
    for _ in range(nth + 1):
        idx = norm.find(needle, idx + 1)
        if idx < 0:
            return None
    return mp[idx], mp[idx + len(needle) - 1] + 1


def nth_of(text, needle, before):
    """(nth, span) of the occurrence of `needle` preceded by `before` (both whitespace-insensitive)."""
    needle_n = re.sub(r"\s+", " ", needle).strip()
    before_n = re.sub(r"\s+", " ", before or "").strip()
    norm, mp = norm_map(text)
    idx, n, cands = -1, -1, []
    while True:
        idx = norm.find(needle_n, idx + 1)
        if idx < 0:
            break
        n += 1
        cands.append((n, idx))
    if not cands:
        return None, None
    pick = None
    if before_n:
        for n, idx in cands:
            if norm[max(0, idx - len(before_n) - 2):idx].rstrip().endswith(before_n):
                pick = (n, idx)
                break
        if pick is None:  # loosen: last 15 chars of the anchor
            tail = before_n[-15:]
            for n, idx in cands:
                if norm[max(0, idx - len(tail) - 2):idx].rstrip().endswith(tail):
                    pick = (n, idx)
                    break
    anchored = pick is not None
    if pick is None:
        pick = cands[0]
    n, idx = pick
    nth_of.last_anchored = anchored or not before_n
    return n, (mp[idx], mp[idx + len(needle_n) - 1] + 1)


def drop_combined(opinions):
    seps = [o for o in opinions if not str(o.get("type") or "").startswith("010")]
    return seps or opinions


def load_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def script_removed(cid):
    p = os.path.join(ANNOT, "overrides", "citation_changes.csv")
    out = set()
    for r in csv.DictReader(open(p, encoding="utf-8")):
        if r["citing_cluster_id"] == str(cid) and r["by"] == "script" and r["action"] == "removed":
            out.add(r["ref"])
    return out


def meta_of(cid):
    for r in csv.DictReader(open(os.path.join(ANNOT, "overrides", "citing_metadata.csv"), encoding="utf-8")):
        if r["cluster_id"] == str(cid):
            return r
    return {}


class State:
    """occurrences (eyecite spans) + assignments + removed + manual + names + roles over plain writings."""

    def __init__(self, cid, seed_state):
        self.cid = str(cid)
        payload = load_json(os.path.join(ANNOT, "data", "opinion_html", f"{cid}.json"))
        self.opinions = drop_combined(payload["opinions"])
        self.texts, self.occ, self.op_type = {}, {}, {}
        seed_to_gid = {}

        def gid_for(seed):
            if seed not in seed_to_gid:
                seed_to_gid[seed] = f"g{len(seed_to_gid) + 1}"
            return seed_to_gid[seed]

        for op in self.opinions:
            oid = str(op["id"])
            self.op_type[oid] = str(op.get("type") or "")
            idx = [0]
            pending = []

            def annotate(m):
                key = f"{op['id']}:{idx[0]}"
                idx[0] += 1
                inner, attrs = m.group("inner"), m.group("attrs") or ""
                href, chref, did = HREF_ID.search(inner), C_HREF.search(inner), DATA_ID.search(attrs)
                seed = (f"c{href.group(1)}" if href else f"cite:{chref.group(1)}" if chref
                        else f"d{did.group(1)}" if did else f"solo:{key}")
                pending.append((key, gid_for(seed)))
                return f"{S1}{key}{S2}{inner}{S3}"

            marked = strip_to_text(CIT_SPAN.sub(annotate, op.get("html", "")))
            gid_of = dict(pending)
            out, i = [], 0
            pos = 0
            for m in re.finditer(f"{S1}(.*?){S2}(.*?){S3}", marked, re.S):
                out.append(marked[i:m.start()])
                pos += m.start() - i
                key, text = m.group(1), m.group(2)
                start = pos
                out.append(text)
                pos += len(text)
                self.occ[key] = {"occ_id": key, "op_id": oid, "span": (start, pos), "text": text.strip(),
                                 "seed_gid": gid_of[key]}
                i = m.end()
            out.append(marked[i:])
            self.texts[oid] = "".join(out)
        ov = load_json(os.path.join(ANNOT, "data", "grouping_overrides", f"{cid}.json")) \
            if os.path.exists(os.path.join(ANNOT, "data", "grouping_overrides", f"{cid}.json")) else {}
        self.assign = {k: o["seed_gid"] for k, o in self.occ.items()}
        if seed_state:
            self.removed = set(script_removed(cid)) & set(self.occ)
            self.manual, self.names, self.roles = [], {}, {}
        else:
            self.assign.update({k: g for k, g in (ov.get("assignments") or {}).items() if k in self.occ})
            self.removed = set(ov.get("removed_occs") or []) & set(self.occ)
            self.manual = [dict(m) for m in ov.get("manual") or []]
            self.names = dict(ov.get("group_names") or {})
            self.roles = dict(ov.get("roles") or {})
        self.ov_raw = ov

    # -- helpers
    def op_by_type(self, typ):
        for oid, t in self.op_type.items():
            if t == typ:
                return oid
        return None

    def manual_span(self, m):
        oid = self.op_by_type(str(m.get("opinion_type", "")))
        if oid is None:
            return None, None
        sp = find_nth(self.texts[oid], m.get("text", ""), int(m.get("nth", 0)))
        return oid, sp

    def gids(self):
        return set(self.assign.values()) | {m["group"] for m in self.manual}

    def new_gid(self):
        n = max([int(g[1:]) for g in self.gids() if re.match(r"g\d+$", g)] + [0]) + 1
        return f"g{n}"

    def mentions(self):
        """[(op_id, start, end, gid, kind, key)] — live eyecite occurrences + manual spans."""
        out = []
        for k, o in self.occ.items():
            if k in self.removed:
                continue
            out.append((o["op_id"], o["span"][0], o["span"][1], self.assign[k], "occ", k))
        for m in self.manual:
            oid, sp = self.manual_span(m)
            if sp:
                out.append((oid, sp[0], sp[1], m["group"], "manual", m["id"]))
        return out


# ------------------------------------------------------------------ prepare
def render_input(st, meta):
    """numbered tagged text + inventory + header; returns (text, map)."""
    mid = [0]
    m2key = {}
    parts = []
    for op in st.opinions:
        oid = str(op["id"])
        T = st.texts[oid]
        tags = []  # (start, end, key, gid, kind)
        for k, o in st.occ.items():
            if o["op_id"] == oid and k not in st.removed:
                tags.append((o["span"][0], o["span"][1], k, st.assign[k], "occ"))
        for m in st.manual:
            o2, sp = st.manual_span(m)
            if o2 == oid and sp:
                tags.append((sp[0], sp[1], m["id"], m["group"], "manual"))
        tags.sort()
        out, i = [], 0
        for s, e, key, gid, kind in tags:
            if s < i:  # overlap (manual inside occ) — skip
                continue
            mid[0] += 1
            m2key[mid[0]] = {"kind": kind, "key": key, "gid": gid}
            out.append(T[i:s])
            out.append(f'<c id="{mid[0]}" g="{gid}">{T[s:e]}</c>')
            i = e
        out.append(T[i:])
        body = re.sub(r"\n{3,}", "\n\n", "".join(out)).strip()
        tag = tt.OPINION_TYPE_TAG.get(st.op_type[oid], "lead")
        parts.append(f"<{tag}>\n{body}\n</{tag}>")
    # inventory
    by_g = defaultdict(list)
    for k, o in st.occ.items():
        if k not in st.removed:
            by_g[st.assign[k]].append(o["text"])
    for m in st.manual:
        by_g[m["group"]].append(m.get("text", ""))
    inv = ["<citedCaseInventory>"]
    for g in sorted(by_g, key=lambda g: int(g[1:]) if re.match(r"g\d+$", g) else 10**6):
        cites = []
        for c in by_g[g]:
            c = re.sub(r"\s+", " ", c)
            if c and c not in cites and not re.match(r"^(id\.|ibid\.?|supra)", c, re.I):
                cites.append(c)
        name = (st.names.get(g) or "").replace('"', "'")
        inv.append(f'<case id="{g}" name="{name}" citation="{"; ".join(cites[:3]).replace(chr(34), chr(39))}" mentions="{len(by_g[g])}" />')
    inv.append("</citedCaseInventory>")
    header = f'<citingCase name="{(meta.get("case_name") or "").replace(chr(34), chr(39))}" court="{meta.get("court", "")}" year="{meta.get("year", "")}" />'
    text = header + "\n\n" + "\n".join(inv) + "\n\n<opinion>\n" + "\n\n".join(parts) + "\n</opinion>\n"
    op_tags = defaultdict(list)
    for oid in st.op_type:
        op_tags[tt.OPINION_TYPE_TAG.get(st.op_type[oid], "lead")].append(oid)
    op_tags = dict(op_tags)
    return text, {"cluster_id": st.cid, "mentions": m2key, "opinion_tags": op_tags, "n_mentions": mid[0]}


def cmd_prepare(a):
    os.makedirs(os.path.join(OUT, "inputs"), exist_ok=True)
    for cid in a.ids:
        st = State(cid, a.seed_state)
        text, mp = render_input(st, meta_of(cid))
        mp["seed_state"] = bool(a.seed_state)
        with open(os.path.join(OUT, "inputs", f"{cid}.txt"), "w", encoding="utf-8") as f:
            f.write(text)
        with open(os.path.join(OUT, "inputs", f"{cid}.map.json"), "w", encoding="utf-8") as f:
            json.dump(mp, f, indent=1)
        print(f"{cid}: {mp['n_mentions']} mentions, {len(text):,} chars -> inputs/{cid}.txt")


# ------------------------------------------------------------------ apply
def parse_edits(path):
    raw = open(path, encoding="utf-8").read()
    m = re.search(r"<edits>\s*(?:```(?:json)?)?\s*(\{.*\})\s*(?:```)?\s*</edits>", raw, re.S)
    txt = m.group(1) if m else raw
    txt = re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip()
    e = json.loads(txt)
    for k in ("remove", "regroup", "merge", "add"):
        e.setdefault(k, [])
    for k in ("names", "roles"):
        e.setdefault(k, {})
    return e


def apply_edits(st, edits, mp):
    """Mutates `st`; returns a log of applied/skipped edits."""
    m2 = {int(k): v for k, v in mp["mentions"].items()}
    log = {"applied": [], "skipped": []}
    newg = {}

    def resolve_gid(to):
        to = str(to)
        if to.startswith("new"):
            if to not in newg:
                newg[to] = st.new_gid()
                st.assign_dummy = None
            return newg[to]
        return to

    for r in edits["remove"]:
        ref = m2.get(int(r["m"])) if str(r.get("m", "")).isdigit() else None
        if not ref:
            log["skipped"].append(("remove", r, "unknown mention"))
            continue
        if ref["kind"] == "occ":
            st.removed.add(ref["key"])
        else:
            st.manual = [m for m in st.manual if m["id"] != ref["key"]]
        log["applied"].append(("remove", ref["key"], r.get("why", "")))
    for r in edits["regroup"]:
        ref = m2.get(int(r["m"])) if str(r.get("m", "")).isdigit() else None
        if not ref:
            log["skipped"].append(("regroup", r, "unknown mention"))
            continue
        if r.get("to") in (None, ""):
            log["skipped"].append(("regroup", r, "missing target group"))
            continue
        gid = resolve_gid(r["to"])
        if ref["kind"] == "occ":
            st.assign[ref["key"]] = gid
        else:
            for m in st.manual:
                if m["id"] == ref["key"]:
                    m["group"] = gid
        log["applied"].append(("regroup", ref["key"], gid))
    for grp in edits["merge"]:
        grp = [resolve_gid(g) for g in grp]
        if len(grp) < 2:
            continue
        keep, rest = grp[0], set(grp[1:])
        for k, g in list(st.assign.items()):
            if g in rest:
                st.assign[k] = keep
        for m in st.manual:
            if m["group"] in rest:
                m["group"] = keep
        for g in rest:
            if g in st.names and keep not in st.names:
                st.names[keep] = st.names[g]
            st.names.pop(g, None)
            st.roles.pop(g, None)
        log["applied"].append(("merge", keep, sorted(rest)))
    used = {m["id"] for m in st.manual}
    for r in edits["add"]:
        cands = mp["opinion_tags"].get(str(r.get("opinion", "lead")), [])
        cands = [cands] if isinstance(cands, str) else list(cands)
        cands += [o for o in st.texts if o not in cands]  # fall back to every writing
        oid, n, sp = None, None, None
        for cand in cands:
            n, sp = nth_of(st.texts[cand], r.get("text", ""), r.get("before", ""))
            if n is not None:
                oid = cand
                break
        if n is None:
            log["skipped"].append(("add", r, "text not found"))
            continue
        if r.get("to") in (None, ""):
            log["skipped"].append(("add", r, "missing target group"))
            continue
        # skip if it lands inside a live eyecite span
        if any(o["op_id"] == oid and k not in st.removed and o["span"][0] <= sp[0] < o["span"][1]
               for k, o in st.occ.items()):
            log["skipped"].append(("add", r, "inside an existing tag"))
            continue
        i = 1
        while f"m{i}" in used:
            i += 1
        used.add(f"m{i}")
        st.manual.append({"id": f"m{i}", "opinion_type": st.op_type[oid], "text": re.sub(r"\s+", " ", r["text"]).strip(),
                          "nth": n, "group": resolve_gid(r["to"])})
        log["applied"].append(("add", f"m{i}", r["text"], n, "anchored" if getattr(nth_of, "last_anchored", True) else "FALLBACK"))
    for g, name in (edits.get("names") or {}).items():
        gid = resolve_gid(g)
        if name:
            st.names[gid] = name
    for g, role in (edits.get("roles") or {}).items():
        st.roles[resolve_gid(g)] = role
    log["new_groups"] = newg
    return log


def state_to_override_delta(st):
    """Override fields reflecting the state (relative to seeds)."""
    return {"assignments": {k: g for k, g in st.assign.items() if g != st.occ[k]["seed_gid"]},
            "removed_occs": sorted(st.removed),
            "manual": st.manual, "group_names": st.names, "roles": st.roles}


def cmd_apply(a):
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    os.makedirs(os.path.join(OUT, "backups"), exist_ok=True)
    out_dir = a.out_dir or os.path.join(OUT, "outputs")
    for cid in a.ids:
        mp = load_json(os.path.join(OUT, "inputs", f"{cid}.map.json"))
        st = State(cid, mp.get("seed_state", False))
        edits = parse_edits(os.path.join(out_dir, f"{cid}.edits.json"))
        log = apply_edits(st, edits, mp)
        print(f"{cid}: applied {len(log['applied'])} edits, skipped {len(log['skipped'])}, new groups {len(log['new_groups'])}")
        for s in log["skipped"]:
            print("   skipped:", json.dumps(s, ensure_ascii=False)[:160])
        with open(os.path.join(out_dir, f"{cid}.apply_log.json"), "w", encoding="utf-8") as f:
            json.dump(log, f, indent=1, ensure_ascii=False)
        if not a.write:
            continue
        path = os.path.join(ANNOT, "data", "grouping_overrides", f"{cid}.json")
        # --seed-state inputs describe the eyecite seed, not the current state; writing
        # them over an existing override would clobber human/gold edits. When no override
        # exists yet (fresh roots such as round 2) the seed IS the current state -> safe.
        if mp.get("seed_state") and os.path.exists(path):
            print("   refusing to write: input was built in --seed-state and an override already exists (gold cluster)")
            continue
        if os.path.exists(path):
            shutil.copy2(path, os.path.join(OUT, "backups", f"{cid}.{stamp}.json"))
        ov = st.ov_raw or {}
        ov.update(state_to_override_delta(st))
        ov["llm_citation_pass"] = {"prompt": os.path.basename(PROMPT), "at": stamp,
                                   "n_applied": len(log["applied"]), "n_skipped": len(log["skipped"])}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(ov, f, ensure_ascii=False, indent=1)
        with open(os.path.join(ANNOT, "overrides", "citation_changes.csv"), "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            at = datetime.datetime.now().isoformat(timespec="seconds")
            for e in log["applied"]:
                kind, ref = e[0], e[1]
                text = st.occ[ref]["text"] if ref in st.occ else (e[2] if kind == "add" else "")
                reason = (e[2] if kind == "remove" else f"-> {e[2]}" if kind == "regroup"
                          else f"absorbs {e[2]}" if kind == "merge" else f"manual tag -> group")
                w.writerow([at, cid, ref, text, kind, reason, "llm"])
        print(f"   written to overrides (backup {stamp})")


# ------------------------------------------------------------------ score
def _match(ma, mb):
    """greedy overlap matching within a writing → list of (ia, ib)."""
    pairs, used = [], set()
    for ia, a_ in enumerate(ma):
        best, bo = None, 0
        for ib, b_ in enumerate(mb):
            if ib in used or a_[0] != b_[0]:
                continue
            ov = min(a_[2], b_[2]) - max(a_[1], b_[1])
            if ov > bo:
                best, bo = ib, ov
        if best is not None:
            used.add(best)
            pairs.append((ia, best))
    return pairs


def prf(tp, np_, ng):
    p = tp / np_ if np_ else 0.0
    r = tp / ng if ng else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return round(p, 3), round(r, 3), round(f, 3)


def score_states(sys_st, gold_st):
    ms, mg = sys_st.mentions(), gold_st.mentions()
    pairs = _match(ms, mg)
    mention = prf(len(pairs), len(ms), len(mg))
    # pairwise coreference over matched mentions
    tp = fp = fn = 0
    for i in range(len(pairs)):
        for j in range(i + 1, len(pairs)):
            a1, a2 = ms[pairs[i][0]], ms[pairs[j][0]]
            g1, g2 = mg[pairs[i][1]], mg[pairs[j][1]]
            same_s, same_g = a1[3] == a2[3], g1[3] == g2[3]
            tp += same_s and same_g
            fp += same_s and not same_g
            fn += same_g and not same_s
    coref = prf(tp, tp + fp, tp + fn)
    return {"mentions_sys": len(ms), "mentions_gold": len(mg), "mention_PRF": mention, "coref_pairwise_PRF": coref,
            "_raw": {"m_tp": len(pairs), "m_sys": len(ms), "m_gold": len(mg), "c_tp": tp, "c_fp": fp, "c_fn": fn}}


def edit_precision(seed, llm, gold, log):
    """Share of the LLM's applied edits that the gold agrees with."""
    res = Counter()
    detail = []
    gold_m = gold.mentions()
    for e in log["applied"]:
        kind = e[0]
        ok = None
        if kind == "remove":
            key = e[1]
            if key in gold.occ:
                ok = key in gold.removed
                if not ok and e[2] == "wrong_span":
                    o = seed.occ[key]
                    readd = any(oid == o["op_id"] and min(sp[1], o["span"][1]) > max(sp[0], o["span"][0])
                                for oid, sp in [llm.manual_span(m) for m in llm.manual] if sp)
                    if readd:  # net effect = a mention at the same place; gold has one there too
                        ok = True
                        res[("respan", "ok")] += 1
                        continue
        elif kind == "regroup":
            key, gid = e[1], e[2]
            if key in gold.occ:
                mates = [k for k, g in llm.assign.items() if g == gid and k != key and k not in llm.removed]
                if mates:
                    gm = Counter(gold.assign[k] for k in mates if k not in gold.removed)
                    ok = bool(gm) and gold.assign.get(key) == gm.most_common(1)[0][0]
                else:  # split into a singleton: gold must also separate it from its seed mates
                    seedmates = [k for k, o in seed.occ.items() if o["seed_gid"] == seed.occ[key]["seed_gid"] and k != key]
                    ok = all(gold.assign.get(k) != gold.assign.get(key) for k in seedmates if k not in gold.removed)
        elif kind == "merge":
            keep, rest = e[1], e[2]
            a = [k for k, g in seed.assign.items() if g == keep and k not in gold.removed]
            b = [k for k, g in seed.assign.items() if g in rest and k not in gold.removed]
            if a and b:
                ga = Counter(gold.assign[k] for k in a).most_common(1)[0][0]
                gb = Counter(gold.assign[k] for k in b).most_common(1)[0][0]
                ok = ga == gb
        elif kind == "add":
            mid = e[1]
            m = next((x for x in llm.manual if x["id"] == mid), None)
            if m:
                oid, sp = llm.manual_span(m)
                if sp:
                    ok = any(g[0] == oid and min(g[2], sp[1]) > max(g[1], sp[0]) for g in gold_m)
        if ok is None:
            res[(kind, "n/a")] += 1
        else:
            res[(kind, "ok" if ok else "wrong")] += 1
            if not ok:
                detail.append(e)
    return res, detail


def gold_edit_recall(seed, llm, gold):
    """Of the gold's own changes to the seed, how many did the LLM make?"""
    out = {}
    g_removed = gold.removed - seed.removed
    out["removed"] = (len(g_removed & llm.removed), len(g_removed))
    # a gold regroup = the occurrence's companions changed vs eyecite; recovered = LLM gives it the same companions
    g_re = {k for k in gold.occ if k not in gold.removed and comates(gold, k) != comates(seed, k)}
    hit = sum(1 for k in g_re if k not in llm.removed and comates(llm, k) == comates(gold, k))
    out["regrouped"] = (hit, len(g_re))
    gm = [(o, s) for o, s in [gold.manual_span(m) for m in gold.manual] if s]
    lm = [(o, s) for o, s in [llm.manual_span(m) for m in llm.manual] if s]
    hit = sum(1 for o, s in gm if any(o2 == o and min(s[1], s2[1]) > max(s[0], s2[0]) for o2, s2 in lm))
    out["added"] = (hit, len(gm))
    return out


def _snip(st, oid, s, e, w=140):
    T = st.texts[oid]
    return re.sub(r"\s+", " ", T[max(0, s - w):s]) + "⟦" + T[s:e] + "⟧" + re.sub(r"\s+", " ", T[e:e + 40])


def diff_report(cid, seed, llm, gold, log, wrong):
    """Markdown: every LLM edit the gold disagrees with + every gold change the LLM missed, with snippets."""
    out = [f"## {cid}"]
    for e in wrong:
        kind = e[0]
        if kind in ("remove", "regroup") and e[1] in seed.occ:
            o = seed.occ[e[1]]
            gg = gold.assign.get(e[1])
            mates = [seed.occ[k]["text"] for k, g in gold.assign.items() if g == gg and k != e[1] and k not in gold.removed][:3]
            out.append(f"- WRONG {kind} `{o['text']}`" + (f" → {e[2]}" if kind == "regroup" else "") +
                       f" (gold keeps it{' with ' + ' / '.join(mates) if mates else ''}): …{_snip(seed, o['op_id'], *o['span'])}…")
        elif kind == "add":
            m = next((x for x in llm.manual if x["id"] == e[1]), None)
            if m:
                oid, sp = llm.manual_span(m)
                if sp:
                    out.append(f"- WRONG add `{m['text']}` → {m['group']} (gold has no mention here): …{_snip(llm, oid, *sp)}…")
        elif kind == "merge":
            out.append(f"- WRONG merge {e[1]} ⇐ {e[2]} (gold keeps them apart)")
    for k in sorted(gold.removed - seed.removed - llm.removed):
        o = seed.occ[k]
        out.append(f"- MISSED remove `{o['text']}`: …{_snip(seed, o['op_id'], *o['span'])}…")
    for k in gold.occ:
        if k in gold.removed or k in llm.removed:
            continue
        if gold.assign[k] != seed.assign[k] and llm.assign[k] == seed.assign[k] and comates(gold, k) != comates(llm, k):
            o = seed.occ[k]
            gg = gold.assign[k]
            mates = [seed.occ[x]["text"] for x, g in gold.assign.items() if g == gg and x != k and x not in gold.removed][:3]
            out.append(f"- MISSED regroup `{o['text']}` → gold groups it with {' / '.join(mates) or '(new group)'}: …{_snip(seed, o['op_id'], *o['span'])}…")
    lm = [(o, s) for o, s in [llm.manual_span(m) for m in llm.manual] if s]
    for m in gold.manual:
        oid, sp = gold.manual_span(m)
        if not sp:
            continue
        if not any(o2 == oid and min(sp[1], s2[1]) > max(sp[0], s2[0]) for o2, s2 in lm):
            out.append(f"- MISSED add `{m['text']}`: …{_snip(gold, oid, *sp)}…")
    return "\n".join(out) if len(out) > 1 else ""


SEED_REVIEW_DIR = os.path.join(ANNOT, "data", "seed_reviews")


def _hash(*parts):
    return hashlib.sha1("|".join(str(x) for x in parts).encode()).hexdigest()[:12]


def _ctx(st, oid, s, e, w=160):
    T = st.texts[oid]
    return {"before": re.sub(r"\s+", " ", T[max(0, s - w):s]), "text": T[s:e], "after": re.sub(r"\s+", " ", T[e:e + 60])}


def comates(st, k):
    """Live occurrence keys sharing k's group (partition view, independent of group ids)."""
    g = st.assign.get(k)
    return frozenset(x for x, gg in st.assign.items() if gg == g and x != k and x not in st.removed)


def _mates(st, gid, exclude=None, n=4):
    return [st.occ[k]["text"] for k, g in st.assign.items() if g == gid and k != exclude and k not in st.removed][:n]


def review_items(cid, seed, llm, gold, log, wrong):
    """Every LLM-vs-gold disagreement as a reviewable item with the edit that
    'LLM is right' would apply to the gold overrides (llm_action)."""
    items = []
    for e in wrong:
        kind = e[0]
        if kind == "remove" and e[1] in seed.occ:
            o = seed.occ[e[1]]
            gg = gold.assign.get(e[1])
            items.append({"id": _hash("wrong_remove", e[1]), "kind": "wrong_remove", "occ": e[1],
                          "ctx": _ctx(seed, o["op_id"], *o["span"]), "op_id": o["op_id"],
                          "llm": f"remove ({e[2]})", "gold": f"keep in group with {' / '.join(_mates(gold, gg, e[1])) or '(alone)'}",
                          "llm_action": {"op": "remove", "occ": e[1], "why": e[2]}})
        elif kind == "regroup" and e[1] in seed.occ:
            o = seed.occ[e[1]]
            members = [k for k, g in llm.assign.items() if g == e[2] and k != e[1] and k not in llm.removed]
            items.append({"id": _hash("wrong_regroup", e[1]), "kind": "wrong_regroup", "occ": e[1],
                          "ctx": _ctx(seed, o["op_id"], *o["span"]), "op_id": o["op_id"],
                          "llm": f"group with {' / '.join(seed.occ[k]['text'] for k in members[:4]) or '(new group)'}",
                          "gold": f"group with {' / '.join(_mates(gold, gold.assign.get(e[1]), e[1])) or '(alone)'}",
                          "llm_action": {"op": "assign", "occ": e[1], "members": members}})
        elif kind == "add":
            m = next((x for x in llm.manual if x["id"] == e[1]), None)
            if m:
                oid, sp = llm.manual_span(m)
                if sp:
                    members = [k for k, g in llm.assign.items() if g == m["group"] and k not in llm.removed]
                    items.append({"id": _hash("wrong_add", m["text"], m["nth"], m["opinion_type"]), "kind": "wrong_add",
                                  "ctx": _ctx(llm, oid, *sp), "op_id": oid,
                                  "llm": f"tag as mention of {' / '.join(seed.occ[k]['text'] for k in members[:3]) or '(new group)'}",
                                  "gold": "not a mention",
                                  "llm_action": {"op": "add", "text": m["text"], "nth": m["nth"], "opinion_type": m["opinion_type"], "members": members}})
        elif kind == "merge":
            keep, rest = e[1], e[2]
            ma = [k for k, g in seed.assign.items() if g == keep and k not in gold.removed]
            mb = [k for k, g in seed.assign.items() if g in rest and k not in gold.removed]
            if ma and mb:
                oa = seed.occ[ma[0]]
                items.append({"id": _hash("wrong_merge", keep, *sorted(rest)), "kind": "wrong_merge", "occ": ma[0],
                              "ctx": _ctx(seed, oa["op_id"], *oa["span"]), "op_id": oa["op_id"],
                              "llm": f"same case: {' / '.join(seed.occ[k]['text'] for k in ma[:3])} = {' / '.join(seed.occ[k]['text'] for k in mb[:3])}",
                              "gold": "different cases",
                              "llm_action": {"op": "merge", "members_a": ma, "members_b": mb}})
    for k in sorted(gold.removed - seed.removed - llm.removed):
        o = seed.occ[k]
        items.append({"id": _hash("missed_remove", k), "kind": "missed_remove", "occ": k,
                      "ctx": _ctx(seed, o["op_id"], *o["span"]), "op_id": o["op_id"],
                      "llm": f"keep in group with {' / '.join(_mates(llm, llm.assign.get(k), k)) or '(alone)'}", "gold": "removed (not a case mention)",
                      "llm_action": {"op": "restore", "occ": k, "members": [x for x, g in llm.assign.items() if g == llm.assign.get(k) and x != k and x not in llm.removed]}})
    for k in gold.occ:
        if k in gold.removed or k in llm.removed:
            continue
        if gold.assign[k] != seed.assign[k] and llm.assign[k] == seed.assign[k] and comates(gold, k) != comates(llm, k):
            o = seed.occ[k]
            items.append({"id": _hash("missed_regroup", k), "kind": "missed_regroup", "occ": k,
                          "ctx": _ctx(seed, o["op_id"], *o["span"]), "op_id": o["op_id"],
                          "llm": f"leave with {' / '.join(_mates(seed, seed.assign[k], k)) or '(alone)'}",
                          "gold": f"group with {' / '.join(_mates(gold, gold.assign[k], k)) or '(alone)'}",
                          "llm_action": {"op": "assign", "occ": k, "members": [x for x, g in seed.assign.items() if g == seed.assign[k] and x != k and x not in llm.removed]}})
    lm = [(o, s) for o, s in [llm.manual_span(m) for m in llm.manual] if s]
    for m in gold.manual:
        oid, sp = gold.manual_span(m)
        if not sp or any(o2 == oid and min(sp[1], s2[1]) > max(sp[0], s2[0]) for o2, s2 in lm):
            continue
        items.append({"id": _hash("missed_add", m["text"], m["nth"], m["opinion_type"]), "kind": "missed_add",
                      "ctx": _ctx(gold, oid, *sp), "op_id": oid,
                      "llm": "not a mention", "gold": f"mention of {' / '.join(_mates(gold, m['group'])) or m['group']}",
                      "llm_action": {"op": "delete_manual", "text": m["text"], "nth": m["nth"], "opinion_type": m["opinion_type"]}})
    return items


def write_review(cid, items, iter_tag, prompt):
    os.makedirs(SEED_REVIEW_DIR, exist_ok=True)
    path = os.path.join(SEED_REVIEW_DIR, f"{cid}.json")
    prev = load_json(path) if os.path.exists(path) else {}
    decisions = {k: v for k, v in (prev.get("decisions") or {}).items() if k in {it["id"] for it in items}}
    data = {"cluster_id": str(cid), "iter": iter_tag, "prompt": prompt,
            "written": datetime.datetime.now().isoformat(timespec="seconds"), "items": items, "decisions": decisions}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    ovp = os.path.join(OVERRIDES, f"{cid}.json")
    ov = load_json(ovp) if os.path.exists(ovp) else {}
    ov["seed_diff"] = {"n": len(items), "n_open": len(items) - len(decisions), "iter": iter_tag}
    with open(ovp + ".tmp", "w", encoding="utf-8") as f:
        json.dump(ov, f, ensure_ascii=False, indent=1)
    os.replace(ovp + ".tmp", ovp)
    return len(items), len(decisions)


def cmd_score(a):
    allres = {}
    out_dir = a.out_dir or os.path.join(OUT, "outputs")
    pooled = Counter()
    diffs = []
    for cid in a.ids:
        if not os.path.exists(os.path.join(out_dir, f"{cid}.edits.json")):
            print(f"\n== {cid}: NO OUTPUT")
            pooled["missing"] += 1
            continue
        mp = load_json(os.path.join(OUT, "inputs", f"{cid}.map.json"))
        seed = State(cid, True)
        llm = State(cid, True)
        gold = State(cid, False)
        try:
            edits = parse_edits(os.path.join(out_dir, f"{cid}.edits.json"))
        except Exception as exc:
            print(f"\n== {cid}: BAD JSON {exc}")
            pooled["bad_json"] += 1
            continue
        log = apply_edits(llm, edits, mp)
        base = score_states(seed, gold)
        res = score_states(llm, gold)
        ep, wrong = edit_precision(seed, llm, gold, log)
        rec = gold_edit_recall(seed, llm, gold)
        names = {}
        for g, n in llm.names.items():
            # compare against gold name of the group holding the same occurrences
            occs = [k for k, gg in llm.assign.items() if gg == g and k not in llm.removed]
            if occs:
                gg = Counter(gold.assign[k] for k in occs if k not in gold.removed).most_common(1)
                if gg and gold.names.get(gg[0][0]):
                    names[g] = (n, gold.names[gg[0][0]])
        allres[cid] = {"eyecite_baseline": base, "llm": res, "edit_precision": {f"{k[0]}:{k[1]}": v for k, v in ep.items()},
                       "gold_edit_recall": rec, "skipped": log["skipped"], "wrong_edits": wrong, "names_vs_gold": names}
        for pre, r_ in (("b_", base["_raw"]), ("l_", res["_raw"])):
            for k, v in r_.items():
                pooled[pre + k] += v
        pooled["edits_ok"] += sum(v for k, v in ep.items() if k[1] == "ok")
        pooled["edits_wrong"] += sum(v for k, v in ep.items() if k[1] == "wrong")
        for k, (h, n) in rec.items():
            pooled["rec_" + k + "_hit"] += h
            pooled["rec_" + k + "_n"] += n
        d = diff_report(cid, seed, llm, gold, log, wrong)
        if d:
            diffs.append(d)
        if a.write_review:
            n_items, n_dec = write_review(cid, review_items(cid, seed, llm, gold, log, wrong),
                                          os.path.basename(out_dir.rstrip("/")), a.review_prompt or "")
            pooled["review_items"] += n_items
            pooled["review_decided"] += n_dec
        print(f"\n== {cid}")
        print(f"  eyecite baseline: mention P/R/F {base['mention_PRF']}  coref P/R/F {base['coref_pairwise_PRF']}  ({base['mentions_sys']} vs gold {base['mentions_gold']})")
        print(f"  LLM             : mention P/R/F {res['mention_PRF']}  coref P/R/F {res['coref_pairwise_PRF']}  ({res['mentions_sys']} vs gold {res['mentions_gold']})")
        print(f"  edit precision  : {dict(sorted(ep.items()))}")
        print(f"  gold edits recovered: {rec}")
        print(f"  skipped edits: {len(log['skipped'])}")
        for k, (ln, gn) in names.items():
            print(f"  name {k}: llm={ln!r} gold={gn!r}")
    def pooled_prf(pre):
        m = prf(pooled[pre + "m_tp"], pooled[pre + "m_sys"], pooled[pre + "m_gold"])
        c = prf(pooled[pre + "c_tp"], pooled[pre + "c_tp"] + pooled[pre + "c_fp"], pooled[pre + "c_tp"] + pooled[pre + "c_fn"])
        return m, c
    bm, bc = pooled_prf("b_")
    lm_, lc = pooled_prf("l_")
    n_e = pooled["edits_ok"] + pooled["edits_wrong"]
    summary = {"n_scored": len(allres), "missing": pooled["missing"], "bad_json": pooled["bad_json"],
               "eyecite_mention_PRF": bm, "eyecite_coref_PRF": bc, "llm_mention_PRF": lm_, "llm_coref_PRF": lc,
               "edit_precision": round(pooled["edits_ok"] / n_e, 4) if n_e else None, "edits_ok": pooled["edits_ok"], "edits_wrong": pooled["edits_wrong"],
               "gold_edit_recall": {k: (pooled["rec_" + k + "_hit"], pooled["rec_" + k + "_n"]) for k in ("removed", "regrouped", "added")}}
    print("\n== POOLED over", len(allres), "opinions")
    print(f"  eyecite: mention P/R/F {bm}  coref P/R/F {bc}")
    print(f"  LLM    : mention P/R/F {lm_}  coref P/R/F {lc}")
    print(f"  edit precision {summary['edit_precision']} ({pooled['edits_ok']} ok / {pooled['edits_wrong']} wrong); gold edits recovered {summary['gold_edit_recall']}")
    os.makedirs(os.path.join(out_dir, "eval"), exist_ok=True)
    with open(os.path.join(out_dir, "eval", "score.json"), "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "per_cluster": allres}, f, indent=1, ensure_ascii=False, default=str)
    with open(os.path.join(out_dir, "eval", "diff.md"), "w", encoding="utf-8") as f:
        f.write(f"# LLM vs gold disagreements ({len(allres)} opinions)\n\n" + "\n\n".join(diffs) + "\n")
    print(f"  wrote {out_dir}/eval/score.json and diff.md")
    if a.write_review:
        print(f"  seed reviews: {pooled['review_items']} items ({pooled['review_decided']} already decided) -> {SEED_REVIEW_DIR}")


# ------------------------------------------------------------------ report
def _state_stats(st):
    ms = st.mentions()
    by_g = Counter(m[3] for m in ms)
    live = {g for g, n in by_g.items() if n}
    idish = [m for m in ms if re.match(r"^(id\.|ibid|supra)", (st.occ[m[5]]["text"] if m[4] == "occ" else
                                                                next(x for x in st.manual if x["id"] == m[5])["text"]).strip(), re.I)]
    id_solo = sum(1 for m in idish if by_g[m[3]] == 1)
    named = sum(1 for g in live if st.names.get(g))
    return {"mentions": len(ms), "groups": len(live), "singletons": sum(1 for g in live if by_g[g] == 1),
            "id_like": len(idish), "id_like_alone_in_group": id_solo, "named_groups": named}


def cmd_report(a):
    ids = a.ids
    if not ids:
        ids = sorted(f[:-5] for f in os.listdir(OVERRIDES)
                     if f.endswith(".json") and (load_json(os.path.join(OVERRIDES, f)).get("llm_citation_pass")))
    meta = {r["cluster_id"]: r for r in csv.DictReader(open(os.path.join(ANNOT, "overrides", "citing_metadata.csv"), encoding="utf-8"))}
    rows, tot = [], defaultdict(Counter)
    for cid in ids:
        seed, cur = State(cid, True), State(cid, False)
        a_, b_ = _state_stats(seed), _state_stats(cur)
        origin = load_json(os.path.join(ANNOT, "data", "opinion_html", f"{cid}.json")).get("origin", "")
        lp = cur.ov_raw.get("llm_citation_pass") or {}
        log = load_json(os.path.join(OUT, "outputs", f"{cid}.apply_log.json")) if os.path.exists(os.path.join(OUT, "outputs", f"{cid}.apply_log.json")) else {"applied": []}
        kinds = Counter(e[0] for e in log["applied"])
        row = {"cluster_id": cid, "origin": origin, "court": meta.get(cid, {}).get("court", ""), "year": meta.get(cid, {}).get("year", ""),
               "chars": sum(len(t) for t in seed.texts.values()),
               **{f"eyecite_{k}": v for k, v in a_.items()}, **{f"llm_{k}": v for k, v in b_.items()},
               "removes": kinds["remove"], "adds": kinds["add"], "merges": kinds["merge"], "regroups": kinds["regroup"],
               "n_applied": lp.get("n_applied", len(log["applied"]))}
        rows.append(row)
        for key in ("all", origin):
            t = tot[key]
            t["opinions"] += 1
            for k, v in row.items():
                if isinstance(v, int) and k not in ("year",):
                    t[k] += v
    os.makedirs(os.path.join(OUT, "eval"), exist_ok=True)
    with open(os.path.join(OUT, "eval", "seed_delta.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    for key, t in tot.items():
        n = t["opinions"]
        print(f"\n== {key}: {n} opinions, {t['chars']:,} chars")
        for k in ("mentions", "groups", "singletons", "id_like", "id_like_alone_in_group", "named_groups"):
            print(f"  {k:24s} eyecite {t['eyecite_' + k]:6d}   llm {t['llm_' + k]:6d}   delta {t['llm_' + k] - t['eyecite_' + k]:+d}")
        print(f"  edits: removes {t['removes']}  adds {t['adds']}  merges {t['merges']}  regroups {t['regroups']}  "
              f"(per opinion: {t['n_applied'] / n:.1f} structural edits)")
    print(f"\nper-opinion table: {os.path.join(OUT, 'eval', 'seed_delta.csv')}")


# ------------------------------------------------------------------ pick
def cmd_pick(a):
    """Next batch of train opinions to seed: unverified, not yet seeded, 3K–90K chars,
    stratified by text source × length like wave 2. Writes data/citation_seed/batch_<stamp>_ids.txt."""
    splits = {r["cluster_id"]: r["split"] for r in csv.DictReader(open(os.path.join(ROOT, "inputs", "splits.csv")))}
    meta = [r for r in csv.DictReader(open(os.path.join(ANNOT, "overrides", "citing_metadata.csv"), encoding="utf-8"))
            if splits.get(r["cluster_id"], "train") == "train"]
    jobs = load_json(os.path.join(OUT, "bedrock_jobs.json")) if os.path.exists(os.path.join(OUT, "bedrock_jobs.json")) else {}
    batched = {str(c) for m in jobs.values() for c in m.get("ids", [])}
    rows = []
    for r in meta:
        cid = r["cluster_id"]
        ov = load_json(os.path.join(OVERRIDES, f"{cid}.json")) if os.path.exists(os.path.join(OVERRIDES, f"{cid}.json")) else {}
        if ov.get("double_reviewed") or ov.get("cluster_done") or ov.get("llm_citation_pass"):
            continue
        if any(os.path.exists(os.path.join(OUT, d, f"{cid}.edits.json")) for d in ("outputs",)):
            continue
        if cid in batched:  # already exported/submitted in a Bedrock batch (bedrock_jobs.json)
            continue
        payload = load_json(os.path.join(ANNOT, "data", "opinion_html", f"{cid}.json"))
        n = sum(len(strip_to_text(o.get("html", ""))) for o in drop_combined(payload["opinions"]))
        if n > a.max_chars or n < 3000:
            continue
        rows.append((cid, payload.get("origin", ""), n))
    random.seed(a.seed)
    bins = defaultdict(list)
    for x in rows:
        bins[(x[1], "S" if x[2] < 15000 else "M" if x[2] < 40000 else "L")].append(x)
    for b in bins.values():
        random.shuffle(b)
    pick, i = [], 0
    order = sorted(bins)
    while len(pick) < a.n and any(bins.values()):  # round-robin over strata
        b = bins[order[i % len(order)]]
        if b:
            pick.append(b.pop())
        i += 1
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    path = os.path.join(OUT, f"batch_{stamp}_ids.txt")
    with open(path, "w") as f:
        f.write("\n".join(x[0] for x in pick) + "\n")
    print(f"{len(rows)} eligible; picked {len(pick)} -> {path}")
    print(" ".join(x[0] for x in pick))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--ids", nargs="+", required=True)
    p.add_argument("--seed-state", action="store_true")
    p = sub.add_parser("apply")
    p.add_argument("--ids", nargs="+", required=True)
    p.add_argument("--write", action="store_true")
    p.add_argument("--out-dir", help="where {cid}.edits.json live (default data/citation_seed/outputs)")
    p = sub.add_parser("score")
    p.add_argument("--ids", nargs="+", required=True)
    p.add_argument("--out-dir", help="where {cid}.edits.json live (default data/citation_seed/outputs)")
    p.add_argument("--write-review", action="store_true", help="write LLM-vs-gold disagreements for the annotator's seed-diff review flow")
    p.add_argument("--review-prompt", help="prompt name recorded with the review items")
    p = sub.add_parser("report", help="eyecite state vs current (LLM-seeded) state; default: every seeded cluster")
    p.add_argument("--ids", nargs="*")
    p = sub.add_parser("pick", help="choose the next batch of unseeded train opinions")
    p.add_argument("--n", type=int, default=50)
    p.add_argument("--max-chars", type=int, default=90000)
    p.add_argument("--seed", type=int, default=None)
    a = ap.parse_args()
    {"prepare": cmd_prepare, "apply": cmd_apply, "score": cmd_score, "report": cmd_report, "pick": cmd_pick}[a.cmd](a)


if __name__ == "__main__":
    main()
