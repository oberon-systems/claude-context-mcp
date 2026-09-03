"""Run an index in this process, and keep a row saying how it went.

Indexing used to be a container the host started for one tree. The API holds
every tree at `/code/<project>` and carries the same code, so it does the work
itself; what a caller loses by not watching a log, it gets back from the row
this module writes.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from typing import Any

from psycopg2.extensions import cursor as Cursor

from ctxgraph.identifiers import project_mount
from ctxgraph.storage import get_db_connection

LOG = logging.getLogger(__name__)

COLUMNS = (
    "id, project, status, fresh, project_type, files, with_node, entities, "
    "edges, pruned, failures, gaps, error, started_at, finished_at"
)
# What scan_and_build_graph returns, in the order the row stores it.
COUNTS = ("files", "with_node", "entities", "edges", "pruned", "failures", "gaps")


def row_view(row: tuple[Any, ...]) -> dict[str, Any]:
    """Turn a selected row into the shape the API answers with."""
    return dict(zip(COLUMNS.replace(" ", "").split(","), row, strict=True))


def running_job(cursor: Cursor, project: str) -> dict[str, Any] | None:
    """Return the run still going for a project, if there is one."""
    cursor.execute(
        f"SELECT {COLUMNS} FROM index_jobs WHERE project = %s AND status = 'running';",
        (project,),
    )
    row = cursor.fetchone()
    return row_view(row) if row else None


def job_row(cursor: Cursor, job_id: int) -> dict[str, Any] | None:
    """Return one run by id."""
    cursor.execute(f"SELECT {COLUMNS} FROM index_jobs WHERE id = %s;", (job_id,))
    row = cursor.fetchone()
    return row_view(row) if row else None


def recent_jobs(cursor: Cursor, project: str | None, limit: int) -> list[dict]:
    """Return the last runs, newest first, of one project or of all of them."""
    cursor.execute(
        f"SELECT {COLUMNS} FROM index_jobs "
        "WHERE (%s::text IS NULL OR project = %s) "
        "ORDER BY started_at DESC LIMIT %s;",
        (project, project, limit),
    )
    return [row_view(row) for row in cursor.fetchall()]


def open_job(
    cursor: Cursor, project: str, fresh: bool, project_type: str | None
) -> int:
    """Record a run about to start. Raises if one is already going."""
    cursor.execute(
        """
        INSERT INTO index_jobs (project, fresh, project_type)
        VALUES (%s, %s, %s)
        RETURNING id;
        """,
        (project, fresh, project_type),
    )
    return int(cursor.fetchone()[0])


def last_run(cursor: Cursor, project: str) -> datetime | None:
    """When a run for this project last started, whatever became of it.

    A failed run counts: the scheduler waits an interval after it rather than
    trying again on the next tick, so a project that cannot be indexed is not
    indexed once a minute forever.
    """
    cursor.execute(
        "SELECT MAX(started_at) FROM index_jobs WHERE project = %s;", (project,)
    )
    row = cursor.fetchone()
    return row[0] if row else None


def open_run(
    cursor: Cursor, project: str, project_type: str | None, fresh: bool
) -> dict[str, Any]:
    """Check that a project may start a run now, and record that it has.

    One implementation of "may this project be indexed", so a run the schedule
    started and a run the dashboard asked for cannot come to different
    conclusions. The thread is left to the caller: it outlives the transaction
    this is called in, and starting it before the row is committed would let a
    rollback leave a run nothing is tracking.
    """
    mount = project_mount(project)
    if not os.path.isdir(mount):
        raise RuntimeError(
            f"{project} is not mounted at {mount}; the override has to be "
            "rewritten and this service recreated before it can be read"
        )
    running = running_job(cursor, project)
    if running is not None:
        raise RuntimeError(f"job {running['id']} is already indexing this project")
    job_id = open_job(cursor, project, fresh, project_type)
    return job_row(cursor, job_id) or {"id": job_id}


def fail_orphaned(cursor: Cursor, before: datetime) -> list[tuple[int, str]]:
    """Close the runs an earlier process left behind, and name them.

    A run is a daemon thread of the API, and `index_jobs` carries no lease: a
    restart in the middle of one leaves the row `running` forever, and the
    partial unique index then refuses every further run of that project -
    scheduled or by hand. Indexing only ever happens in this process, so a row
    still running from before this process started belongs to a dead one.
    """
    cursor.execute(
        """
        UPDATE index_jobs
           SET status = 'failed', finished_at = CURRENT_TIMESTAMP,
               error = 'worker-api restarted while this run was going'
         WHERE status = 'running' AND started_at < %s
        RETURNING id, project;
        """,
        (before,),
    )
    return [(int(row[0]), str(row[1])) for row in cursor.fetchall()]


def close_job(
    cursor: Cursor,
    job_id: int,
    counts: dict[str, int] | None,
    error: str | None,
) -> None:
    """Record how a run ended, whether it finished or threw."""
    values = [None if counts is None else counts.get(name) for name in COUNTS]
    cursor.execute(
        """
        UPDATE index_jobs
           SET status = %s, error = %s, finished_at = CURRENT_TIMESTAMP,
               files = %s, with_node = %s, entities = %s, edges = %s,
               pruned = %s, failures = %s, gaps = %s
         WHERE id = %s;
        """,
        ("failed" if error else "done", error, *values, job_id),
    )


def run_in_background(
    job_id: int,
    project: str,
    root_path: str,
    project_type: str | None,
    fresh: bool,
) -> None:
    """Index a project on a thread of its own, and close the row after.

    A connection of its own, because the request that started this returned
    long ago and the pooled one went back with it.
    """

    def work() -> None:
        # Imported here rather than at module level: this pulls in the whole
        # tree-sitter stack, and the API must start whether or not a run is
        # ever asked for.
        from ctxgraph.indexer import scan_and_build_graph

        counts: dict[str, int] | None = None
        error: str | None = None
        try:
            counts = scan_and_build_graph(project, root_path, project_type, fresh)
        except Exception as failure:  # noqa: BLE001 - recorded, not swallowed
            error = f"{type(failure).__name__}: {failure}"
            LOG.exception("Indexing %s failed", project)
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                close_job(cursor, job_id, counts, error)
            conn.commit()
        finally:
            conn.close()

    threading.Thread(target=work, name=f"index-{project}", daemon=True).start()
