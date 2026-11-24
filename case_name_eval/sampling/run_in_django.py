"""Run the sampling queries from the CourtListener Django shell.

Runs inside the `cl-django` container, by hand, against the database the
container's default connection points at. Read-only.

    docker cp sampling/. cl-django:/opt/courtlistener/case_name_sampling/
    docker exec -e STEP=counts cl-django python manage.py shell -c \
        "exec(open('case_name_sampling/run_in_django.py').read())"
    docker exec -e STEP=pull cl-django python manage.py shell -c \
        "exec(open('case_name_sampling/run_in_django.py').read())"
    docker cp cl-django:/opt/courtlistener/case_name_sampling/sample_2k.csv data/

STEP=counts prints the candidate pool per stratum.
STEP=pull writes sample_2k.csv next to this script.
"""

import os
import time
from pathlib import Path

from django.db import connections

HERE = Path("case_name_sampling")
DB_ALIAS = os.environ.get("DB_ALIAS", "default")
STEP = os.environ.get("STEP", "counts")

started = time.time()
with connections[DB_ALIAS].cursor() as cursor:
    if STEP == "counts":
        cursor.execute((HERE / "stratum_counts.sql").read_text())
        print("".join(f"{col.name:<24}" for col in cursor.description))
        for row in cursor.fetchall():
            print("".join(f"{value!s:<24}" for value in row))
    elif STEP == "pull":
        out_path = HERE / "sample_2k.csv"
        # pull_sample.sql is a COPY ... TO STDOUT statement; stream it to disk.
        sql = (HERE / "pull_sample.sql").read_text()
        with out_path.open("wb") as out, cursor.copy(sql) as copy:
            for chunk in copy:
                out.write(chunk)
        print(f"Wrote {out_path} ({out_path.stat().st_size:,} bytes)")
    else:
        raise SystemExit(f"Unknown STEP={STEP!r}; use 'counts' or 'pull'")
print(f"Done in {time.time() - started:.0f}s")
