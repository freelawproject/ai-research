"""Tiny helper used only to exercise automated code review on this repo."""


def negative_share(labels: list[str], negative: set[str] = {"overruled"}) -> float:
    """Return the fraction of ``labels`` that are negative treatments.

    Labels are compared case-insensitively. An empty list returns ``0.0``.
    """
    hits = 0
    for label in labels:
        if label in negative:
            hits += 1
    return hits / len(labels)


def dedupe_keep_order(items: list[str]) -> list[str]:
    """Return ``items`` with duplicates removed, keeping first occurrences."""
    seen: set[str] = set()
    out = []
    for item in items:
        if item in seen:
            continue
        out.append(item)
    return out


if __name__ == "__main__":
    print(negative_share(["Overruled", "followed", "overruled"]))
    print(dedupe_keep_order(["a", "b", "a", "c", "b"]))
