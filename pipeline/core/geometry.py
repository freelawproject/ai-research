"""Shared bbox geometry. Every bbox is [x0, y0, x1, y1] in the canonical
1700x2200 render space (pipeline.core.config)."""

from __future__ import annotations


def area(bb: list[float]) -> float:
    return max(0.0, bb[2] - bb[0]) * max(0.0, bb[3] - bb[1])


def inter(a: list[float], b: list[float]) -> float:
    """Intersection area of two bboxes."""
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def cover_frac(inner: list[float], outer: list[float]) -> float:
    """Fraction of inner's area that lies inside outer (0..1)."""
    return inter(inner, outer) / max(1.0, area(inner))


def center(bb: list[float]) -> tuple[float, float]:
    return ((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2)


def center_in(inner: list[float], outer: list[float]) -> bool:
    cx, cy = center(inner)
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]
