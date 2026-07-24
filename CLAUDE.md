# CLAUDE.md

Branch `extraction_package`: production packaging of the case-law extraction
pipeline (3 OCR routes + Django walkthrough viewer + RunPod kits).

## Rules

- `uv` only — never pip/venv/conda. Python 3.13, Django 6.0.x.
- ruff, line length 79, rules E/F/I/UP/W; format with ruff-format.
- Imports at the top of the file; no inline imports.
- Type hints required on all new code; mypy strict for `viewer/` and
  `pipeline/` (see mypy.ini).
- **No database.** JSON artifacts under `data/` are the source of truth; the
  Django app runs DB-less. Production persistence is intentionally out of
  scope for this package.
- `data/` is gitignored and distributed as a zip (`scripts/pack_data.sh`).
  Never commit data, models, or archives.
- Conventional commits: `type(scope): message`.
- Tests run via `uv run python manage.py test` (SimpleTestCase — no DB).
- Do not run pre-commit, tests, or push unless the user says to.
