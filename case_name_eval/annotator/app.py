"""Annotator for the case-name evaluation set.

    uv run uvicorn app:app --port 8215

Two review flows, one per field:

- `/review/full`   full captions whose Opus value is not confirmed by production
- `/review/short`  short names whose Opus value is not confirmed by the docket name

Each flow shows only the fields that still need a person. `/` lists every
record with the state of both fields. Saves go to the `field_labels` table in
../data/annotations.db. The store is backed up on every start.
"""

import json
from contextlib import asynccontextmanager
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlencode

import store
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent
CONTEXT_CHARS = 6000  # text shown after the model input, for the annotator only
FIELD_TITLES = {"full": "Full captions", "short": "Short names"}
FIELD_FLAGS = {
    "full": list(store.FLAGS),
    "short": ["no_caption", "ocr_error", "unsure"],
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.backup()
    yield


app = FastAPI(title="Case-name annotator", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "ui" / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "ui" / "templates")


class Filters(BaseModel):
    split: str = ""
    stratum: str = ""
    doc_type: str = ""

    def clause(self) -> tuple[str, list]:
        parts, params = [], []
        if self.split:
            parts.append("r.split = ?")
            params.append(self.split)
        if self.stratum:
            parts.append("r.stratum = ?")
            params.append(self.stratum)
        if self.doc_type:
            parts.append("r.doc_type = ?")
            params.append(self.doc_type)
        return (" AND ".join(parts) or "1 = 1"), params

    def query(self) -> str:
        return urlencode({k: v for k, v in self.model_dump().items() if v})


class LabelIn(BaseModel):
    value: str = Field(max_length=5000)
    flags: list[str] = []
    notes: str = Field(default="", max_length=5000)
    status: str = "reviewed"
    split: str = ""
    stratum: str = ""
    doc_type: str = ""


def read_filters(request: Request) -> Filters:
    q = request.query_params
    return Filters(
        split=q.get("split", ""),
        stratum=q.get("stratum", ""),
        doc_type=q.get("doc_type", ""),
    )


def check_field(field: str) -> None:
    if field not in store.FIELDS:
        raise HTTPException(404, "No such field")


def neighbour(
    conn,
    field: str,
    filters: Filters,
    position: int,
    step: int,
    pending_only: bool = True,
) -> int | None:
    """Next (step=1) or previous (step=-1) record in a field's review queue.

    The queue holds the records whose field was not auto-accepted. With
    pending_only, records already reviewed or skipped are left out too.
    """
    where, params = filters.clause()
    op, order = (">", "ASC") if step > 0 else ("<", "DESC")
    pending = (
        "l.cluster_id IS NULL" if pending_only else "coalesce(l.status, '') <> 'auto'"
    )
    row = conn.execute(
        f"""
        SELECT r.cluster_id FROM records r
        LEFT JOIN field_labels l ON l.cluster_id = r.cluster_id AND l.field = ?
        WHERE {where} AND {pending} AND r.position {op} ?
        ORDER BY r.position {order} LIMIT 1
        """,
        [field, *params, position],
    ).fetchone()
    return row["cluster_id"] if row else None


def progress(conn) -> dict:
    """Per-field counts: auto-accepted, reviewed, skipped and still to review."""
    total = conn.execute("SELECT count(*) FROM records").fetchone()[0]
    out = {"total": total, "fields": {}}
    for field in store.FIELDS:
        counts = dict(
            conn.execute(
                "SELECT status, count(*) FROM field_labels WHERE field = ? GROUP BY 1",
                (field,),
            ).fetchall()
        )
        auto, reviewed, skipped = (
            counts.get(k, 0) for k in ("auto", "reviewed", "skipped")
        )
        out["fields"][field] = {
            "title": FIELD_TITLES[field],
            "auto": auto,
            "reviewed": reviewed,
            "skipped": skipped,
            "todo": total - auto - reviewed - skipped,
        }
    out["complete"] = conn.execute(
        """
        SELECT count(*) FROM (
            SELECT cluster_id FROM field_labels
            WHERE status IN ('auto', 'reviewed')
            GROUP BY cluster_id HAVING count(*) = 2
        )
        """
    ).fetchone()[0]
    return out


def mark_differences(text: str, other: str) -> list[dict]:
    """Split `text` into words, marking those `other` does not have in the same place.

    Words are compared ignoring capitalization and punctuation.
    """
    words, other_words = text.split(), other.split()
    matcher = SequenceMatcher(
        a=[store.comparable(w) for w in words],
        b=[store.comparable(w) for w in other_words],
        autojunk=False,
    )
    same = set()
    for block in matcher.get_matching_blocks():
        same.update(range(block.a, block.a + block.size))
    return [{"word": w, "differs": i not in same} for i, w in enumerate(words)]


def default_flags(field: str, pre) -> list[str]:
    """Flags ticked on an unlabeled full caption that was read from the wider window."""
    if field != "full" or not pre or pre["window"] != store.WIDE_WINDOW_CHARS:
        return []
    if not pre["case_name_full"]:
        return []
    return [
        "caption_beyond_input" if pre["caption_at_2k"] == "none" else "caption_cut_off"
    ]


def candidates(field: str, record, pre, others, hold=None) -> list[dict]:
    """The values offered for one field, with words marked against the other source.

    For a field put back in the queue, the value saved before is offered too.
    """
    if field == "full":
        opus = pre["case_name_full"] if pre else ""
        second = record["prod_case_name_full"] or ""
        rows = [("Opus", opus, second), ("In production", second, opus)]
    else:
        opus = pre["case_name"] if pre else ""
        second = record["docket_case_name"] or ""
        rows = [("Opus", opus, second), ("Original docket name", second, opus)]
        prod = record["prod_case_name"] or ""
        if prod and store.comparable(prod) != store.comparable(second):
            rows.append(("In production", prod, opus))
    for other in others:
        value = other["case_name_full"] if field == "full" else other["case_name"]
        if value:
            rows.append((other["source"], value, opus))
    if hold and hold["previous_value"]:
        rows.append(("Your earlier value", hold["previous_value"], opus))
    return [
        {"source": source, "value": value, "words": mark_differences(value, against)}
        for source, value, against in rows
    ]


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    filters = read_filters(request)
    where, params = filters.clause()
    with store.session() as conn:
        rows = conn.execute(
            f"""
            SELECT r.cluster_id, r.position, r.split, r.stratum, r.court_id,
                   r.docket_case_name, r.doc_type,
                   f.status AS full_status, s.status AS short_status,
                   coalesce(f.value, p.case_name_full, '') AS full_value,
                   coalesce(s.value, p.case_name, '') AS short_value
            FROM records r
            LEFT JOIN preannotations p USING (cluster_id)
            LEFT JOIN field_labels f
                   ON f.cluster_id = r.cluster_id AND f.field = 'full'
            LEFT JOIN field_labels s
                   ON s.cluster_id = r.cluster_id AND s.field = 'short'
            WHERE {where}
            ORDER BY r.position
            """,
            params,
        ).fetchall()
        strata = [
            r[0]
            for r in conn.execute("SELECT DISTINCT stratum FROM records ORDER BY 1")
        ]
        doc_types = [
            (r[0], r[1])
            for r in conn.execute(
                "SELECT doc_type, count(*) FROM records "
                "WHERE doc_type IS NOT NULL GROUP BY 1 ORDER BY 2 DESC"
            )
        ]
        stats = progress(conn)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "page": "list",
            "doc_types": doc_types,
            "rows": rows,
            "filters": filters,
            "strata": strata,
            "splits": store.SPLITS,
            "stats": stats,
            "query": filters.query(),
        },
    )


@app.get("/review/{field}")
def review_start(request: Request, field: str):
    """Jump to the first item still to review in this field's queue."""
    check_field(field)
    filters = read_filters(request)
    with store.session() as conn:
        cluster_id = neighbour(conn, field, filters, -1, 1)
    if cluster_id is None:
        return RedirectResponse(f"/?{filters.query()}")
    return RedirectResponse(f"/review/{field}/{cluster_id}?{filters.query()}")


@app.get("/review/{field}/{cluster_id}", response_class=HTMLResponse)
def review(request: Request, field: str, cluster_id: int):
    check_field(field)
    filters = read_filters(request)
    with store.session() as conn:
        record = conn.execute(
            "SELECT * FROM records WHERE cluster_id = ?", (cluster_id,)
        ).fetchone()
        if record is None:
            raise HTTPException(404, "No such record")
        pre = conn.execute(
            "SELECT * FROM preannotations WHERE cluster_id = ?", (cluster_id,)
        ).fetchone()
        others = conn.execute(
            "SELECT * FROM suggestions WHERE cluster_id = ? ORDER BY source",
            (cluster_id,),
        ).fetchall()
        labels = {
            r["field"]: r
            for r in conn.execute(
                "SELECT * FROM field_labels WHERE cluster_id = ?", (cluster_id,)
            )
        }
        hold = conn.execute(
            "SELECT reason, previous_value FROM review_holds "
            "WHERE cluster_id = ? AND field = ?",
            (cluster_id, field),
        ).fetchone()
        position = record["position"]
        prev_id = neighbour(conn, field, filters, position, -1, pending_only=False)
        next_id = neighbour(conn, field, filters, position, 1, pending_only=False)
        stats = progress(conn)

    label = labels.get(field)
    column = "case_name_full" if field == "full" else "case_name"
    full_label = labels.get("full")
    limit = store.MODEL_INPUT_CHARS
    return templates.TemplateResponse(
        request,
        "review.html",
        {
            "page": field,
            "field": field,
            "title": FIELD_TITLES[field],
            "r": record,
            "pre": pre,
            "label": label,
            "label_flags": json.loads(label["flags"])
            if label
            else default_flags(field, pre),
            "start": label["value"] if label else (pre[column] if pre else ""),
            "candidates": candidates(field, record, pre, others, hold),
            "hold": hold,
            # The short-name flow shows which full caption the short name belongs to.
            "full_value": full_label["value"]
            if full_label
            else (pre["case_name_full"] if pre else ""),
            "full_status": full_label["status"] if full_label else "todo",
            "text_input": record["plain_text"][:limit],
            "text_after": record["plain_text"][limit : limit + CONTEXT_CHARS],
            "text_more": max(0, record["plain_text_length"] - limit - CONTEXT_CHARS),
            "flags": {k: store.FLAGS[k] for k in FIELD_FLAGS[field]},
            "filters": filters,
            "query": filters.query(),
            "prev_id": prev_id,
            "next_id": next_id,
            "stats": stats,
        },
    )


@app.post("/api/label/{field}/{cluster_id}")
def save(field: str, cluster_id: int, body: LabelIn):
    check_field(field)
    if body.status not in ("reviewed", "skipped"):
        raise HTTPException(422, "status must be 'reviewed' or 'skipped'")
    unknown = [f for f in body.flags if f not in FIELD_FLAGS[field]]
    if unknown:
        raise HTTPException(422, f"Unknown flags: {unknown}")
    value = " ".join(body.value.split())
    filters = Filters(split=body.split, stratum=body.stratum, doc_type=body.doc_type)
    with store.session() as conn:
        record = conn.execute(
            "SELECT position FROM records WHERE cluster_id = ?", (cluster_id,)
        ).fetchone()
        if record is None:
            raise HTTPException(404, "No such record")
        no_caption = "no_caption" in body.flags
        if body.status == "reviewed" and no_caption and value:
            raise HTTPException(
                422,
                "'No case name in the document' is ticked but a name is filled in. "
                "Clear the name or untick the flag.",
            )
        if body.status == "reviewed" and not value and not no_caption:
            if field == "full" and "caption_beyond_input" not in body.flags:
                raise HTTPException(
                    422,
                    "The caption is empty. Use 'No case name in document', tick "
                    "'Caption is not in the first 2,000 characters', or skip it.",
                )
            if field == "short":
                full = conn.execute(
                    "SELECT value FROM field_labels "
                    "WHERE cluster_id = ? AND field = 'full'",
                    (cluster_id,),
                ).fetchone()
                if full is None or full["value"]:
                    raise HTTPException(
                        422,
                        "The short name is empty. Use 'No case name in document', "
                        "or skip it.",
                    )
        conn.execute(
            """
            INSERT INTO field_labels VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cluster_id, field) DO UPDATE SET
                value = excluded.value, status = excluded.status,
                flags = excluded.flags, notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (
                cluster_id,
                field,
                value,
                body.status,
                json.dumps(sorted(body.flags)),
                body.notes.strip(),
                store.now(),
            ),
        )
        if field == "full" and body.status == "reviewed" and no_caption:
            # A document with no case name has no short name either. Record that
            # too, unless a person already saved a short name for it.
            conn.execute(
                """
                INSERT INTO field_labels VALUES (?, 'short', '', 'reviewed', ?, '', ?)
                ON CONFLICT(cluster_id, field) DO UPDATE SET
                    value = '', status = 'reviewed', flags = excluded.flags,
                    notes = '', updated_at = excluded.updated_at
                WHERE field_labels.status <> 'reviewed' OR field_labels.value = ''
                """,
                (cluster_id, json.dumps(["no_caption"]), store.now()),
            )
        if body.status == "reviewed":
            conn.execute(
                "DELETE FROM review_holds WHERE cluster_id = ? AND field = ?",
                (cluster_id, field),
            )
        conn.commit()
        next_id = neighbour(conn, field, filters, record["position"], 1)
        if next_id is None:
            next_id = neighbour(conn, field, filters, -1, 1)
    return {"ok": True, "next_id": next_id}
