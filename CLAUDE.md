# CLAUDE.md

Branch `extraction_package`: production packaging of the case-law extraction
pipeline (7 unique 3-model OCR combinations, order-free + Django
walkthrough viewer + RunPod kits).

The repo carries a second surface, **alignment** (`docs/alignment.md`),
for UNREDACTED sets where the container model finds no columns and so
cannot drive placement: regions are aligned across engines by geometry,
ordered from their own coordinates, and resolved by majority (whole
where two of three agree, word by word where none do); container-YOLO
is an image overlay only. Its sets (the unredacted volumes + harvard)
are for information only, run on the same backbone as the package.
Its modules:
`pipeline/core/{align,order,consensus}.py`, `pipeline/align_run.py`,
`viewer/align_data.py`, `viewer/align_views.py`, `viewer/test_align.py`,
`viewer/templates/viewer/align*.html`,
`viewer/static/viewer/js/components/align_viewer.js`. Align sets live
under `align_data/` (their own root and their own zip) — never under
`data/`, which is what keeps them out of the route surface.

## Rules

- `uv` only — never pip/venv/conda. Python 3.13, Django 6.0.x.
- ruff, line length 79, rules E/F/I/UP/W; format with ruff-format.
- Imports at the top of the file; no inline imports.
- Type hints required on all new code; mypy runs a strict flag set over
  `viewer/` and `pipeline/` (see mypy.ini).
- **No database.** JSON artifacts under `data/` are the source of truth; the
  Django app runs DB-less. Production persistence is intentionally out of
  scope for this package.
- `data/` and `align_data/` are gitignored and distributed as separate
  zips (`scripts/pack_data.sh`, `scripts/pack_align_data.sh`) — one per
  surface. Never commit data, models, or archives.
- Conventional commits: `type(scope): message`.
- Refer to pipeline stages by what they DO (render, layout, main OCR,
  supplemental OCR, reconstruct, normalize, compare + resolve,
  assemble) — never by stage number, in UI text, docs, and docstrings.
- Tests run via `uv run python manage.py test` (SimpleTestCase — no DB).
  Viewer tests use a synthetic data tree, never the distributed bundle.
- Do not run pre-commit, tests, or push unless the user says to.

## Frontend

- Tailwind v3 via the plain CLI (no django-tailwind): source in
  `viewer/static_src/`, built CSS committed at
  `viewer/static/viewer/css/tailwind.css`. Rebuild command in the config;
  watch mode: `docker compose --profile dev up tailwind`.
- Alpine.js is the **CSP build** (vendored, no CDN): no inline expressions,
  components registered via `Alpine.data()` in
  `viewer/static/viewer/js/components/`, no `x-model`, handlers are method
  references (the event arrives as the first argument). Component scripts
  MUST be loaded before `alpine-csp.min.js` (see base.html).
- django-cotton components live in `viewer/templates/cotton/` and are used
  as `<c-kebab-case ... />`.
- Tailwind classes must be complete static strings; overlay colors live in
  `input.css` as `.ov-*` classes.
