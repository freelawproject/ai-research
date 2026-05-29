"""Phase 2 postprocess: validate, merge, resolve, log.

For one cluster, given the Phase 1 tagged_artifact + the `CallInput` list from
paginate_and_call + the per-call LLM emissions, produces:

- The grouped JSON output for `data/grouped/{cluster_id}.json` (returned).
- Side effects: appends to audit jsonl logs (untagged, unrouted ids,
  invalid ids, casename warnings). The driver opens these once per batch
  and passes handles in.

Two-pass merge (Pass 1 by shared `group_id`, Pass 2 by canonical
`(mainCitationString, caseName)`) is the central operation; everything else
is validation, resolution, and logging around it. See
`experiments_05192026/citator_pipeline_migration_plan.md` Phase 2.
"""

import json
import re
import unicodedata
from io import TextIOBase

from .paginate_and_call import CallInput


# ── Normalization for Pass 2 canonical-name merge ─────────────────────

def _normalize_canonical(s: str | None) -> str:
    """Lowercase + collapse whitespace + strip trailing punctuation.

    Used only as a merge key; the original strings are kept for output.
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.lower()
    s = re.sub(r"\s+", " ", s).strip()
    s = s.rstrip(".,;: ")
    return s


# ── Validation (per call) ────────────────────────────────────────────

def _casename_in_maincit(case_name: str | None, main_cit: str | None) -> bool:
    """True iff normalized caseName is a substring of normalized
    mainCitationString. Treats null/empty either side as trivially OK."""
    if not case_name or not main_cit:
        return True
    nc = _normalize_canonical(case_name)
    nm = _normalize_canonical(main_cit)
    if not nc:
        return True
    return nc in nm


def validate_call_emissions(
    cited_cases: list[dict],
    call: CallInput,
    tagged_artifact: dict,
    *,
    untagged_log: TextIOBase | None = None,
    invalid_ids_log: TextIOBase | None = None,
    casename_warnings_log: TextIOBase | None = None,
) -> tuple[list[dict], dict]:
    """Validate one call's `cited_cases`. Returns (cleaned_cases, counters).

    Cleaning:
    - `accepted_ids` filtered to ids actually in `id_to_metadata_map`.
      Fabricated ids are dropped and logged to `invalid_ids_log`.
    - Each `untagged_occurrence` snippet is validated by `str.find()` in the
      referenced chunk's `tagged_text`. Matches are kept with a per-opinion
      offset (chunk_start + find_position) and logged as `accepted`; misses
      are dropped and logged as `dropped`.
    - `caseName` is checked against `mainCitationString` (non-blocking — just
      logs to `casename_warnings_log` for review).

    Cleaned cases carry an internal `_validated_untagged` list (with offsets)
    that downstream merge / build_occurrences reads.
    """
    cluster_id = tagged_artifact["cluster_id"]
    id_map = tagged_artifact["id_to_metadata_map"]
    cleaned: list[dict] = []
    counters = {
        "n_invalid_ids": 0,
        "n_untagged_accepted": 0,
        "n_untagged_dropped": 0,
        "n_casename_warnings": 0,
    }

    for cc in cited_cases:
        main_cite = cc.get("mainCitationString")
        case_name = cc.get("caseName")
        parallel_cite = cc.get("parallelCitationString")

        # caseName-vs-mainCit consistency double-check (non-blocking).
        if not _casename_in_maincit(case_name, main_cite):
            counters["n_casename_warnings"] += 1
            if casename_warnings_log is not None:
                casename_warnings_log.write(json.dumps({
                    "cluster_id": cluster_id,
                    "kind": "casename_not_in_maincit",
                    "mainCitationString": main_cite,
                    "caseName": case_name,
                    "message": (
                        f"caseName '{case_name}' is not a substring of "
                        f"mainCitationString '{main_cite}' after normalization "
                        "— the model may have truncated, misnamed, or "
                        "substituted the case name."
                    ),
                }) + "\n")

        # Filter accepted_ids → drop fabricated ones.
        valid_ids: list[int] = []
        for i in (cc.get("accepted_ids") or []):
            if str(i) in id_map:
                valid_ids.append(int(i))
            else:
                counters["n_invalid_ids"] += 1
                if invalid_ids_log is not None:
                    invalid_ids_log.write(json.dumps({
                        "cluster_id": cluster_id,
                        "id": i,
                        "case": {"mainCitationString": main_cite, "caseName": case_name},
                        "reason": "id_not_in_id_to_metadata_map",
                    }) + "\n")

        # Validate untagged_occurrences.
        validated: list[dict] = []
        for occ in (cc.get("untagged_occurrences") or []):
            handle = occ.get("opinion_handle")
            snippet = occ.get("snippet") or ""
            if handle is None or not snippet:
                counters["n_untagged_dropped"] += 1
                if untagged_log is not None:
                    untagged_log.write(json.dumps({
                        "cluster_id": cluster_id,
                        "case": {"mainCitationString": main_cite, "caseName": case_name},
                        "opinion_handle": handle,
                        "source_opinion_id": None,
                        "opinion_type": None,
                        "snippet": snippet,
                        "outcome": "dropped",
                        "drop_reason": "malformed_untagged_occurrence",
                    }) + "\n")
                continue

            try:
                meta = call.meta(handle)
            except IndexError:
                counters["n_untagged_dropped"] += 1
                if untagged_log is not None:
                    untagged_log.write(json.dumps({
                        "cluster_id": cluster_id,
                        "case": {"mainCitationString": main_cite, "caseName": case_name},
                        "opinion_handle": handle,
                        "source_opinion_id": None,
                        "opinion_type": None,
                        "snippet": snippet,
                        "outcome": "dropped",
                        "drop_reason": "invalid_opinion_handle",
                    }) + "\n")
                continue

            chunk_text = meta["tagged_text"]
            chunk_start = meta["chunk_start"]
            source_op_id = meta["source_opinion_id"]
            op_type = meta["opinion_type"]

            pos = chunk_text.find(snippet)
            if pos == -1:
                counters["n_untagged_dropped"] += 1
                if untagged_log is not None:
                    untagged_log.write(json.dumps({
                        "cluster_id": cluster_id,
                        "case": {"mainCitationString": main_cite, "caseName": case_name},
                        "opinion_handle": handle,
                        "source_opinion_id": source_op_id,
                        "opinion_type": op_type,
                        "snippet": snippet,
                        "outcome": "dropped",
                        "drop_reason": "snippet_not_found",
                    }) + "\n")
                continue

            offset_in_opinion = chunk_start + pos
            validated.append({
                "opinion_handle": handle,
                "source_opinion_id": source_op_id,
                "opinion_type": op_type,
                "snippet": snippet,
                "offset": offset_in_opinion,
            })
            counters["n_untagged_accepted"] += 1
            if untagged_log is not None:
                untagged_log.write(json.dumps({
                    "cluster_id": cluster_id,
                    "case": {"mainCitationString": main_cite, "caseName": case_name},
                    "opinion_handle": handle,
                    "source_opinion_id": source_op_id,
                    "opinion_type": op_type,
                    "snippet": snippet,
                    "outcome": "accepted",
                    "offset": offset_in_opinion,
                }) + "\n")

        cleaned.append({
            "mainCitationString": main_cite,
            "parallelCitationString": parallel_cite,
            "caseName": case_name,
            "accepted_ids": valid_ids,
            "_validated_untagged": validated,
            "ocr_corrected": bool(cc.get("ocr_corrected", False)),
            "ocr_note": cc.get("ocr_note"),
        })
    return cleaned, counters


# ── Union-find for two-pass merge ────────────────────────────────────

class _UnionFind:
    """Disjoint-set union over integer keys 0..n-1."""

    def __init__(self, n: int) -> None:
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, x: int, y: int) -> None:
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return
        if self.rank[rx] < self.rank[ry]:
            rx, ry = ry, rx
        self.parent[ry] = rx
        if self.rank[rx] == self.rank[ry]:
            self.rank[rx] += 1

    def groups(self) -> list[list[int]]:
        out: dict[int, list[int]] = {}
        for i in range(len(self.parent)):
            out.setdefault(self.find(i), []).append(i)
        return list(out.values())


def merge_two_pass(
    all_cases: list[dict],
    id_to_metadata_map: dict,
) -> list[list[int]]:
    """Pass 1 (by shared `group_id`, name-aware) then Pass 2 (by canonical name).

    Pass 1 only unions cases whose `caseName`s are *compatible*: same after
    normalization, or one side is null/empty (wildcard). This protects against
    Phase 1 group_ids that eyecite produced by mis-chaining multiple distinct
    cited cases — when the model correctly splits such a group into multiple
    cases with distinct caseNames, Pass 1 leaves the split intact instead of
    force-merging them back.

    Returns a list of merged groups, each a list of indices into `all_cases`.
    """
    from collections import defaultdict
    n = len(all_cases)
    uf = _UnionFind(n)

    # Pass 1: per shared group_id, only union cases with compatible caseNames.
    group_to_cases: dict[str, list[int]] = {}
    for i, cc in enumerate(all_cases):
        case_groups: set[str] = set()
        for id_ in cc["accepted_ids"]:
            meta = id_to_metadata_map.get(str(id_))
            if meta:
                case_groups.add(meta["group_id"])
        for gid in case_groups:
            group_to_cases.setdefault(gid, []).append(i)

    for idxs in group_to_cases.values():
        if len(idxs) <= 1:
            continue
        # Bucket by normalized caseName; null/empty names are wildcards.
        named: dict[str, list[int]] = defaultdict(list)
        unnamed: list[int] = []
        for i in idxs:
            name = all_cases[i].get("caseName")
            key = _normalize_canonical(name) if name else ""
            if key:
                named[key].append(i)
            else:
                unnamed.append(i)
        # Union within each name-bucket.
        for bucket in named.values():
            for j in bucket[1:]:
                uf.union(bucket[0], j)
        # Wildcards: if exactly one named bucket, attach unnamed to it; if no
        # named buckets, union unnamed with each other. Multiple named buckets
        # → leave unnamed alone (ambiguous; Pass 2 may still pick them up).
        if len(named) == 1:
            anchor = next(iter(named.values()))[0]
            for j in unnamed:
                uf.union(anchor, j)
        elif not named:
            for j in unnamed[1:]:
                uf.union(unnamed[0], j)

    # Pass 2: union cases sharing a normalized (mainCitationString, caseName).
    # Skip cases where both are null (no canonical key to match on).
    canon_to_cases: dict[tuple[str, str], list[int]] = {}
    for i, cc in enumerate(all_cases):
        key = (
            _normalize_canonical(cc["mainCitationString"]),
            _normalize_canonical(cc["caseName"]),
        )
        if not key[0] and not key[1]:
            continue
        canon_to_cases.setdefault(key, []).append(i)
    for idxs in canon_to_cases.values():
        for j in idxs[1:]:
            uf.union(idxs[0], j)

    return uf.groups()


# ── Conflict resolution per merged set ──────────────────────────────

def resolve_merged(case_indices: list[int], all_cases: list[dict]) -> dict:
    """Combine multiple cited_case dicts into one.

    Rules (locked 2026-05-27, parallelCitationString added 2026-05-28):
    - `mainCitationString` / `caseName` / `parallelCitationString`: longest
      non-null wins.
    - `accepted_ids`: union, sorted, deduped.
    - `_validated_untagged`: union, deduped by (source_opinion_id, snippet) —
      first accepted wins for the overlap region.
    - `ocr_corrected`: OR.
    - `ocr_note`: non-null notes concatenated with "; ".
    """
    cases = [all_cases[i] for i in case_indices]

    def longest_non_null(values):
        non_null = [v for v in values if v]
        return max(non_null, key=len) if non_null else None

    main_cite = longest_non_null(c["mainCitationString"] for c in cases)
    parallel_cite = longest_non_null(c.get("parallelCitationString") for c in cases)
    case_name = longest_non_null(c["caseName"] for c in cases)

    accepted_ids = sorted({i for c in cases for i in c["accepted_ids"]})

    seen: set[tuple] = set()
    merged_untagged: list[dict] = []
    for c in cases:
        for occ in c["_validated_untagged"]:
            key = (occ["source_opinion_id"], occ["snippet"])
            if key in seen:
                continue
            seen.add(key)
            merged_untagged.append(occ)

    ocr_corrected = any(c["ocr_corrected"] for c in cases)
    notes = [c["ocr_note"] for c in cases if c["ocr_note"]]
    ocr_note = "; ".join(notes) if notes else None

    return {
        "mainCitationString": main_cite,
        "parallelCitationString": parallel_cite,
        "caseName": case_name,
        "accepted_ids": accepted_ids,
        "_validated_untagged": merged_untagged,
        "ocr_corrected": ocr_corrected,
        "ocr_note": ocr_note,
    }


def resolve_id_conflicts(
    merged_cases: list[dict],
    id_to_metadata_map: dict,
    *,
    cluster_id: int,
    id_conflicts_log: TextIOBase | None = None,
) -> tuple[list[dict], int]:
    """Detect ids assigned to multiple merged_cases; strip from losers.

    Under the new chunked-call architecture, the same id can appear in two
    different cited_cases when adjacent chunks (overlap region) disagree
    about which case the id refers to, and Pass 1/2 don't merge those cases
    (genuinely different canonical names). This function resolves the
    conflict deterministically.

    Winner rule per conflicting id:
        1. Most total occurrences (accepted_ids + untagged) — more context.
        2. Tie-break: longest non-null mainCitationString.
        3. Final tie-break: lowest case index (stable).

    The id stays in the winner; it's stripped from every loser. A loser
    that ends up with zero accepted_ids AND zero untagged is dropped
    entirely.

    Returns (cleaned_cases, n_conflicts_resolved). Conflicts are logged
    one-per-id to `id_conflicts_log` if provided.
    """
    from collections import defaultdict
    id_appearances: dict[int, list[int]] = defaultdict(list)
    for i, mc in enumerate(merged_cases):
        for id_ in mc["accepted_ids"]:
            id_appearances[id_].append(i)

    conflicts = {id_: idxs for id_, idxs in id_appearances.items() if len(idxs) > 1}
    if not conflicts:
        return merged_cases, 0

    def score(idx: int) -> tuple:
        mc = merged_cases[idx]
        n_occ = len(mc["accepted_ids"]) + len(mc["_validated_untagged"])
        main_cit_len = len(mc["mainCitationString"] or "")
        return (n_occ, main_cit_len, -idx)

    id_to_remove_per_case: dict[int, set[int]] = defaultdict(set)
    n_conflicts = 0
    for id_, candidate_idxs in conflicts.items():
        winner = max(candidate_idxs, key=score)
        losers = [i for i in candidate_idxs if i != winner]
        for loser_idx in losers:
            id_to_remove_per_case[loser_idx].add(id_)
        n_conflicts += 1
        if id_conflicts_log is not None:
            meta = id_to_metadata_map.get(str(id_), {})
            id_conflicts_log.write(json.dumps({
                "cluster_id": cluster_id,
                "id": id_,
                "citation_string": meta.get("citation_string"),
                "group_id": meta.get("group_id"),
                "winner": {
                    "mainCitationString": merged_cases[winner].get("mainCitationString"),
                    "caseName": merged_cases[winner].get("caseName"),
                    "n_occurrences": (
                        len(merged_cases[winner]["accepted_ids"])
                        + len(merged_cases[winner]["_validated_untagged"])
                    ),
                },
                "losers": [{
                    "mainCitationString": merged_cases[i].get("mainCitationString"),
                    "caseName": merged_cases[i].get("caseName"),
                    "n_occurrences": (
                        len(merged_cases[i]["accepted_ids"])
                        + len(merged_cases[i]["_validated_untagged"])
                    ),
                } for i in losers],
            }) + "\n")

    cleaned: list[dict] = []
    for i, mc in enumerate(merged_cases):
        to_remove = id_to_remove_per_case.get(i, set())
        if not to_remove:
            cleaned.append(mc)
            continue
        new_ids = [id_ for id_ in mc["accepted_ids"] if id_ not in to_remove]
        if not new_ids and not mc["_validated_untagged"]:
            continue
        cleaned.append({**mc, "accepted_ids": new_ids})
    return cleaned, n_conflicts


def _restore_unrouted_ids(
    merged_cases: list[dict],
    id_to_metadata_map: dict,
    *,
    cluster_id: int,
    unrouted_log: TextIOBase | None = None,
) -> int:
    """For each Phase 1 id the model omitted, attach to a merged_case
    sharing its `group_id`; else create a solo case with null names.

    Mutates `merged_cases` in place (existing cases may gain ids; solo
    cases may be appended). Returns the count of ids restored.

    Sibling pick rule: when multiple merged_cases touch the same group_id
    (e.g., chimera-fix kept them split by name), prefer the case with the
    most total occurrences. Tie-break on longest mainCitationString.
    """
    accepted = {i for mc in merged_cases for i in mc["accepted_ids"]}
    unrouted = [
        int(id_str) for id_str in id_to_metadata_map.keys()
        if int(id_str) not in accepted
    ]
    if not unrouted:
        return 0

    def case_score(idx: int) -> tuple:
        mc = merged_cases[idx]
        n_occ = len(mc["accepted_ids"]) + len(mc["_validated_untagged"])
        main_cit_len = len(mc["mainCitationString"] or "")
        return (n_occ, main_cit_len)

    # group_id → best merged_case index sharing that group.
    group_to_case_idx: dict[str, int] = {}
    for i, mc in enumerate(merged_cases):
        case_groups: set[str] = set()
        for id_ in mc["accepted_ids"]:
            meta = id_to_metadata_map.get(str(id_))
            if meta:
                case_groups.add(meta["group_id"])
        for gid in case_groups:
            cur = group_to_case_idx.get(gid)
            if cur is None or case_score(i) > case_score(cur):
                group_to_case_idx[gid] = i

    n_restored = 0
    for rid in unrouted:
        rid_meta = id_to_metadata_map[str(rid)]
        gid = rid_meta["group_id"]
        target = group_to_case_idx.get(gid)
        if target is not None:
            mc = merged_cases[target]
            mc["accepted_ids"] = sorted(set(mc["accepted_ids"]) | {rid})
            fallback = "group_id_sibling"
        else:
            merged_cases.append({
                "mainCitationString": None,
                "parallelCitationString": None,
                "caseName": None,
                "accepted_ids": [rid],
                "_validated_untagged": [],
                "ocr_corrected": False,
                "ocr_note": None,
            })
            # Subsequent unrouted ids in the same group attach here.
            group_to_case_idx[gid] = len(merged_cases) - 1
            fallback = "solo_from_phase1"
        n_restored += 1
        if unrouted_log is not None:
            unrouted_log.write(json.dumps({
                "cluster_id": cluster_id,
                "id": rid,
                "opinion_type": rid_meta["opinion_type"],
                "source_opinion_id": rid_meta["source_opinion_id"],
                "citation_string": rid_meta["citation_string"],
                "group_id": gid,
                "fallback": fallback,
            }) + "\n")
    return n_restored


def detect_casename_disagreements(
    merged_index_groups: list[list[int]],
    all_cases: list[dict],
) -> list[dict]:
    """For each merged group, return a warning record when the constituent
    cases' (non-null) caseNames disagree after normalization."""
    warnings: list[dict] = []
    for group_indices in merged_index_groups:
        if len(group_indices) <= 1:
            continue
        cases = [all_cases[i] for i in group_indices]
        names = [c["caseName"] for c in cases if c["caseName"]]
        if len(names) <= 1:
            continue
        normalized = {_normalize_canonical(n) for n in names}
        if len(normalized) > 1:
            warnings.append({
                "caseNames": names,
                "mainCitationStrings": [c["mainCitationString"] for c in cases],
            })
    return warnings


# ── Build final occurrences ────────────────────────────────────────

def build_occurrences(merged_case: dict, id_to_metadata_map: dict) -> list[dict]:
    """Resolve `accepted_ids` + `_validated_untagged` into a unified occurrences list.

    Tagged occurrences carry `source: "tagged"`; untagged carry `source: "untagged"`.
    Sorted by `(source_opinion_id, offset)` for stable output.
    """
    occurrences: list[dict] = []
    for id_ in merged_case["accepted_ids"]:
        meta = id_to_metadata_map[str(id_)]
        occurrences.append({
            "opinion_type": meta["opinion_type"],
            "source_opinion_id": meta["source_opinion_id"],
            "offset": meta["offset"],
            "citation_string": meta["citation_string"],
            "source": "tagged",
        })
    for occ in merged_case["_validated_untagged"]:
        occurrences.append({
            "opinion_type": occ["opinion_type"],
            "source_opinion_id": occ["source_opinion_id"],
            "offset": occ["offset"],
            "citation_string": occ["snippet"],
            "source": "untagged",
        })
    occurrences.sort(key=lambda o: (o["source_opinion_id"], o["offset"]))
    return occurrences


# ── Public entry point ─────────────────────────────────────────────

def postprocess(
    tagged_artifact: dict,
    calls: list[CallInput],
    emissions_by_call: list[dict],
    *,
    untagged_log: TextIOBase | None = None,
    unrouted_log: TextIOBase | None = None,
    invalid_ids_log: TextIOBase | None = None,
    casename_warnings_log: TextIOBase | None = None,
    id_conflicts_log: TextIOBase | None = None,
) -> dict:
    """Postprocess all calls for one cluster into the final grouped output.

    Args:
        tagged_artifact: Phase 1 tagged artifact dict (cluster_id, opinions,
            id_to_metadata_map, groups, …).
        calls: list of `CallInput` from paginate_and_call.
        emissions_by_call: list of LLM output dicts, one per call. Each dict
            looks like `{"cited_cases": [...]}` per the citation_grouping
            schema. Same length and order as `calls`.
        untagged_log / unrouted_log / invalid_ids_log: open file handles
            opened in append mode; `None` to skip a log.

    Returns the dict to write to `data/grouped/{cluster_id}.json`.
    """
    cluster_id = tagged_artifact["cluster_id"]
    id_map = tagged_artifact["id_to_metadata_map"]

    # Step 1 — validate per-call.
    all_cases: list[dict] = []
    totals = {
        "n_invalid_ids": 0,
        "n_untagged_accepted": 0,
        "n_untagged_dropped": 0,
        "n_casename_warnings": 0,
    }
    for call, emission in zip(calls, emissions_by_call):
        cited_cases = (emission or {}).get("cited_cases") or []
        cleaned, counters = validate_call_emissions(
            cited_cases, call, tagged_artifact,
            untagged_log=untagged_log,
            invalid_ids_log=invalid_ids_log,
            casename_warnings_log=casename_warnings_log,
        )
        all_cases.extend(cleaned)
        for k, v in counters.items():
            totals[k] += v

    # Step 2 — two-pass merge.
    merged_index_groups = merge_two_pass(all_cases, id_map)

    # Step 2b — caseName disagreements among merged cases (non-blocking).
    for warning in detect_casename_disagreements(merged_index_groups, all_cases):
        totals["n_casename_warnings"] += 1
        if casename_warnings_log is not None:
            names = warning.get("caseNames", [])
            main_cites = warning.get("mainCitationStrings", [])
            primary_mc = max(main_cites, key=len, default=None) if main_cites else None
            casename_warnings_log.write(json.dumps({
                "cluster_id": cluster_id,
                "kind": "merged_casenames_disagree",
                "mainCitationString": primary_mc,
                "caseNames": names,
                "mainCitationStrings": main_cites,
                "message": (
                    "Two or more emissions of the same case (merged via canonical "
                    "citation match) gave different caseNames: "
                    + ", ".join(repr(n) for n in names)
                    + " — the model was internally inconsistent about the name."
                ),
            }) + "\n")

    merged_cases = [resolve_merged(g, all_cases) for g in merged_index_groups]

    # Step 2c — resolve id conflicts. Same id assigned to multiple merged
    # cases (typically two chunks of overlap region disagreeing on which
    # case the id refers to). Pick a winner; strip from losers; log.
    merged_cases, n_id_conflicts = resolve_id_conflicts(
        merged_cases,
        id_map,
        cluster_id=cluster_id,
        id_conflicts_log=id_conflicts_log,
    )

    # Step 2d — fallback for ids the model omitted. Every Phase 1 id must
    # land in some cited_case. For an unrouted id, attach to the merged
    # case sharing its group_id (Phase 1 grouping is authoritative for the
    # cited case); fall back to a solo cited_case with null names if no
    # sibling exists. Logged to `unrouted_log` with fallback kind.
    n_unrouted_ids = _restore_unrouted_ids(
        merged_cases,
        id_map,
        cluster_id=cluster_id,
        unrouted_log=unrouted_log,
    )

    # Step 3 — build final occurrences. Drop any merged case that ended up with
    # zero occurrences (no valid accepted_ids and no validated untagged).
    final_cases: list[dict] = []
    for mc in merged_cases:
        occurrences = build_occurrences(mc, id_map)
        if not occurrences:
            continue
        final_cases.append({
            "mainCitationString": mc["mainCitationString"],
            "parallelCitationString": mc["parallelCitationString"],
            "caseName": mc["caseName"],
            "occurrences": occurrences,
            "ocr_corrected": mc["ocr_corrected"],
            "ocr_note": mc["ocr_note"],
        })

    # Stable output ordering by first occurrence.
    final_cases.sort(key=lambda c: (
        c["occurrences"][0]["source_opinion_id"],
        c["occurrences"][0]["offset"],
    ))

    n_occurrences = sum(len(c["occurrences"]) for c in final_cases)
    return {
        "cluster_id": cluster_id,
        "cited_cases": final_cases,
        "n_cases": len(final_cases),
        "n_occurrences": n_occurrences,
        "n_untagged_accepted": totals["n_untagged_accepted"],
        "n_untagged_dropped": totals["n_untagged_dropped"],
        "n_invalid_ids": totals["n_invalid_ids"],
        "n_casename_warnings": totals["n_casename_warnings"],
        "n_id_conflicts": n_id_conflicts,
        "n_unrouted_ids": n_unrouted_ids,
    }
