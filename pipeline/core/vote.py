"""The direct three-way vote variant: resolve a disagreement by asking a
third OCR model what it read, with no crop and no tiebreaker.

EVERYTHING specific to that variant lives here. The LightOn crop
tiebreak is `tiebreak.py`; primitives both share are `streams.py`. This
variant has no resolution parameters of its own — it reads whole-page
outputs the pipeline already has, so there is nothing to tune per run.

The main stream is diffed against BOTH supplementals; disagreement
regions from the two alignments are merged into intervals on the main
stream, each supplemental's aligned reading is projected out of its own
alignment, and a direct 2-of-3 majority resolves the interval. A
three-way split keeps the main reading and flags it low-confidence.
"""

from __future__ import annotations

from pipeline.core import streams


def _merge_intervals(
    regions: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Merge overlapping AND touching disagreement intervals on the main
    stream (a zero-width insertion at another region's edge belongs with
    it — the two alignments produced them independently)."""
    out: list[list[int]] = []
    for a, b in sorted(regions):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def resolve(
    ms: streams.MainStream,
    supps: list[tuple[str, list[dict]]],
) -> list[dict]:
    """Direct vote: merged disagreement intervals on the main stream,
    each supplemental's reading projected from its own alignment,
    2-of-3 majority. With one supplemental stream missing (degraded)
    there is no third voter — every dispute keeps main, low-confidence."""
    aligned = [
        (label, tokens, streams.keys(tokens)) for label, tokens in supps
    ]
    ops = {label: streams.opcodes(ms.keys, keys) for label, _, keys in aligned}
    regions = [
        (i1, i2)
        for label, _, _ in aligned
        for tag, i1, i2, _, _ in ops[label]
        if tag != "equal"
    ]
    disputes: list[dict] = []
    for i1, i2 in _merge_intervals(regions):
        d = streams.base_dispute(ms, i1, i2)
        d["spans"] = {}
        main_read = ms.keys[i1:i2]
        supp_reads: list[tuple[str, list[str]]] = []
        for label, tokens, keys in aligned:
            j1, j2 = streams.project(ops[label], i1, i2)
            d["spans"][label] = [j1, j2]
            d["reads"][label] = " ".join(keys[j1:j2])
            d["displays"][label] = streams.text(tokens, j1, j2, "display")
            supp_reads.append((label, keys[j1:j2]))
        if len(supp_reads) < 2:
            d.update(
                verdict="main",
                resolution="fallback",
                reason="no-vote",
                low_confidence=True,
            )
        elif supp_reads[0][1] == supp_reads[1][1]:
            # the supplementals agree; main is outvoted (or, on a
            # merged interval, everyone agrees after all — still the
            # supplementals' shared reading, identical to main's)
            winner = (
                supp_reads[0][0] if supp_reads[0][1] != main_read else "main"
            )
            d.update(
                verdict=winner,
                resolution="majority",
                reason=None,
                low_confidence=False,
            )
        elif main_read in (supp_reads[0][1], supp_reads[1][1]):
            d.update(
                verdict="main",
                resolution="majority",
                reason=None,
                low_confidence=False,
            )
        else:
            d.update(
                verdict="main",
                resolution="fallback",
                reason="three-way-split",
                low_confidence=True,
            )
        disputes.append(d)
    return disputes
