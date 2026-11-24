"""Check the accepted labels against the annotation guidelines.

    uv run python check_gold.py            # summary and up to 12 examples per check
    uv run python check_gold.py --all      # every example

Read-only. Each check looks for one way a label can break a rule in
../annotation_guidelines.md, or disagree with the other field of the same
record. A hit is a label to look at, not always an error: the checks are text
rules and cannot read a caption.
"""

import argparse
import json
import re
from collections import Counter, defaultdict

import store

# A role label standing alone after a comma, as in "John Smith, Plaintiff".
ROLE = re.compile(
    r",\s*(plaintiffs?|defendants?|debtors?|petitioners?|respondents?|appell(?:ant|ee)s?)[,.;]?\s*$",
    re.I,
)
OFFICE = re.compile(
    r" v\. (?:Warden|Director|Administrator|Superintendent|Sheriff|Facility)[^,(]*((?:,[^,()]*)*)"
)


def case_count(value: str) -> int:
    return len(
        [p for p in (value or "").split(";") if re.search(r"\bv\.|\bin re\b", p, re.I)]
    )


def is_adversary(row: dict) -> bool:
    """A bankruptcy-court caption with both an 'In re' case and a lawsuit."""
    full = row["full"]
    return (
        row["jurisdiction"] == "FB"
        and bool(re.match(r"in re", full, re.I))
        and ";" in full
        and " v. " in full
    )


def load(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT r.cluster_id, r.split, r.stratum, r.doc_type, r.plain_text, r.jurisdiction,
               r.docket_case_name,
               p.case_name_full AS opus_full, p.case_name AS opus_short,
               p.window, p.caption_in_input,
               f.value AS full, f.status AS full_status, f.flags AS full_flags,
               s.value AS short, s.status AS short_status, s.flags AS short_flags,
               (SELECT case_name_full FROM suggestions g
                 WHERE g.cluster_id = r.cluster_id AND g.source = ?) AS sol_full
        FROM records r
        JOIN preannotations p USING (cluster_id)
        LEFT JOIN field_labels f ON f.cluster_id = r.cluster_id AND f.field = 'full'
        LEFT JOIN field_labels s ON s.cluster_id = r.cluster_id AND s.field = 'short'
        ORDER BY r.position
        """,
        (store.SECOND_MODEL,),
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["full_flags"] = json.loads(item["full_flags"] or "[]")
        item["short_flags"] = json.loads(item["short_flags"] or "[]")
        out.append(item)
    return out


def checks(rows: list[dict]) -> dict[str, list[tuple]]:
    """Every check, as name -> list of (cluster_id, detail)."""
    found = defaultdict(list)
    by_full = defaultdict(list)

    for r in rows:
        cid, full, short = r["cluster_id"], r["full"], r["short"]
        if full is None or short is None:
            found["01 A field has no label"].append(
                (cid, f"full={r['full_status']} short={r['short_status']}")
            )
            continue
        if "skipped" in (r["full_status"], r["short_status"]):
            found["02 A field is marked skipped"].append(
                (cid, f"full={r['full_status']} short={r['short_status']}")
            )
        flags = r["full_flags"]

        # --- the two fields of one record
        if bool(full) != bool(short):
            found["03 One name is empty and the other is not"].append(
                (cid, f"full={full[:50]!r} short={short[:50]!r}")
            )
        if not full and not ({"no_caption", "caption_beyond_input"} & set(flags)):
            found["04 Empty caption without a flag that explains it"].append(
                (cid, str(flags))
            )
        if full and "no_caption" in flags:
            found["05 'No case name' flag on a caption that has a name"].append(
                (cid, full[:60])
            )
        if (
            not full
            and "caption_beyond_input" in flags
            and r["window"] == store.WIDE_WINDOW_CHARS
            and r["opus_full"]
        ):
            found[
                "06 Saved empty, but the 8,000-character read found a caption"
            ].append((cid, r["opus_full"][:70]))
        if (
            not full
            and "no_caption" in flags
            and "no_caption" not in r["short_flags"]
            and not short
        ):
            found["07 'No case name' on the caption but not on the short name"].append(
                (cid, str(r["short_flags"]))
            )

        if not full:
            continue
        by_full[store.comparable(full)].append(r)

        # --- copy the text exactly
        stray = sorted(
            store.words(full) - store.words(r["plain_text"]) - store.IGNORED_WORDS
        )
        if stray:
            found["08 Caption has a word that is nowhere in the document"].append(
                (cid, ", ".join(stray[:6]))
            )
        if full != " ".join(full.split()) or re.search(r"[,;:]$", full):
            found["09 Caption has stray spacing or ends in punctuation"].append(
                (cid, repr(full[-25:]))
            )
        if re.search(r"\bvs\.?\s|\bversus\b|-against-", full, re.I):
            found["10 Connector not written as 'v.'"].append((cid, full[:70]))
        if ROLE.search(full):
            found["11 Caption ends with a stand-alone role label"].append(
                (cid, full[-50:])
            )

        # --- every case printed
        best = max(case_count(r["opus_full"]), case_count(r["sol_full"]))
        if best > case_count(full):
            found["12 Fewer cases than a model found in the caption"].append(
                (cid, f"label {case_count(full)}, model {best}")
            )

        # --- a 'v.' with a party missing
        if short:
            if full.endswith(" v.") != short.endswith(" v."):
                found["13 Trailing 'v.' on one field only"].append(
                    (cid, f"{full[-30:]!r} / {short!r}")
                )
            if full.startswith("v. ") != short.startswith("v. "):
                found["14 Leading 'v.' on one field only"].append(
                    (cid, f"{full[:30]!r} / {short!r}")
                )

        if not short:
            continue
        # --- short-name rules
        if is_adversary(r) and "(In re " not in short:
            found["15 Adversary caption, short name without '(In re Debtor)'"].append(
                (cid, short[:80])
            )
        if re.search(r"\bBank\b[^(),]*,? N\.A\.", short):
            found["16 'N.A.' kept after a name with 'Bank'"].append((cid, short[:80]))
        if re.search(
            r"(^| v\. )U\.S\.A\.?( v\. |$| \()|United States of America", short
        ):
            found["17 'U.S.A.' or 'United States of America' in a short name"].append(
                (cid, short[:80])
            )
        if re.search(r"^[^()]*? ex rel\. United States", short):
            found["18 '[person] ex rel. United States' order"].append((cid, short[:80]))
        office = OFFICE.search(short)
        if office and len([p for p in office.group(1).split(",") if p.strip()]) > 1:
            found["19 Office with place details after the institution"].append(
                (cid, short[:100])
            )
        if re.search(r"\bet\.? ?al\b", short, re.I):
            found["20 'et al.' in a short name"].append((cid, short[:80]))
        if re.match(r"in re:", short, re.I):
            found["21 'In re:' with a colon in a short name"].append((cid, short[:80]))
        loose = sorted(
            store.words(short)
            - store.words(full)
            - store.IGNORED_WORDS
            - {"united", "states"}
        )
        if loose:
            found["22 Short name has a word that is not in the caption"].append(
                (cid, f"{', '.join(loose[:5])} | {short[:60]}")
            )
        if re.search(r"(^| v\. )[A-Z]\.?( v\. |$| \()", short):
            found["23 A party reduced to a single initial"].append((cid, short[:80]))

    # --- the same caption labeled two ways
    for group in by_full.values():
        shorts = {store.comparable(r["short"] or "") for r in group}
        if len(group) > 1 and len(shorts) > 1:
            found["24 Same caption, different short names"].append(
                (
                    group[0]["cluster_id"],
                    " | ".join(sorted({(r["short"] or "")[:45] for r in group})),
                )
            )
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--all", action="store_true", help="print every example")
    args = parser.parse_args()

    conn = store.connect()
    rows = load(conn)
    print(f"Records: {len(rows)}")
    for field in ("full", "short"):
        print(
            f"  {field:<6}",
            dict(Counter(r[f"{field}_status"] or "no label" for r in rows)),
        )
    complete = [
        r
        for r in rows
        if r["full_status"] in ("auto", "reviewed")
        and r["short_status"] in ("auto", "reviewed")
    ]
    print(
        f"Both names accepted: {len(complete)}",
        dict(Counter(r["split"] for r in complete)),
    )
    print(
        "Flags on full captions:",
        dict(Counter(f for r in rows for f in r["full_flags"])),
    )
    print(
        f"Fields still held for review: {conn.execute('SELECT count(*) FROM review_holds').fetchone()[0]}"
    )

    found = checks(rows)
    print(f"\n{sum(len(v) for v in found.values())} hits across {len(found)} checks\n")
    for name in sorted(found):
        hits = found[name]
        print(f"{name}: {len(hits)}")
        for cid, detail in hits if args.all else hits[:12]:
            print(f"    {cid}  {detail}")


if __name__ == "__main__":
    main()
