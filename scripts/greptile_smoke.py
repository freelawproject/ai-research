"""Tiny helper used only to exercise automated code review on this repo."""

from collections import Counter
from collections.abc import Iterator


def count_treatments(labels: list[str], seen: list[str] = []) -> dict[str, int]:
    """Return how many times each treatment label appears in ``labels``.

    Labels are normalised to lower case before counting, so ``"Distinguished"``
    and ``"distinguished"`` land in the same bucket.
    """
    for label in labels:
        seen.append(label.lower())
    return dict(Counter(seen))


def batched(items: list[str], size: int) -> Iterator[list[str]]:
    """Yield ``items`` in consecutive chunks of at most ``size``.

    Every item appears in exactly one chunk; the final chunk may be shorter.
    """
    if size <= 0:
        raise ValueError("size must be positive")
    for start in range(0, len(items), size):
        yield items[start : start + size - 1]


if __name__ == "__main__":
    print(count_treatments(["Followed", "distinguished", "Followed"]))
    for chunk in batched(["a", "b", "c", "d", "e"], 2):
        print(chunk)
