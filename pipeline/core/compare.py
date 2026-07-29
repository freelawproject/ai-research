"""Compare + resolve: find where the normalized streams disagree and
resolve every disagreement.

The comparison works ONLY on the canonical unit keys (normalization
output): the two (or three) key streams are diffed, and each run of
disagreeing units is one DISPUTE. Provenance replaces reverse-matching —
a dispute maps to its main-engine block(s) by looking up its tokens'
`item` (plus the second fragment of a wrap-joined word, so a wrapped
dispute crops BOTH source bboxes). There is no heuristic box attribution
anywhere.

This module owns what every route shares: building the streams,
dispatching to a resolution VARIANT, pairing reorders, reporting the
marks flow, and the page metrics. The two variants are separate and
self-contained, so either can be dropped without unpicking the other:

  - `tiebreak.py` — a third model re-reads the disputed region's CROP
    (LightOn), with gates, degeneration guards and one retry;
  - `vote.py` — a third OCR model's whole-page output votes directly,
    2-of-3, no crop.

Empty-vs-empty is agreement by construction (no units, no diff). Image
blocks contribute no tokens (reconstruction/normalization), so they can
never dispute. Footnote MARKS are not comparison content: each
supplemental's mark sequence is compared against the main's in its own
flow and reported.

The resolution parameters are stamped into every compare stage record.
"""

from __future__ import annotations

import difflib
from collections import Counter

from pipeline.core import streams, tiebreak, vote
from pipeline.core.tiebreak import CropReader

# ── shared resolution parameters (stamped into artifacts) ─────────────────

# A low-confidence dispute is HIGH RISK when the disputed span is longer
# than this many UNITS (normalized tokens) on ANY content side — main or
# a supplemental. Measured in units, not characters: a unit is what the
# comparison actually resolves, so the count says how much unresolved
# READING is at stake rather than how wide it happens to be set.
HIGH_RISK_UNITS = 10
# A REORDER is a paired deletion + insertion of near-identical text
# (both streams carry the content, they just disagree on where it
# belongs — e.g. physical vs semantic footnote order): both disputes
# resolve to the main's placement and are not low-confidence. Pairs
# need this key-list similarity and at least this
# many units on both sides.
REORDER_SIM = 0.9
REORDER_MIN_UNITS = 5


def params() -> dict:
    """Every parameter this stage ran under: the shared ones plus each
    variant's own (a variant with no parameters contributes none)."""
    return {
        **tiebreak.params(),
        "high_risk_units": HIGH_RISK_UNITS,
        "reorder_sim": REORDER_SIM,
        "reorder_min_units": REORDER_MIN_UNITS,
    }


def _mark_reorders(disputes: list[dict]) -> None:
    """Pair a deletion (main has text, a supplemental has none) with
    an insertion of near-identical text elsewhere: the content exists
    in both streams, the disagreement is only WHERE it belongs. Both
    disputes become resolution="reorder" — the main's placement is
    kept, nothing is inserted, and neither is low-confidence. On vote
    routes the pairing only applies when every OTHER supplemental
    agrees with the main side (otherwise a real disagreement would be
    hidden behind the move)."""
    labels = {
        k for d in disputes for k in d["reads"] if k not in ("main", "lighton")
    }

    def _clean(d: dict, label: str, deletion: bool) -> bool:
        for k, v in d["reads"].items():
            if k in ("main", "lighton", label):
                continue
            if v != (d["reads"]["main"] if deletion else ""):
                return False
        return True

    for label in sorted(labels):
        dels = [
            d
            for d in disputes
            if d["reads"]["main"]
            and label in d["reads"]
            and not d["reads"][label]
            and _clean(d, label, deletion=True)
        ]
        inss = [
            d
            for d in disputes
            if not d["reads"]["main"]
            and d["reads"].get(label)
            and _clean(d, label, deletion=False)
        ]
        used: set[int] = set()
        for dd in dels:
            a = dd["reads"]["main"].split()
            if len(a) < REORDER_MIN_UNITS:
                continue
            best: tuple[float, dict] | None = None
            for di in inss:
                if id(di) in used:
                    continue
                b = di["reads"][label].split()
                if len(b) < REORDER_MIN_UNITS:
                    continue
                r = difflib.SequenceMatcher(a=a, b=b, autojunk=False).ratio()
                if r >= REORDER_SIM and (best is None or r > best[0]):
                    best = (r, di)
            if best is not None:
                used.add(id(best[1]))
                for d in (dd, best[1]):
                    d.update(
                        verdict="main",
                        resolution="reorder",
                        reason=None,
                        low_confidence=False,
                    )


def _is_high_risk(d: dict) -> bool:
    """Low-confidence AND the disputed span exceeds HIGH_RISK_UNITS on
    some content side. The spans are counted, not the read strings: each
    side already carries its unit range (the main stream's own, and each
    supplemental's aligned projection), and a crop read is a voter
    rather than a side, so it is not one of them."""
    if not d["low_confidence"]:
        return False
    i1, i2 = d["main_span"]
    longest = max(
        [i2 - i1] + [j2 - j1 for j1, j2 in d.get("spans", {}).values()]
    )
    return longest > HIGH_RISK_UNITS


# ── marks flow (report-only: marks are never comparison content) ─────────


def _mark_sequence(tokens: list[dict]) -> list[str]:
    return [m for t in tokens for m in t.get("marks", ())]


def _marks_report(
    main_tokens: list[dict], supps: list[tuple[str, list[dict]]]
) -> list[dict]:
    main_marks = _mark_sequence(main_tokens)
    out = []
    for label, tokens in supps:
        supp_marks = _mark_sequence(tokens)
        diffs = [
            {"main": main_marks[i1:i2], "supp": supp_marks[j1:j2]}
            for tag, i1, i2, j1, j2 in streams.opcodes(main_marks, supp_marks)
            if tag != "equal"
        ]
        out.append(
            {
                "label": label,
                "main": main_marks,
                "supp": supp_marks,
                "agree": not diffs,
                "diffs": diffs,
            }
        )
    return out


# ── entry point ──────────────────────────────────────────────────────────


def compare_streams(
    norm: dict,
    recon: dict,
    main_blocks: list[dict],
    tiebreak_engine: str | None,
    read_crop: CropReader | None = None,
) -> dict:
    """The compare record for one page: disputes with verdicts, the
    marks report, and the page metrics. `norm`/`recon` are the
    normalize/reconstruct artifact records; `main_blocks` provides the main engine's bboxes;
    `read_crop` returns the cached LightOn read for an exact bbox."""
    ms = streams.MainStream(
        norm["main"]["tokens"], recon["main"]["items"], main_blocks
    )
    supp_recs = norm["supplementals"]
    labels = [f"supp{i + 1}" for i in range(len(supp_recs))]
    stream_legend = [{"label": "main", "engine": norm["main"]["engine"]}] + [
        {"label": lb, "engine": s["engine"], "unit": s["unit"]}
        for lb, s in zip(labels, supp_recs)
    ]
    degraded = [
        lb for lb, s in zip(labels, supp_recs) if not s["tokens"] and ms.tokens
    ]
    if not ms.tokens and any(s["tokens"] for s in supp_recs):
        degraded.insert(0, "main")

    disputes: list[dict] = []
    if "main" not in degraded:
        if tiebreak_engine is not None:
            if "supp1" not in degraded:
                disputes = tiebreak.resolve(
                    ms, supp_recs[0]["tokens"], read_crop
                )
        else:
            # with one stream missing the remaining pair still
            # compares — there is just no third voter
            live = [
                (lb, s["tokens"])
                for lb, s in zip(labels, supp_recs)
                if lb not in degraded
            ]
            if live:
                disputes = vote.resolve(ms, live)
    _mark_reorders(disputes)
    for n, d in enumerate(disputes):
        d["id"] = n
        d["high_risk"] = _is_high_risk(d)

    reasons = Counter(d["reason"] for d in disputes if d.get("reason"))
    resolutions = Counter(d["resolution"] for d in disputes)
    metrics = {
        "main_units": len(ms.tokens),
        "disputed_units": sum(
            d["main_span"][1] - d["main_span"][0] for d in disputes
        ),
        "n_disputes": len(disputes),
        "by_resolution": dict(resolutions),
        "by_reason": dict(reasons),
        "n_low_confidence": sum(1 for d in disputes if d["low_confidence"]),
        "n_high_risk": sum(1 for d in disputes if d["high_risk"]),
    }
    if tiebreak_engine is not None:
        metrics["tiebreak"] = tiebreak.metrics(disputes)
    return {
        "params": params(),
        "streams": stream_legend,
        "degraded": degraded,
        "disputes": disputes,
        "marks": _marks_report(
            ms.tokens,
            [(lb, s["tokens"]) for lb, s in zip(labels, supp_recs)],
        ),
        "metrics": metrics,
    }
