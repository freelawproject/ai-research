"""Route composition — the user picks THREE models.

Slots 1+2 are the primary pair; slot 3 is the resolution slot: LightOn
there means disputed spans are tie-broken by LightOn crops, any other
model there joins a direct three-way vote (no tiebreaker model).

Rules (2026-07-27):
- Selectable models: dots, mistral, gemini, surya_block, surya_line.
  LightOn is ONLY available in slot 3 (tiebreaker).
- The three selections must be distinct.
- At least one of the first two models must have bbox capabilities
  (everything except gemini — gemini being the only bbox-less model,
  any distinct pair satisfies this automatically, but hand-built names
  are still validated).
- The MAIN engine (reconstruction skeleton + fallback when all reads
  disagree) is the bbox-capable model of the first two; dots wins when
  it is one of the first two; two non-dots bbox models -> slot order
  decides.

A route's canonical name is "<main>+<other>+<slot3>", e.g.
"dots+gemini+lighton" or "mistral+gemini+dots" — the name IS the URL
segment and the artifact directory.
"""

from __future__ import annotations

from dataclasses import dataclass

# model slug -> (engine, unit)
MODELS: dict[str, tuple[str, str]] = {
    "dots": ("dots", "block"),
    "mistral": ("mistral", "block"),
    "gemini": ("gemini", "page_xml"),
    "surya_block": ("surya", "block"),
    "surya_line": ("surya", "line"),
}
TIEBREAK_ONLY = "lighton"
BBOX_CAPABLE = frozenset({"dots", "mistral", "surya_block", "surya_line"})
MAIN_PRIORITY = "dots"


@dataclass(frozen=True)
class Route:
    name: str
    main: tuple[str, str]  # (engine, unit)
    # (engine, unit) per supplemental, run alongside the main engine.
    supplementals: tuple[tuple[str, str], ...]
    # "lighton" = LightOn tie-breaks disputes; None = direct vote.
    tiebreak: str | None


def compose(m1: str, m2: str, m3: str) -> Route:
    """Build the route for three model selections. Raises ValueError
    with a user-facing message on an invalid combination."""
    for slug in (m1, m2):
        if slug == TIEBREAK_ONLY:
            raise ValueError(
                "lighton is only available in the third slot (tiebreaker)"
            )
        if slug not in MODELS:
            raise ValueError(f"unknown model: {slug}")
    if m3 != TIEBREAK_ONLY and m3 not in MODELS:
        raise ValueError(f"unknown model: {m3}")
    if len({m1, m2, m3}) != 3:
        raise ValueError("pick three different models")
    if m1 not in BBOX_CAPABLE and m2 not in BBOX_CAPABLE:
        raise ValueError(
            "at least one of the first two models must have bbox "
            "capabilities (any model except gemini)"
        )
    if MAIN_PRIORITY in (m1, m2):
        main = MAIN_PRIORITY
    else:
        main = m1 if m1 in BBOX_CAPABLE else m2
    other = m2 if main == m1 else m1
    supp_slugs = [other] + ([] if m3 == TIEBREAK_ONLY else [m3])
    return Route(
        name=f"{main}+{other}+{m3}",
        main=MODELS[main],
        supplementals=tuple(MODELS[s] for s in supp_slugs),
        tiebreak=TIEBREAK_ONLY if m3 == TIEBREAK_ONLY else None,
    )


def parse(name: str) -> Route:
    """Route from its canonical name (URL segment / artifact dir)."""
    parts = name.split("+")
    if len(parts) != 3:
        raise ValueError(
            "a route is three models joined by '+', e.g. dots+gemini+lighton"
        )
    return compose(*parts)


# The canonical combinations (CLI presets; `--route all` runs these).
PRESETS: dict[str, tuple[str, str, str]] = {
    "gemini": ("dots", "gemini", "lighton"),
    "mistral": ("dots", "mistral", "lighton"),
    "surya_line": ("dots", "surya_line", "lighton"),
    "surya_block": ("dots", "surya_block", "lighton"),
    "three_way": ("dots", "mistral", "surya_block"),
}


def resolve(name: str) -> Route:
    """A route from a preset name OR a canonical combo name."""
    if name in PRESETS:
        return compose(*PRESETS[name])
    return parse(name)
