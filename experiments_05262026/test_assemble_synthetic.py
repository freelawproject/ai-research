"""Local validation of assemble_tagged_text.py against synthetic html_with_citations.

docker/CLReplica is unavailable in this env, so this exercises Phase 1's logic on
hand-built input covering: full/short/Id/supra cites sharing a data-id (grouping),
no-link unmatched, multiple-matches, paragraph/<br> structure, cross-opinion shared
group, and the UnmatchedCitation safety-net add/skip/miss paths.

Usage:  python test_assemble_synthetic.py
"""

import os
import sys

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "citator-pipeline")
)

from utils.assemble_tagged_text import assemble_cluster  # noqa: E402

# CL's real wrapper shapes (from cl/citations/annotate_citations.py):
#   matched:          <span class="citation" data-id="PK"><a ...>TEXT</a></span>
#   no-link:          <span class="citation no-link">TEXT</span>
#   multiple-matches: <span class="citation multiple-matches"><a ...>TEXT</a></span>
LEAD_HTML = (
    '<p>The foundational case is '
    '<span class="citation" data-id="100">'
    '<a href="/x" aria-description="Citation for case: Marbury v. Madison">'
    "Marbury v. Madison, 5 U.S. 137</a></span>"
    ", decided in 1803. See also "
    '<span class="citation no-link">Smith v. Jones, 1 F.2d 1</span>'
    " (an unmatched cite).</p>"
    "<p>As the Court reiterated, "
    '<span class="citation" data-id="100"><a href="/x">Marbury, 5 U.S. at 145</a></span>'
    ", the province of the judiciary. <em>See</em> "
    '<span class="citation" data-id="100"><a href="/x">Id.</a></span>'
    " at 147.<br>A later passage cites "
    '<span class="citation multiple-matches"><a href="/cr">Doe v. Roe</a></span>'
    " ambiguously.</p>"
    "<p>Finally, Brown v. Board, 347 U.S. 483, governs — but CL never wrapped "
    "that one (drift), so the safety net must add it.</p>"
)

DISSENT_HTML = (
    "<p>I dissent. The majority misreads "
    '<span class="citation" data-id="100"><a href="/x">Marbury, supra</a></span>'
    ". The real rule comes from "
    '<span class="citation" data-id="200"><a href="/y">Erie R.R. Co. v. Tompkins, 304 U.S. 64</a></span>'
    ".</p>"
)

CL_FETCH = {
    "cluster_id": 999001,
    "opinions": [
        # Deliberately out of reading order to test the sort (dissent before lead).
        {"id": 6790, "type": "040dissent", "html_with_citations": DISSENT_HTML},
        {"id": 6789, "type": "020lead", "html_with_citations": LEAD_HTML},
        {"id": 6791, "type": "030concurrence", "html_with_citations": ""},  # blank
    ],
    "unmatched_citations": [
        # Drift add: present in lead text but not wrapped → safety net adds it.
        {"id": 11, "citing_opinion_id": 6789, "citation_string": "Brown v. Board, 347 U.S. 483"},
        # Already captured: overlaps the no-link Smith span → skipped, not added.
        {"id": 12, "citing_opinion_id": 6789, "citation_string": "Smith v. Jones, 1 F.2d 1"},
        # Genuine miss: text not present anywhere in the opinion → counted as miss.
        {"id": 13, "citing_opinion_id": 6789, "citation_string": "Nonexistent v. Phantom, 9 U.S. 9"},
    ],
}


def main() -> None:
    art = assemble_cluster(CL_FETCH)

    failures: list[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            failures.append(msg)

    # --- Reading order: lead before dissent; blank concurrence excluded. ---
    op_types = [o["opinion_type"] for o in art["opinions"]]
    check(op_types == ["020lead", "040dissent"], f"opinion order wrong: {op_types}")
    check(art["empty_html"] == ["030concurrence"], f"empty_html wrong: {art['empty_html']}")
    # source_opinion_id present + correct (lead=6789, dissent=6790).
    op_ids = {o["opinion_type"]: o["source_opinion_id"] for o in art["opinions"]}
    check(op_ids == {"020lead": 6789, "040dissent": 6790}, f"source_opinion_id wrong: {op_ids}")

    # --- Offsets grep-correct: each id's offset points at its <cited> tag, and
    #     the citation_string follows the opening tag verbatim. Key by
    #     source_opinion_id (unique), not opinion_type. ---
    tagged_by_opinion = {o["source_opinion_id"]: o["tagged_text"] for o in art["opinions"]}
    for cid, meta in art["id_to_metadata_map"].items():
        text = tagged_by_opinion[meta["source_opinion_id"]]
        off = meta["offset"]
        open_tag = f'<cited id="{cid}" group="{meta["group_id"]}">'
        check(
            text[off : off + len(open_tag)] == open_tag,
            f"id {cid}: offset {off} not at opening tag (got {text[off:off+len(open_tag)]!r})",
        )
        after = off + len(open_tag)
        cstr = meta["citation_string"]
        check(
            text[after : after + len(cstr)] == cstr,
            f"id {cid}: citation_string not after tag (got {text[after:after+len(cstr)]!r})",
        )

    # --- IDs sequential 0..n_tags-1. ---
    ids = sorted(int(k) for k in art["id_to_metadata_map"])
    check(ids == list(range(art["n_tags"])), f"ids not sequential 0..{art['n_tags']-1}: {ids}")

    # --- Grouping: Marbury full+short+Id (lead) + supra (dissent) share ONE group;
    #     Erie its own; Smith (no-link), Doe (multiple-matches), Brown (safety-net)
    #     each solo. ---
    cite_to_group = {
        meta["citation_string"]: meta["group_id"]
        for meta in art["id_to_metadata_map"].values()
    }
    marbury_groups = {
        cite_to_group[c]
        for c in [
            "Marbury v. Madison, 5 U.S. 137",
            "Marbury, 5 U.S. at 145",
            "Id.",
            "Marbury, supra",
        ]
    }
    check(len(marbury_groups) == 1, f"Marbury family not one group: {marbury_groups}")
    marbury_group = next(iter(marbury_groups))
    for solo in ["Smith v. Jones, 1 F.2d 1", "Doe v. Roe", "Brown v. Board, 347 U.S. 483",
                 "Erie R.R. Co. v. Tompkins, 304 U.S. 64"]:
        check(cite_to_group[solo] != marbury_group, f"{solo!r} wrongly in Marbury group")
    # Each non-Marbury cite is in a distinct group (all solo / distinct data-id).
    non_marbury = [g for c, g in cite_to_group.items() if g != marbury_group]
    check(len(non_marbury) == len(set(non_marbury)), f"non-Marbury groups collide: {non_marbury}")

    # --- Safety net: Brown added, Smith skipped (not double-counted), 1 miss. ---
    all_cites = [m["citation_string"] for m in art["id_to_metadata_map"].values()]
    check("Brown v. Board, 347 U.S. 483" in all_cites, "Brown not added by safety net")
    check(all_cites.count("Smith v. Jones, 1 F.2d 1") == 1, "Smith double-counted")
    check(art["n_unmatched_misses"] == 1, f"expected 1 miss, got {art['n_unmatched_misses']}")

    # --- n_tags: Marbury x3 + Smith + Doe + Brown (lead=6) + supra + Erie (dissent=2) = 8 ---
    check(art["n_tags"] == 8, f"expected 8 tags, got {art['n_tags']}")

    # --- No raw HTML leaked into tagged_text (only <cited> tags allowed). ---
    for o in art["opinions"]:
        t = o["tagged_text"]
        check("<span" not in t and "<a " not in t and "<p>" not in t and "<em>" not in t,
              f"raw HTML leaked into {o['opinion_type']} tagged_text")

    tagged_by_type = {o["opinion_type"]: o["tagged_text"] for o in art["opinions"]}
    print("=== tagged_text (020lead) ===")
    print(tagged_by_type["020lead"])
    print()
    print("=== tagged_text (040dissent) ===")
    print(tagged_by_type["040dissent"])
    print()
    print("=== groups ===", art["groups"])
    print("=== n_tags ===", art["n_tags"], " n_unmatched_misses ===", art["n_unmatched_misses"])
    print("=== empty_html ===", art["empty_html"])
    print()

    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print("  -", f)
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
