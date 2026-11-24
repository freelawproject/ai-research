"""Score each engine's output against the eval set.

Punctuation and spacing are ignored: two values match when their letters
and digits are the same. Each wrong entry gets a tier (see toc.tiers);
where the reviewer set a tier for that engine in the viewer, that tier is
used. Only pages whose eval set is complete are scored. No API calls.

There is one eval set per prompt version (data/eval/v1, data/eval/v2). By
default each engine is scored against the eval set of its own version, and
for v2 on the history split is scored too. `--eval-version v1` scores every
engine against one version's eval set instead; fields that version or the
engine does not have (topics, the history split) are then left out.

    uv run python -m toc.score
    uv run python -m toc.score --pages 21-30
    uv run python -m toc.score --eval-version v1
"""

import argparse
import json
import re
from collections import Counter
from difflib import SequenceMatcher

from toc.config import EVAL_DIR, OUTPUTS_DIR, page_file, prompt_version
from toc.evalset import LEVELS
from toc.pages import parse_page_range
from toc.schema import FLAG_FIELDS, TEXT_FIELDS
from toc.tiers import entry_level, is_misread, norm, split_shape


def without_joiners(history: str | None) -> str | None:
    """Drop the "and" that only joins one history phrase to the next.

    "reh den, and cert den" and "reh den, cert den" then compare equal, so a
    prompt that returns the phrases as a list is not marked down for it. An
    "and" inside a phrase ("reh and reh den") is kept.
    """
    return re.sub(r",\s+and\s+", ", ", history) if history else history


def key(entry: dict) -> str:
    first = entry["citations"][0] if entry["citations"] else ""
    return f"{norm(entry['name'])}|{norm(first)}".lower()


def align(gold: list[dict], model: list[dict]) -> list[tuple[dict | None, dict | None]]:
    """Pair gold and model entries in order; None marks a missing side."""
    pairs: list[tuple[dict | None, dict | None]] = []
    matcher = SequenceMatcher(
        None, [key(e) for e in gold], [key(e) for e in model], autojunk=False
    )
    for tag, g0, g1, m0, m1 in matcher.get_opcodes():
        if tag == "equal":
            pairs += zip(gold[g0:g1], model[m0:m1], strict=True)
            continue
        # Unmatched stretches pair up in order, so near-miss names still meet.
        shared = min(g1 - g0, m1 - m0)
        pairs += zip(gold[g0 : g0 + shared], model[m0 : m0 + shared], strict=True)
        pairs += [(entry, None) for entry in gold[g0 + shared : g1]]
        pairs += [(None, entry) for entry in model[m0 + shared : m1]]
    return pairs


def score_citations(got: dict, want: dict) -> Counter:
    """Sort one entry's citations into exact, misread, missed and added."""
    tally: Counter = Counter()
    found = Counter(norm(c) for c in got["citations"])
    wanted = Counter(norm(c) for c in want["citations"])
    tally["citations_exact"] = sum((found & wanted).values())
    # A leftover citation that is a look-alike swap away from a leftover
    # gold citation was found on the page but misread. What then remains
    # is missed (gold side) or added (model side).
    extra = list((found - wanted).elements())
    for cite in (wanted - found).elements():
        near = next((e for e in extra if is_misread(e, cite)), None)
        if near is None:
            tally["citations_missed"] += 1
        else:
            extra.remove(near)
            tally["citations_misread"] += 1
    # An added "citation" whose text is this entry's own history or topics
    # is real text in the wrong field, not an invention.
    elsewhere = norm(want["history"]) + "|" + norm(want["topics"])
    misplaced = sum(1 for cite in extra if cite and cite in elsewhere)
    tally["citations_misplaced"] = misplaced
    tally["citations_invented"] = len(extra) - misplaced
    return tally


def reviewer_tier(row: dict, engine: str) -> str | None:
    """The tier a reviewer set by hand for this engine, if they changed it."""
    judgment = row["judgments"].get(engine)
    if judgment and judgment["level"] != judgment["suggested"]:
        return judgment["level"]
    return None


def score_engine(engine: str, version: str, pages: list[int]) -> Counter:
    """Score one engine against the eval set of prompt version `version`."""
    tally: Counter = Counter()
    # The history split is only comparable within one prompt version.
    compare_split = version == prompt_version(engine) and version != "v1"
    for page in pages:
        output = page_file(OUTPUTS_DIR / engine, page)
        if not output.is_file():
            continue
        tally["pages"] += 1
        rows = json.loads(page_file(EVAL_DIR / version, page).read_text())["rows"]
        # Keep each accepted entry with the tier the reviewer confirmed for it.
        gold = [
            {**row["entry"], "_tier": reviewer_tier(row, engine)}
            for row in rows
            if row["status"] != "open"
        ]
        record = json.loads(output.read_text())
        model_entries = record["entries"]
        gold_has_topics = any(entry["topics"] for entry in gold)
        if not record.get("topics_extracted", True) or not gold_has_topics:
            # One side has no topics (the prompt or the eval set leaves them
            # out), so they are not compared.
            tally["pages_without_topics"] += 1
            gold = [{**entry, "topics": None} for entry in gold]
            model_entries = [{**entry, "topics": None} for entry in model_entries]
        for want, got in align(gold, model_entries):
            if want is not None:
                want = {**want, "history": without_joiners(want["history"])}
            if got is not None:
                got = {**got, "history": without_joiners(got["history"])}
            if want is None:
                tally["extra_entries"] += 1
                continue
            tally["gold_entries"] += 1
            tally["gold_citations"] += len(want["citations"])
            if got is None:
                tally["missing_entries"] += 1
                continue
            for field in TEXT_FIELDS:
                tally[f"{field}_right"] += norm(want[field]) == norm(got[field])
            tally["flags_right"] += all(
                bool(want[f]) == bool(got[f]) for f in FLAG_FIELDS
            )
            # The reviewer's tier wins over the automatic one where there is one.
            if compare_split and want["history"]:
                tally["split_entries"] += 1
                # The division only; a misread inside an item is counted above.
                tally["split_right"] += split_shape(got) == split_shape(want)
            tier = want["_tier"]
            if tier not in LEVELS:
                tier = entry_level(got, want, compare_split)
            tally[f"tier_{'same' if tier == 'cosmetic' else tier}"] += 1
            tally += score_citations(got, want)
    return tally


def line(label: str, count: int, total: int | None = None) -> None:
    share = f" / {total:<5} {count / total:7.2%}" if total else ""
    print(f"    {label:<44} {count:>5}{share}")


def report(engine: str, tally: Counter) -> None:
    entries, cites = tally["gold_entries"], tally["gold_citations"]
    print(f"{engine}  ({tally['pages']} pages)")
    print(f"  Entries ({entries} in the eval set)")
    line("right", tally["tier_same"], entries)
    line("text in the wrong field", tally["tier_field"], entries)
    line("OCR error (look-alike character)", tally["tier_ocr"], entries)
    line(
        "hallucination (text added, dropped, altered)",
        tally["tier_hallucination"],
        entries,
    )
    line("missed entirely", tally["missing_entries"], entries)
    line("invented (not in the eval set)", tally["extra_entries"])
    print(f"  Citations ({cites} in the eval set)")
    found = tally["citations_exact"] + tally["citations_misread"]
    line("found on the page", found, cites)
    line("  of those, read exactly", tally["citations_exact"], cites)
    line("  of those, with an OCR misread", tally["citations_misread"], cites)
    line("missed", tally["citations_missed"], cites)
    line("hallucinated (added by the model)", tally["citations_invented"])
    line("other text filed as a citation", tally["citations_misplaced"])
    print("  Other fields right")
    line("case name", tally["name_right"], entries)
    line("court", tally["court_right"], entries)
    line("history", tally["history_right"], entries)
    if tally["pages_without_topics"]:
        print("    topics                                       not compared")
    else:
        line("topics", tally["topics_right"], entries)
    line("page-break flags", tally["flags_right"], entries)
    if tally["split_entries"]:
        line(
            "history split (entries with history)",
            tally["split_right"],
            tally["split_entries"],
        )
    print()


def complete_pages(version: str) -> tuple[list[int], list[int]]:
    """(complete, unfinished) eval pages for one prompt version."""
    complete: list[int] = []
    unfinished: list[int] = []
    for path in sorted((EVAL_DIR / version).glob("page_*.json")):
        data = json.loads(path.read_text())
        (complete if data["complete"] else unfinished).append(data["page"])
    return complete, unfinished


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pages", help="page or range, e.g. 21-30")
    parser.add_argument(
        "--eval-version", help="score every engine against this eval set, e.g. v1"
    )
    args = parser.parse_args()
    wanted = parse_page_range(args.pages) if args.pages else None

    for engine in sorted(d.name for d in OUTPUTS_DIR.iterdir() if d.is_dir()):
        version = args.eval_version or prompt_version(engine)
        complete, unfinished = complete_pages(version)
        pages = [p for p in complete if wanted is None or p in wanted]
        tally = score_engine(engine, version, pages)
        if not tally["pages"]:
            note = f"; still open: {unfinished}" if unfinished else ""
            print(f"{engine}: no complete {version} eval pages to score{note}\n")
            continue
        done, still_open = len(complete), len(unfinished)
        print(f"eval set {version}: {done} pages complete, {still_open} open")
        report(engine, tally)


if __name__ == "__main__":
    main()
