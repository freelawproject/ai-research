"""Route composition — the user picks THREE models.

A combination is a SET: order never matters —
dots+mistral+gemini IS dots+gemini+mistral. Slot 3 is the resolution
slot only in the sense of WHAT is picked there: LightOn means disputed
spans are tie-broken by LightOn crops; any other model joins a direct
three-way vote (no tiebreaker model).

Rules:
- Selectable models: dots, mistral, gemini, surya_block. LightOn is
  ONLY available in slot 3 (tiebreaker).
- The three selections must be distinct.
- The OCR pair/trio must contain a bbox-capable model (everything
  except gemini — gemini being the only bbox-less model, any distinct
  set satisfies this automatically, but hand-built names are still
  validated).
- A lighton tiebreak needs DOTS in the combination: the cached
  tiebreak crops are cut from dots blocks, so
  a non-dots-main tiebreak route could never vote. Vote trios have no
  such constraint.
- The MAIN engine (reconstruction skeleton + fallback when all reads
  disagree) is the highest-priority bbox-capable model in the set:
  dots > mistral > surya_block (MAIN_PRIORITY — a fixed ranking, so
  the same set always gets the same main).

There are exactly 7 unique combinations: 3 tiebreak pairs (dots +
one other model + lighton) + C(4,3)=4 vote trios. A route's canonical
name is "<main>+<rest sorted>+[lighton]", e.g. "dots+gemini+lighton"
or "mistral+gemini+surya_block" — the name IS the URL segment and the
artifact directory. Non-canonical orderings in a URL still parse —
they serve as aliases of the canonical route.

Which of the 7 a given dataset PRESENTS is a separate question, answered
by combos_for(dataset): a dataset without a complete LightOn crop cache
presents its vote trios only (config.TIEBREAK_DATASETS).
"""

from __future__ import annotations

from dataclasses import dataclass

from pipeline.core.config import presents_tiebreak

# model slug -> (engine, unit)
MODELS: dict[str, tuple[str, str]] = {
    "dots": ("dots", "block"),
    "mistral": ("mistral", "block"),
    "gemini": ("gemini", "page_xml"),
    "surya_block": ("surya", "block"),
}
TIEBREAK_ONLY = "lighton"
BBOX_CAPABLE = frozenset({"dots", "mistral", "surya_block"})
# The MAIN engine is the highest-priority bbox model in the set — a
# fixed ranking so the same combination always gets the same main.
MAIN_PRIORITY = ("dots", "mistral", "surya_block")


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
    members = {m1, m2} | ({m3} if m3 != TIEBREAK_ONLY else set())
    if not members & BBOX_CAPABLE:
        raise ValueError(
            "the models must include one with bbox capabilities "
            "(any model except gemini)"
        )
    if m3 == TIEBREAK_ONLY and "dots" not in members:
        raise ValueError(
            "a lighton tiebreak needs dots in the combination — the "
            "cached tiebreak crops are cut from dots blocks. Pick dots "
            "as one of the models, or use a third model for a "
            "three-way vote"
        )
    # order never matters: the same SET is the same route. The main is
    # the highest-priority bbox model; the rest sort into the name.
    main = next(s for s in MAIN_PRIORITY if s in members)
    rest = sorted(members - {main})
    tail = "+".join([*rest, m3] if m3 == TIEBREAK_ONLY else rest)
    return Route(
        name=f"{main}+{tail}",
        main=MODELS[main],
        supplementals=tuple(MODELS[s] for s in rest),
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


def slugs(route: Route) -> tuple[str, str, str]:
    """The three model slugs (m1 = main, m2 = other, m3 = resolver)
    that compose a route — the dropdown selection it round-trips to."""
    slug_of = {v: k for k, v in MODELS.items()}
    return (
        slug_of[route.main],
        slug_of[route.supplementals[0]],
        TIEBREAK_ONLY if route.tiebreak else slug_of[route.supplementals[1]],
    )


# LEGACY named combinations — URL aliases from the fixed-route era +
# the default flow combo. No longer privileged: all 7 unique
# combinations are first-class (`--route all` builds every one).
PRESETS: dict[str, tuple[str, str, str]] = {
    "gemini": ("dots", "gemini", "lighton"),
    "mistral": ("dots", "mistral", "lighton"),
    "surya_block": ("dots", "surya_block", "lighton"),
    "three_way": ("dots", "mistral", "surya_block"),
}


def resolve(name: str) -> Route:
    """A route from a preset name OR a canonical combo name."""
    if name in PRESETS:
        return compose(*PRESETS[name])
    return parse(name)


def all_combos() -> list[Route]:
    """Every semantically distinct composable route, one per canonical
    name — compose() canonicalizes vote-member order, so slot-order
    twins (dots+gemini+mistral / dots+mistral+gemini) are already one
    route."""
    out: dict[str, Route] = {}
    for m1 in MODELS:
        for m2 in MODELS:
            for m3 in (TIEBREAK_ONLY, *MODELS):
                try:
                    r = compose(m1, m2, m3)
                except ValueError:
                    continue
                out.setdefault(r.name, r)
    return sorted(out.values(), key=lambda r: r.name)


def combos_for(dataset: str) -> list[Route]:
    """The combinations a dataset PRESENTS — every one, or the vote
    trios alone where the tiebreak cache is not complete enough to
    compare fairly (config.TIEBREAK_DATASETS). This is the one list
    `--route all`, the stats table, and every combination picker in the
    viewer are built from, so a combination is never offered for a
    dataset that cannot answer it."""
    combos = all_combos()
    if presents_tiebreak(dataset):
        return combos
    return [r for r in combos if r.tiebreak is None]


def presented(dataset: str, name: str) -> bool:
    """Whether one combination is presented for a dataset — the guard
    for a route arriving from a URL or the CLI."""
    try:
        route = resolve(name)
    except ValueError:
        return False
    return any(r.name == route.name for r in combos_for(dataset))
