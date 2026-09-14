"""Shared text plumbing for the triage experiment.

One canonical opinion representation feeds both the seeder inputs and the
encoder windows:

    tagged text   plain prose with `<citedCase group="N">…</citedCase>` around
                  every mention of a cited case (N = group id, shared by all
                  mentions of the same case) and `<lead>` / `<concurrence>` /
                  `<dissent>` / … wrappers around sub-opinions
    inventory     [{id: N, cited_cluster_id, name, citation, n_mentions}]

Two sources produce it:

  from_revised_html(html)   the annotator's corrected export
                            (`<cite group="N" cited_ref="R">`, `<noncite>`,
                            `<section data-opinion-type>`), i.e. gold coref
  from_opinion_html(payload) raw CourtListener html_with_citations — mentions
                            grouped by the cluster id in each citation link;
                            un-linked spans (`citation no-link`) each become
                            their own group. The fallback for clusters the
                            annotator has not reached (silver-only opinions).

`mentions(text)` returns every tagged mention with char offsets in the
tagged text; `strip_tags(text)` gives plain prose with an offset map back.
"""
import html as htmlmod
import json
import re
from collections import OrderedDict

OPINION_TYPE_TAG = {
    "010combined": "combined", "015unamimous": "lead", "020lead": "lead",
    "025plurality": "plurality", "030concurrence": "concurrence",
    "035concurrenceinpart": "concurrence", "040dissent": "dissent",
    "050addendum": "addendum", "060remittitur": "remittitur",
    "070rehearing": "rehearing", "080onthemerits": "onthemerits",
    "090onmotiontostrike": "onmotion", "100trialcourt": "trialcourt",
}
_CITE_REV = re.compile(r'<cite\s+([^>]*)>(.*?)</cite>', re.S)
_ATTR = lambda a, s: (re.search(rf'{a}="([^"]*)"', s) or [None, ""])[1]  # noqa: E731
_SECTION_REV = re.compile(r'<section\s+data-opinion-type="([^"]*)"\s*>(.*?)</section>', re.S)
_NONCITE = re.compile(r'</?noncite[^>]*>')
_SPAN_CL = re.compile(r'<span\s+class="citation([^"]*)"([^>]*)>(.*?)</span>', re.S)
_HREF_CID = re.compile(r'href="/opinion/(\d+)/')
_ARIA = re.compile(r'aria-description="Citation for case: ([^"]*)"')
_ANY_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t]+")
_MENTION = re.compile(r'<citedCase group="(\d+)">(.*?)</citedCase>', re.S)
_SECTION_TAG = re.compile(r"</?(lead|concurrence|dissent|plurality|combined|addendum|remittitur|rehearing|onthemerits|onmotion|trialcourt)>")


_SENT_END = ".?!:;"
_PNUM = re.compile(r"^\*?\d{1,4}\.?$")   # CL paragraph numbers / *star pages on their own line


def _ends_sentence(par):
    t = re.sub(r"<[^>]+>", "", par).rstrip(" \"'”’)]*")
    return bool(t) and t[-1] in _SENT_END


def _starts_lower(par):
    for ch in re.sub(r"<[^>]+>", "", par):
        if ch.isalpha():
            return ch.islower()
    return False


def merge_fragment_paragraphs(text):
    """CourtListener's Harvard/Lawbox html sets citations in their own blocks,
    splitting sentences across 'paragraphs' ("Noland," / "495 F.2d at 533.
    Therefore …"). Re-stitch: a block joins the previous one unless the
    previous block ended a sentence and the new one starts like one. Page
    numbers / paragraph numbers on their own line are folded in as text.
    Same rule the benchmark viewer uses for display."""
    parts = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    out = []
    for part in parts:
        plain = re.sub(r"<[^>]+>", "", part).strip()
        if out and (_PNUM.match(plain) or not _ends_sentence(out[-1]) or _starts_lower(part)):
            out[-1] = out[-1].rstrip() + " " + part.lstrip()
        else:
            out.append(part)
    return "\n\n".join(out)


def _clean_prose(fragment):
    """Drop residual html tags (not our own), unescape entities, normalize
    blank-line structure, re-stitch fragment paragraphs."""
    # centralia page-break markers (two renderings) are not opinion text
    fragment = re.sub(r'<span class="pg"[^>]*>[^<]*</span>|<div class="pgbreak"[^>]*>[^<]*</div>', "", fragment)
    t = re.sub(r"</(?:p|div|h\d|li|blockquote|tr)>|<br\s*/?>", "\n\n", fragment)  # block ends = paragraph breaks
    t = _ANY_TAG.sub("", t)
    t = htmlmod.unescape(t)
    t = _WS.sub(" ", t)
    t = re.sub(r"\n[ \t]*\n[\s]*", "\n\n", t)
    return merge_fragment_paragraphs(t.strip())


def _wrap_opinions(parts):
    return "\n\n".join(f"<{tag}>\n{body}\n</{tag}>" for tag, body in parts if body.strip())


def from_revised_html(revised_html):
    """Annotator export -> (tagged_text, inventory). Group ids are the
    `group` numbers; cited_cluster_id from `cited_ref` when it is numeric."""
    inv = OrderedDict()
    parts = []
    sections = _SECTION_REV.findall(revised_html) or [("020lead", revised_html)]
    for otype, body in sections:
        # <noncite> = a span the reviewer removed (not a case citation): keep
        # its text, drop a leading group-number badge the client export can
        # leave glued to it ("3Ibid." → "Ibid."; a real "42 U.S.C." keeps its space)
        body = re.sub(r"<noncite[^>]*>(\d{1,3})(?=[A-Za-z])", "<noncite>", body)
        body = _NONCITE.sub("", body)
        # the viewer's export separates blocks with SINGLE newlines (no <p>
        # tags, no blank lines); make them paragraph breaks before cleaning
        if "\n\n" not in body and "<p" not in body:
            body = body.replace("\n", "\n\n")

        def rep(m):
            attrs, inner = m.group(1), m.group(2)
            gid, ref = _ATTR("group", attrs), _ATTR("cited_ref", attrs)
            text = _clean_prose(inner)
            e = inv.setdefault(gid, {"id": int(gid) if gid.isdigit() else gid,
                                     "cited_cluster_id": int(ref) if ref.isdigit() else None,
                                     "cited_ref": ref, "name": "", "citations": [], "n_mentions": 0})
            e["n_mentions"] += 1
            if text and text not in e["citations"] and len(e["citations"]) < 6:
                e["citations"].append(text)
            return f"\x01{gid}\x02{text}\x03"
        body = _CITE_REV.sub(rep, body)
        body = _clean_prose(body)
        body = re.sub("\x01(\\d+|[^\x02]*)\x02(.*?)\x03",
                      lambda m: f'<citedCase group="{m.group(1)}">{m.group(2)}</citedCase>', body, flags=re.S)
        parts.append((OPINION_TYPE_TAG.get(otype, "lead"), body))
    return _wrap_opinions(parts), list(inv.values())


def from_opinion_html(payload, drop_combined_when_separates=True):
    """Raw CourtListener payload {opinions:[{type, html}]} -> (tagged_text,
    inventory). Mentions linked to the same cited cluster share a group;
    unlinked spans get their own group each."""
    ops = sorted(payload["opinions"], key=lambda o: o.get("type", ""))
    if drop_combined_when_separates and len(ops) > 1:
        seps = [o for o in ops if o.get("type") != "010combined"]
        if seps:
            ops = seps
    inv, gid_of = OrderedDict(), {}
    next_gid = [1]
    parts = []
    for op in ops:
        def rep(m):
            cls, attrs, inner = m.group(1), m.group(2), m.group(3)
            cid_m = _HREF_CID.search(inner) or _HREF_CID.search(attrs)
            did_m = re.search(r'data-id="(\d+)"', attrs)
            name_m = _ARIA.search(inner)
            text = _clean_prose(inner)
            # group key: CL cluster id (CL payload) > eyecite seed id (centralia payload) > the text itself
            key = (("cid", cid_m.group(1)) if cid_m else ("did", did_m.group(1)) if did_m
                   else ("nolink", text.lower()))
            if key not in gid_of:
                gid_of[key] = next_gid[0]
                next_gid[0] += 1
                inv[gid_of[key]] = {"id": gid_of[key],
                                    "cited_cluster_id": int(cid_m.group(1)) if cid_m else None,
                                    "cited_ref": (cid_m.group(1) if cid_m else f"seed:{did_m.group(1)}" if did_m
                                                  else f"nolink:{text}"),
                                    "name": name_m.group(1) if name_m else "",
                                    "citations": [], "n_mentions": 0}
            e = inv[gid_of[key]]
            e["n_mentions"] += 1
            if name_m and not e["name"]:
                e["name"] = name_m.group(1)
            if text and text not in e["citations"] and len(e["citations"]) < 6:
                e["citations"].append(text)
            return f"\x01{gid_of[key]}\x02{text}\x03"
        body = _SPAN_CL.sub(rep, op.get("html", ""))
        body = _clean_prose(body)
        body = re.sub("\x01(\\d+)\x02(.*?)\x03",
                      lambda m: f'<citedCase group="{m.group(1)}">{m.group(2)}</citedCase>', body, flags=re.S)
        parts.append((OPINION_TYPE_TAG.get(op.get("type", ""), "lead"), body))
    return _wrap_opinions(parts), list(inv.values())


def mentions(tagged_text):
    """[{group, start, end, text}] over the TAGGED text (offsets include tags)."""
    return [{"group": m.group(1), "start": m.start(), "end": m.end(),
             "text": _ANY_TAG.sub("", m.group(2))} for m in _MENTION.finditer(tagged_text)]


def strip_tags(tagged_text):
    """Plain prose + a list mapping each plain-text char offset back to the
    tagged-text offset (so quotes located in prose can be tied to mentions)."""
    out, back = [], []
    i = 0
    for m in re.finditer(r"<[^>]+>", tagged_text):
        seg = tagged_text[i:m.start()]
        out.append(seg)
        back.extend(range(i, m.start()))
        i = m.end()
    out.append(tagged_text[i:])
    back.extend(range(i, len(tagged_text)))
    return "".join(out), back


def inventory_block(inventory):
    lines = ["<citedCaseInventory>"]
    for e in inventory:
        name = (e.get("name") or "").replace('"', "'")
        cit = "; ".join(e.get("citations", [])[:3]).replace('"', "'")
        lines.append(f'<case id="{e["id"]}" name="{name}" citation="{cit}" mentions="{e["n_mentions"]}" />')
    lines.append("</citedCaseInventory>")
    return "\n".join(lines)


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)
