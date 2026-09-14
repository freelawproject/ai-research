"""Mechanical candidate generator for the citation-fixer prompt.

Weaker models (GPT-5.6, Sonnet) apply the conventions correctly but do not
FIND the untagged references: on dev, 84 of GPT's 94 missed name-only
references never appeared in its own enumeration. This module does the search
deterministically over a prepared input (data/citation_seed/inputs/{cid}.txt)
and emits a `<candidates>` block the model must adjudicate one by one:

  - short names of every cited case, derived from the case name that precedes
    each tagged full citation (`United States v. Pugliese, <c …>805 F.2d 1117</c>`
    -> `Pugliese`; `Davidson v. Kimberly-Clark Corp.` -> `Davidson`,
    `Kimberly-Clark`), found in the untagged prose; occurrences that are part
    of a name introducing its own citation are dropped (fragment rule);
  - every untagged `Id.` / `Ibid.` (with pin), `volume reporter, at page` short
    form and `Name, supra[, at N]`, each with the group of the nearest
    preceding tagged citation as a hint.

It is a superset with false positives (party-as-party uses, generic labels):
the model decides. Usage:

    from candidates import candidates_block
    block = candidates_block(open(path).read())   # "" if nothing found
"""

from __future__ import annotations

import re
import sys

TAG = re.compile(r'<c id="(\d+)" g="(g\d+)">(.*?)</c>', re.S)
WS = re.compile(r"\s+")
STAR = re.compile(r"\*\d+")
# words that never serve as a case short name on their own
STOP = {
    "United", "States", "State", "People", "Commonwealth", "City", "County", "Town", "Village", "Board", "Bd.",
    "Board", "Department", "Dept.", "Dep't", "Co.", "Company", "Inc.", "Corp.", "Corporation", "Ltd.", "LLC", "L.L.C.",
    "Ass'n", "Assn.", "Association", "Bank", "Trust", "Insurance", "Ins.", "Hospital", "Hosp.", "University", "Univ.",
    "School", "District", "Dist.", "Comm'n", "Commission", "Committee", "Secretary", "Sec'y", "Attorney", "General",
    "Director", "Administrator", "Commissioner", "Ex", "In", "re", "Matter", "of", "the", "The", "and", "et", "al.",
    "v.", "vs.", "on", "for", "at", "by", "In", "Estate", "Ex", "parte", "Petition", "Application", "Appeal", "Sons",
    "Bros.", "Brothers", "National", "Nat'l", "American", "Am.", "International", "Int'l", "Federal", "Fed.", "Public",
    "Pub.", "Service", "Serv.", "Services", "Servs.", "Industries", "Indus.", "Group", "Holdings", "Partners", "Fund",
    "Authority", "Auth.", "Agency", "Office", "Union", "Local", "Health", "Medical", "Center", "Ctr.", "Power",
    "Light", "Gas", "Oil", "Electric", "Railroad", "R.R.", "Ry.", "Railway", "Motor", "Motors", "Airlines", "Life",
    "Mutual", "Mut.", "Casualty", "Cas.", "Fire", "Savings", "Loan", "Sav.", "Building", "Bldg.", "Land", "Realty",
    "Properties", "Prop.", "Enterprises", "Products", "Prods.", "Foods", "Stores", "Supply", "Mfg.", "Manufacturing",
    "Chemical", "Steel", "Mining", "Coal", "Lumber", "Paper", "Telephone", "Tel.", "Telegraph", "Broadcasting",
    "Communications", "Publishing", "Press", "News", "Times", "Journal", "Court", "Judge", "Justice", "Sheriff",
    "Warden", "Superintendent", "Chief", "Officer", "Police", "Parish", "Township", "Borough", "Municipality",
    "Regents", "Trustees", "Governor", "President", "Mayor", "Treasurer", "Comptroller", "Collector", "Clerk",
    "Internal", "Revenue", "Securities", "Exchange", "Labor", "Relations", "Interior", "Treasury", "Defense", "Housing",
    "Education", "Welfare", "Human", "Resources", "Veterans", "Affairs", "Immigration", "Naturalization", "Customs",
    "Article", "Section", "Amendment", "Congress", "Constitution", "Government", "Administration",
    # states / common geographic parties (as a party they are rarely how the decision is later named)
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Cal", "Colorado", "Connecticut", "Delaware", "Florida", "Fla",
    "Georgia", "Hawaii", "Idaho", "Illinois", "Ill", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland",
    "Massachusetts", "Mass", "Michigan", "Mich", "Minnesota", "Minn", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "Hampshire", "Jersey", "Mexico", "York", "Carolina", "Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Penn", "Rhode",
    "Island", "Tennessee", "Tenn", "Texas", "Tex", "Utah", "Vermont", "Virginia", "Washington", "Wash", "Wisconsin", "Wis", "Wyoming",
    "Columbia", "Puerto", "Rico", "America", "Canada", "England", "Britain", "France", "Germany", "Japan", "China", "Mexico",
}
# STOP is checked on the raw token and on the token with its period stripped
STOP |= {w.rstrip(".") for w in STOP}
NAME_WORD = r"(?:\*\d+\s*)?(?:[A-Z][A-Za-z.'’&-]*|&|of|the|and|de|la|du|for|on)"
# "A v. B" or "In re X" immediately before a tag (whitespace/star pages tolerated)
NAME_BEFORE = re.compile(
    r"((?:" + NAME_WORD + r"[\s,]+){1,9})v(?:s)?\.\s+((?:" + NAME_WORD + r"[\s,]*){1,9}?)[\s,]*(?:\((?:\w+\.?\s*){1,4}\))?[\s,]*$"
    r"|((?:In\s+re|Ex\s+parte|Matter\s+of|Estate\s+of)\s+(?:" + NAME_WORD + r"[\s,]*){1,12}?)[\s,]*$"
    r"|([A-Z][A-Za-z.'’-]+[’']s\s+[Cc]ase)[\s,]*$",  # M’Naghten’s case, 10 Clark & Fin. 200
    re.S)
BLANK_CITE = re.compile(r"[\s,]*(?:[—–-]+\s*U\.\s?S\.\s*[—–_-]+|\d{3}\s+U\.\s?S\.\s*_{2,})[\s,]*$")
ABBREV_OK = {"Corp.", "Inc.", "Co.", "Bros.", "Assn.", "Ass'n.", "Dept.", "Hosp.", "Univ.", "Bd.", "Mfg.", "Nat'l.", "Int'l.",
             "Ltd.", "Ry.", "R.R.", "Sav.", "Ins.", "Cas.", "Mut.", "Indus.", "Servs.", "Prods.", "Enters.", "Sec'y.", "Comm'n.",
             "Auth.", "Dist.", "Tel.", "Pub.", "Am.", "Fed.", "Gen.", "St.", "Mt.", "Ft."}
FULL_CITE = re.compile(r"^\s*\d{1,4}\s+[A-Z]")  # a tagged span that starts with a volume number
ID_TOKEN = re.compile(r"\b(?:Id|id|Ibid|ibid)\.(?:,?\s*(?:at|p\.|pp\.)\s*[\d,\s*–-]*\d)?")
SHORT_FORM = re.compile(r"\b\d{1,4}\s+[A-Z][A-Za-z.]*(?:\s?[A-Za-z0-9.]+){0,3}?,?\s+at\s+\d[\d,\s*–-]*\d?")
SUPRA = re.compile(r"(?:" + NAME_WORD + r"[\s,]+){1,6}supra(?:,?\s*at\s+[\d,\s*–-]*\d)?")
# after a name occurrence: only name-ish words / "v." / commas / docket before a tag  => it introduces its own citation
FRAGMENT_AFTER = re.compile(
    r"^(?:[\s,’'s]*(?:\*\d+|" + NAME_WORD + r"|v\.|vs\.|of|the|and|&|for|No\.\s*[\w:-]+|\([A-Za-z0-9.\s]+\))){0,14}[\s,]*(?:<c\b|\d{1,4}\s+[A-Z][A-Za-z.]*\s*\d|\d{2,4}-\d{2,5}\s*\(|__+|—\s*U)")


def _norm(s):
    return WS.sub(" ", STAR.sub("", s)).strip(" ,")


SIGNALS = {"See", "see", "Cf.", "cf.", "Compare", "compare", "But", "but", "E.g.", "e.g.", "Accord", "accord", "Also", "also",
           "Contra", "contra", "In", "in", "Citing", "citing", "Quoting", "quoting", "With", "with", "And", "and", "Under", "under",
           "Following", "following", "Generally", "generally", "The", "the", "A", "An", "Of", "of", "To", "to", "For", "for", "By", "by",
           "As", "as", "At", "at", "On", "on", "From", "from", "Our", "our", "Its", "its", "This", "this", "That", "that", "Both", "both",
           "Then", "then", "Thus", "thus", "Here", "here", "Later", "later", "Earlier", "earlier", "Recently", "recently", "Moreover", "Finally"}


def _cut_sentence(party):
    """Drop everything up to the last sentence-ending token ('New York. United States' -> 'United States')
    and any leading citation signals / function words ('See Steward Machine' -> 'Steward Machine').
    A token ending in '.' is a sentence end unless it is a known abbreviation or an initial."""
    toks = party.split()
    cut = 0
    for i, t in enumerate(toks[:-1]):
        if t.endswith(".") and t not in ABBREV_OK and not re.fullmatch(r"(?:[A-Z]\.)+", t) and len(t) > 3:
            cut = i + 1
    toks = toks[cut:]
    while len(toks) > 1 and (toks[0] in SIGNALS or toks[0].rstrip(".,") in SIGNALS or toks[0] in ("also", "generally")):
        toks = toks[1:]
    return " ".join(toks)


def _short_names(name):
    """Distinctive tokens of a case name, plus the whole normalized name."""
    out = set()
    n = re.sub(r"[’']s\b", "", _norm(name))
    if n:
        out.add(n)
    parts = re.split(r"\s+v(?:s)?\.\s+", n) if re.search(r"\sv(?:s)?\.\s", n) else [n]
    if len(parts) == 2:
        parts[0] = _cut_sentence(parts[0])
        n = f"{parts[0]} v. {parts[1]}"
        out = {n}
    for p in parts:
        toks = [t.strip(",.’'") for t in p.split()]
        toks = [t for t in toks if t and t[0].isupper() and t not in STOP and len(t) >= 3 and not t.endswith(".") and t != "&"]
        # keep hyphenated / multiword distinctive heads: first distinctive token of each party
        if toks:
            out.add(toks[0])
            if len(toks) >= 2 and toks[1] not in STOP and toks[1][0].isupper():
                out.add(f"{toks[0]} {toks[1]}")
    return {s for s in out if len(s) >= 3}


def case_names(text):
    """gid -> set of short names, from the name phrase preceding each tagged full citation."""
    names = {}
    for m in TAG.finditer(text):
        gid, span = m.group(2), m.group(3)
        if not FULL_CITE.match(span):
            continue
        window = text[max(0, m.start() - 260):m.start()]
        window = re.sub(r"<[^>]+>", " ", window)
        window = BLANK_CITE.sub(" ", window)  # `Lopez, — U.S. -, <c>115 S.Ct. 1624</c>`
        nm = NAME_BEFORE.search(window)
        if not nm:
            continue
        if nm.group(3):
            full = nm.group(3)
        elif nm.group(4):
            full = re.sub(r"[’']s\s+[Cc]ase$", "", nm.group(4))
        else:
            full = f"{nm.group(1)} v. {nm.group(2)}"
        for s in _short_names(full):
            names.setdefault(gid, set()).add(s)
    return names


def _segments(text):
    """(start, end) of text outside <c> tags."""
    segs, pos = [], 0
    for m in TAG.finditer(text):
        if m.start() > pos:
            segs.append((pos, m.start()))
        pos = m.end()
    if pos < len(text):
        segs.append((pos, len(text)))
    return segs


def _nearest_group(text, pos):
    last = None
    for m in TAG.finditer(text, 0, pos):
        last = m.group(2)
    return last


def _ctx(text, s, e, w=55):
    return _norm(text[max(0, s - w):s]) + " ⟦" + _norm(text[s:e]) + "⟧ " + _norm(text[e:e + w])


def candidates(text):
    """List of dicts: kind, text, groups, context, pos."""
    out, seen = [], set()
    names = case_names(text)
    name_to_groups = {}
    for gid, ss in names.items():
        for s in ss:
            name_to_groups.setdefault(s, set()).add(gid)
    body_start = max(text.find("<opinion>"), 0)
    segs = [(max(a, body_start), b) for a, b in _segments(text) if b > body_start]
    for name, gids in sorted(name_to_groups.items(), key=lambda kv: -len(kv[0])):
        pat = re.compile(r"(?<![A-Za-z’'])" + re.escape(name).replace(r"\ ", r"\s+") + r"(?:[’']s)?(?![A-Za-z-])")
        for s0, e0 in segs:
            for m in pat.finditer(text, s0, e0):
                s, e = m.start(), m.end()
                if (s, e) in seen or any(s >= a and e <= b for a, b, _ in seen_spans(out)):
                    continue
                after = text[e:e + 220]
                if FRAGMENT_AFTER.match(after):
                    continue  # part of a name that introduces its own citation
                seen.add((s, e))
                out.append({"kind": "name", "text": text[s:e], "groups": sorted(gids), "pos": s, "end": e, "ctx": _ctx(text, s, e)})
    for kind, rx in (("id", ID_TOKEN), ("short_form", SHORT_FORM), ("supra", SUPRA)):
        for s0, e0 in segs:
            for m in rx.finditer(text, s0, e0):
                s, e = m.start(), m.end()
                if (s, e) in seen:
                    continue
                if kind == "short_form" and re.match(r"\s*<c", text[e:e + 3]):
                    continue
                seen.add((s, e))
                g = _nearest_group(text, s)
                out.append({"kind": kind, "text": _norm(text[s:e]), "groups": [g] if g else [], "pos": s, "end": e, "ctx": _ctx(text, s, e)})
    out.sort(key=lambda c: c["pos"])
    return out


def seen_spans(items):
    return [(c["pos"], c["end"], c["kind"]) for c in items]


def candidates_block(text, max_items=600):
    cs = candidates(text)
    if not cs:
        return ""
    lines = [f'<candidates n="{len(cs)}">']
    for i, c in enumerate(cs[:max_items], 1):
        g = ",".join(c["groups"]) or "?"
        lines.append(f'c{i} | {c["kind"]} | "{c["text"]}" | {g} | {c["ctx"]}')
    if len(cs) > max_items:
        lines.append(f"… {len(cs) - max_items} more candidates omitted; enumerate them yourself.")
    lines.append("</candidates>")
    return "\n".join(lines)


if __name__ == "__main__":
    for p in sys.argv[1:]:
        t = open(p, encoding="utf-8").read()
        print(candidates_block(t))
