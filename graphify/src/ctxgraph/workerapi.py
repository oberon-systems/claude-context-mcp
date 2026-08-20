"""Hand summarizing work to a machine that has a GPU and no copy of the tree.

The local pass loads a model in this container and reads the files off the
mount. A worker on another machine has neither, so the work becomes a queue:
it claims a batch, is handed the text and the prompt to run, and sends back
one sentence per file. Nothing it sends is trusted - the answer goes through
the same shaping and the same gates the in-process summarizer applies before
it reaches a node.

Every handler is a plain `def`. psycopg2 is synchronous, so an `async def`
here would block the event loop for every other request in flight.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg2
import uvicorn
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query
from psycopg2.extensions import cursor as Cursor
from psycopg2.pool import ThreadedConnectionPool
from pydantic import BaseModel, Field

from ctxgraph import jobs
from ctxgraph.config import (
    CONTENT_STORE_CHARS,
    LLM_MAX_TOKENS,
    WORKER_API_DOCS,
    WORKER_API_PORT,
    WORKER_API_TOKEN,
    WORKER_API_TOKEN_MIN,
    WORKER_LEASE_SECONDS,
    WORKER_MAX_ATTEMPTS,
    WORKER_MAX_BATCH,
    WORKER_MAX_REPLY_CHARS,
)
from ctxgraph.storage import (
    get_db_url,
    put_cached_summary,
    save_llm_summary,
)
from ctxgraph.summary_text import (
    SYSTEM_PROMPT,
    content_key,
    shape,
    strip_preamble,
    useful,
)

LOG = logging.getLogger(__name__)

DEFAULT_PAGE = 50
MAX_PAGE = 500
NOT_USEFUL = "says nothing the file name does not"

_pool: ThreadedConnectionPool | None = None


def pool() -> ThreadedConnectionPool:
    """Return the connection pool, opening it on first use.

    A pool rather than a connection per request: this is a long-lived service,
    unlike every other user of `storage`, which is a job that runs once.
    """
    global _pool
    if _pool is None:
        _pool = ThreadedConnectionPool(1, 8, get_db_url())
    return _pool


@contextmanager
def transaction() -> Iterator[Cursor]:
    """Lend a connection out, committing what the handler wrote."""
    connection = pool().getconn()
    try:
        with connection.cursor() as cursor:
            yield cursor
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        pool().putconn(connection)


def require_token(authorization: str = Header(default="")) -> None:
    """Refuse anything that does not carry the shared token.

    Both sides are encoded first: compare_digest raises TypeError on a string
    holding non-ASCII, which would turn a bad header into a 500.
    """
    scheme, _, offered = authorization.partition(" ")
    if scheme.lower() != "bearer" or not offered:
        raise HTTPException(
            status_code=401,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not secrets.compare_digest(
        offered.encode("utf-8"), WORKER_API_TOKEN.encode("utf-8")
    ):
        raise HTTPException(
            status_code=401,
            detail="bad token",
            headers={"WWW-Authenticate": "Bearer"},
        )


class JobRequest(BaseModel):
    """What to summarize, and how much of each file to show the model."""

    project: str
    refresh: bool = False
    input_chars: int = Field(default=0, ge=0)
    limit: int = Field(default=0, ge=0)
    lease_seconds: int = Field(default=0, ge=0)
    model: str | None = None


class LeaseRequest(BaseModel):
    """A worker asking for its next batch."""

    worker_id: str = Field(min_length=1, max_length=64)
    batch: int = Field(default=4, ge=1)
    lease_seconds: int = Field(default=0, ge=0)


class LeaseHeld(BaseModel):
    """A worker naming the batch it holds."""

    worker_id: str = Field(min_length=1, max_length=64)
    lease_token: uuid.UUID
    lease_seconds: int = Field(default=0, ge=0)


class ResultRequest(BaseModel):
    """One file, described. `summary` is the raw reply, unshaped."""

    worker_id: str = Field(min_length=1, max_length=64)
    lease_token: uuid.UUID
    summary: str
    elapsed_ms: int | None = None
    model: str | None = None


class FailureRequest(BaseModel):
    """One file the worker could not describe."""

    worker_id: str = Field(min_length=1, max_length=64)
    lease_token: uuid.UUID
    error: str = ""


def apply_summary(
    cursor: Cursor, project: str, rel_path: str, summary: str
) -> tuple[bool, str | None]:
    """Put a shaped sentence on a node, if it says anything.

    A rejected answer is not applied but is still the answer, so the caller
    caches it either way: the files a small model is worst at are the ones it
    would otherwise be asked about on every pass.
    """
    if not useful(summary, rel_path):
        return False, NOT_USEFUL
    if not save_llm_summary(cursor, project, rel_path, summary):
        return False, "node is gone or carries a manual summary"
    return True, None


def job_view(cursor: Cursor, job: dict[str, Any]) -> dict[str, Any]:
    """Return a job with the counts that say how far along it is."""
    return {**job, "progress": jobs.job_progress(cursor, int(job["id"]))}


api = APIRouter(dependencies=[Depends(require_token)])


@api.get("/projects")
def get_projects() -> dict[str, Any]:
    """List what can be summarized, and what needs re-indexing first."""
    with transaction() as cursor:
        cursor.execute(
            """
            SELECT p.name, p.root_path, p.indexed_at,
                   COUNT(n.id) FILTER (WHERE n.type = 'file'),
                   COUNT(n.id) FILTER (
                       WHERE n.type = 'file' AND COALESCE(n.content, '') <> ''
                   ),
                   COUNT(n.id) FILTER (
                       WHERE n.type = 'file'
                       AND COALESCE(n.metadata ->> 'summary_source', 'auto')
                           = 'auto'
                   )
              FROM projects AS p
              LEFT JOIN graph_nodes AS n ON n.project = p.name
             GROUP BY p.name, p.root_path, p.indexed_at
             ORDER BY p.name;
            """
        )
        rows = cursor.fetchall()
        running = {}
        for name, *_ in rows:
            job = jobs.running_job(cursor, name)
            running[name] = int(job["id"]) if job else None
    projects = []
    for name, root_path, indexed_at, files, with_content, pending in rows:
        projects.append(
            {
                "name": name,
                "root_path": root_path,
                "indexed_at": indexed_at,
                "files": int(files),
                "with_content": int(with_content),
                "without_llm_summary": int(pending),
                "running_job": running[name],
            }
        )
    return {"projects": projects}


@api.post("/jobs", status_code=201)
def post_job(request: JobRequest) -> dict[str, Any]:
    """Open a job over every file of a project that still needs describing."""
    input_chars = request.input_chars or CONTENT_STORE_CHARS
    if not 1 <= input_chars <= CONTENT_STORE_CHARS:
        raise HTTPException(
            status_code=422,
            detail=f"input_chars must be between 1 and {CONTENT_STORE_CHARS}",
        )
    lease_seconds = request.lease_seconds or WORKER_LEASE_SECONDS
    if not 30 <= lease_seconds <= 3600:
        raise HTTPException(
            status_code=422, detail="lease_seconds must be between 30 and 3600"
        )

    with transaction() as cursor:
        cursor.execute("SELECT 1 FROM projects WHERE name = %s;", (request.project,))
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="unknown project")
        open_job = jobs.running_job(cursor, request.project)
        if open_job is not None:
            raise HTTPException(
                status_code=409,
                detail=f"job {open_job['id']} is still running for this project",
            )
        job_id = jobs.create_job(
            cursor,
            request.project,
            input_chars,
            request.refresh,
            lease_seconds,
            request.model,
        )
        total, skipped = jobs.populate_job(
            cursor, job_id, request.project, input_chars, request.refresh, request.limit
        )
        cached = 0
        if not request.refresh:
            for _, rel_path, summary in jobs.settle_cached(
                cursor, job_id, request.project
            ):
                apply_summary(cursor, request.project, rel_path, summary)
                cached += 1
        jobs.finish_job_if_drained(cursor, job_id)
        view = job_view(cursor, jobs.job_row(cursor, job_id) or {})

    hint = None
    if skipped:
        hint = (
            f"{skipped} files have no stored content; "
            "re-index the project so the worker can read them"
        )
    return {
        **view,
        "enqueued": total,
        "cached": cached,
        "skipped": skipped,
        "hint": hint,
    }


@api.get("/jobs")
def get_jobs(
    project: str | None = None,
    status: str | None = None,
    limit: int = Query(default=DEFAULT_PAGE, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """List jobs, newest first."""
    with transaction() as cursor:
        rows, total = jobs.list_jobs(cursor, project, status, limit, offset)
        items = [job_view(cursor, row) for row in rows]
    return {"jobs": items, "total": total, "limit": limit, "offset": offset}


@api.get("/jobs/{job_id}")
def get_job(job_id: int) -> dict[str, Any]:
    """One job with its progress."""
    with transaction() as cursor:
        row = jobs.job_row(cursor, job_id)
        if row is None:
            raise HTTPException(status_code=404, detail="unknown job")
        return job_view(cursor, row)


@api.post("/jobs/{job_id}/cancel")
def post_cancel(job_id: int) -> dict[str, Any]:
    """Stop handing out work. Leases already held are left to expire."""
    with transaction() as cursor:
        if jobs.job_row(cursor, job_id) is None:
            raise HTTPException(status_code=404, detail="unknown job")
        jobs.cancel_job(cursor, job_id)
        return job_view(cursor, jobs.job_row(cursor, job_id) or {})


@api.get("/jobs/{job_id}/files")
def get_job_files(
    job_id: int,
    state: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Page through the files of a job. Their text is only ever leased."""
    with transaction() as cursor:
        if jobs.job_row(cursor, job_id) is None:
            raise HTTPException(status_code=404, detail="unknown job")
        rows, total = jobs.list_job_files(cursor, job_id, state, limit, offset)
    return {"files": rows, "total": total, "limit": limit, "offset": offset}


@api.post("/jobs/{job_id}/lease")
def post_lease(job_id: int, request: LeaseRequest) -> dict[str, Any]:
    """Claim a batch, and be handed the text and the prompt to run on it.

    The batch can come back smaller than asked, or empty: cache hits and files
    with no stored text are settled here and never leased.
    """
    token = str(uuid.uuid4())
    batch = min(request.batch, WORKER_MAX_BATCH)
    with transaction() as cursor:
        job = jobs.job_row(cursor, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job")
        if job["status"] != "running":
            raise HTTPException(status_code=409, detail=f"job is {job['status']}")

        project = str(job["project"])
        input_chars = int(job["input_chars"])
        lease_seconds = request.lease_seconds or int(job["lease_seconds"])

        jobs.reclaim_expired(cursor, job_id)
        if not job["refresh"]:
            for _, rel_path, summary in jobs.settle_cached(cursor, job_id, project):
                apply_summary(cursor, project, rel_path, summary)

        claimed = jobs.claim_batch(
            cursor,
            job_id,
            batch,
            token,
            request.worker_id,
            lease_seconds,
            WORKER_MAX_ATTEMPTS,
        )
        texts = (
            jobs.read_task_content(
                cursor, project, [task["task_id"] for task in claimed], input_chars
            )
            if claimed
            else {}
        )

        tasks: list[dict[str, Any]] = []
        empty: list[int] = []
        for task in claimed:
            text = texts.get(int(task["task_id"]), "")
            if not text:
                empty.append(int(task["task_id"]))
                continue
            digest = content_key(text)
            if digest != task["content_hash"]:
                jobs.set_task_hash(cursor, int(task["task_id"]), digest)
            tasks.append(
                {
                    "task_id": int(task["task_id"]),
                    "file_path": task["file_path"],
                    "content_hash": digest,
                    "attempts": int(task["attempts"]),
                    "prompt": f"File: {task['file_path']}\n\n{text}",
                }
            )
        jobs.skip_tasks(cursor, empty, jobs.NO_CONTENT)
        jobs.finish_job_if_drained(cursor, job_id)
        progress = jobs.job_progress(cursor, job_id)
        status = str((jobs.job_row(cursor, job_id) or {}).get("status", "running"))

    return {
        "lease_token": token,
        "lease_seconds": lease_seconds,
        "job": {
            "id": job_id,
            "project": project,
            "status": status,
            "input_chars": input_chars,
        },
        "system_prompt": SYSTEM_PROMPT,
        "max_tokens": LLM_MAX_TOKENS,
        "tasks": tasks,
        "remaining": progress["pending"],
    }


@api.post("/jobs/{job_id}/heartbeat")
def post_heartbeat(job_id: int, request: LeaseHeld) -> dict[str, Any]:
    """Push back the deadline of the whole batch this token covers."""
    with transaction() as cursor:
        job = jobs.job_row(cursor, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job")
        seconds = request.lease_seconds or int(job["lease_seconds"])
        extended = jobs.extend_lease(cursor, job_id, str(request.lease_token), seconds)
    if not extended:
        raise HTTPException(status_code=409, detail="lease is no longer held")
    return {"extended": extended, "lease_seconds": seconds}


@api.post("/jobs/{job_id}/release")
def post_release(job_id: int, request: LeaseHeld) -> dict[str, Any]:
    """Hand an unfinished batch back at once, rather than waiting it out."""
    with transaction() as cursor:
        if jobs.job_row(cursor, job_id) is None:
            raise HTTPException(status_code=404, detail="unknown job")
        released = jobs.release_lease(cursor, job_id, str(request.lease_token))
    return {"released": released}


@api.post("/tasks/{task_id}/result")
def post_result(task_id: int, request: ResultRequest) -> dict[str, Any]:
    """Take one answer, shape it, and put it on the node if it says anything."""
    if len(request.summary) > WORKER_MAX_REPLY_CHARS:
        raise HTTPException(status_code=413, detail="reply too long")

    with transaction() as cursor:
        task = jobs.lock_leased_task(cursor, task_id, str(request.lease_token))
        if task is None:
            raise HTTPException(
                status_code=409, detail="lease expired or already settled"
            )

        project = str(task["project"])
        rel_path = str(task["file_path"])
        summary = strip_preamble(shape(request.summary), rel_path)
        if not summary:
            state = jobs.fail_task(cursor, task_id, "empty reply", WORKER_MAX_ATTEMPTS)
            return {
                "task_id": task_id,
                "state": state,
                "applied": False,
                "summary": None,
                "reason": "empty reply",
            }

        put_cached_summary(cursor, project, str(task["content_hash"]), summary)
        applied, reason = apply_summary(cursor, project, rel_path, summary)
        jobs.finish_task(cursor, task_id, reason)
        jobs.finish_job_if_drained(cursor, task["job_id"])
        status = str((jobs.job_row(cursor, task["job_id"]) or {}).get("status", ""))

    return {
        "task_id": task_id,
        "state": "done",
        "applied": applied,
        "summary": summary,
        "reason": reason,
        "job_status": status,
    }


@api.post("/tasks/{task_id}/failure")
def post_failure(task_id: int, request: FailureRequest) -> dict[str, Any]:
    """Report that the model could not describe a file."""
    with transaction() as cursor:
        task = jobs.lock_leased_task(cursor, task_id, str(request.lease_token))
        if task is None:
            raise HTTPException(
                status_code=409, detail="lease expired or already settled"
            )
        state = jobs.fail_task(
            cursor, task_id, request.error[:500] or "worker failed", WORKER_MAX_ATTEMPTS
        )
        jobs.finish_job_if_drained(cursor, task["job_id"])
    return {"task_id": task_id, "state": state, "attempts": int(task["attempts"])}


def create_app() -> FastAPI:
    """Build the application, refusing to serve without a token."""
    if len(WORKER_API_TOKEN) < WORKER_API_TOKEN_MIN:
        raise RuntimeError(
            "WORKER_API_TOKEN is unset or shorter than "
            f"{WORKER_API_TOKEN_MIN} characters. This service is published to "
            "the network and serves the text of your files; generate one with "
            "`openssl rand -hex 24`."
        )
    docs = "/docs" if WORKER_API_DOCS else None
    app = FastAPI(
        title="claude-context-mcp worker API",
        docs_url=docs,
        redoc_url=None,
        openapi_url="/openapi.json" if WORKER_API_DOCS else None,
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        """Liveness, and whether the database is answering."""
        try:
            with transaction() as cursor:
                cursor.execute("SELECT 1;")
        except psycopg2.Error:
            raise HTTPException(
                status_code=503, detail="database unreachable"
            ) from None
        return {"status": "ok", "database": "ok"}

    app.include_router(api)
    return app


def main() -> None:
    """Run the API."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    uvicorn.run(create_app(), host="0.0.0.0", port=WORKER_API_PORT)


if __name__ == "__main__":
    main()
