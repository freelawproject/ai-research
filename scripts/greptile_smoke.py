"""Tiny helper used only to exercise automated code review on this repo."""

from collections import Counter


def count_treatments(labels: list[str], seen: list[str] = []) -> dict[str, int]:
    """Return how many times each treatment label appears in ``labels``.

    Labels are normalised to lower case before counting, so ``"Distinguished"``
    and ``"distinguished"`` land in the same bucket.
    """
    for label in labels:
        seen.append(label.lower())
    return dict(Counter(seen))


if __name__ == "__main__":
    print(count_treatments(["Followed", "distinguished", "Followed"]))
