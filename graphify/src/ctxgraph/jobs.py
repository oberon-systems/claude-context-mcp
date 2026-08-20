"""The work queue behind the remote summarizer, as SQL over a cursor.

Kept apart from `storage`, which is the indexer's half of the database: a job
describes files that are already in the graph and writes nothing an index run
would recognise. Every function here takes a cursor and a scope, the way
`storage` does, so the caller owns the transaction.
"""

from __future__ import annotations

from typing import Any

from psycopg2.extensions import cursor as Cursor

# A file whose node carries no text cannot be described by a worker that has
# no copy of the tree.
NO_CONTENT = "no stored content, re-index the project"

# The digest of the text a worker is handed, computed in the database so that
# creating a job does not drag the whole corpus through this process. The
# result must equal summary_text.content_key of the same slice.
HASH_SQL = "ENCODE(SHA256(CONVERT_TO(LEFT(n.content, %s), 'UTF8')), 'hex')"


def running_job(cursor: Cursor, project: str) -> dict[str, Any] | None:
    """Return the job still handing out work for a project, if there is one."""
    cursor.execute(
        """
        SELECT id, project, status, input_chars, refresh, lease_seconds, model
          FROM summary_jobs
         WHERE project = %s AND status = 'running'
         LIMIT 1;
        """,
        (project,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return dict(
        zip(
            (
                "id",
                "project",
                "status",
                "input_chars",
                "refresh",
                "lease_seconds",
                "model",
            ),
            row,
            strict=False,
        )
    )


def create_job(
    cursor: Cursor,
    project: str,
    input_chars: int,
    refresh: bool,
    lease_seconds: int,
    model: str | None,
) -> int:
    """Open a job. Raises psycopg2.IntegrityError when one is already open."""
    cursor.execute(
        """
        INSERT INTO summary_jobs (
            project, input_chars, refresh, lease_seconds, model
        )
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id;
        """,
        (project, input_chars, refresh, lease_seconds, model),
    )
    return int(cursor.fetchone()[0])


def populate_job(
    cursor: Cursor,
    job_id: int,
    project: str,
    input_chars: int,
    refresh: bool,
    limit: int = 0,
) -> tuple[int, int]:
    """Enqueue the files of a project. Returns the tasks written and skipped.

    A file with no stored text is enqueued as skipped rather than left out: a
    job that silently covered a tenth of a project would read as a finished
    one.
    """
    sources = ["auto", "llm"] if refresh else ["auto"]
    cursor.execute(
        f"""
        INSERT INTO summary_tasks (job_id, file_path, content_hash, state, note)
        SELECT
            %s,
            n.file_path,
            COALESCE({HASH_SQL}, ''),
            CASE
                WHEN COALESCE(n.content, '') = '' THEN 'skipped' ELSE 'pending'
            END,
            CASE WHEN COALESCE(n.content, '') = '' THEN %s END
          FROM graph_nodes AS n
         WHERE n.project = %s AND n.type = 'file' AND n.file_path IS NOT NULL
           AND COALESCE(n.metadata ->> 'summary_source', 'auto') = ANY(%s)
         ORDER BY n.file_path
         LIMIT %s
        ON CONFLICT (job_id, file_path) DO NOTHING
        RETURNING state;
        """,
        (job_id, input_chars, NO_CONTENT, project, sources, limit or None),
    )
    states = [row[0] for row in cursor.fetchall()]
    return len(states), states.count("skipped")


def settle_cached(
    cursor: Cursor, job_id: int, project: str
) -> list[tuple[int, str, str]]:
    """Close every pending task the cache can already answer.

    Returns (task id, file path, summary) so the caller can apply each one to
    its node. The model is never asked about text it has already described.
    """
    cursor.execute(
        """
        UPDATE summary_tasks AS t
           SET state = 'done', origin = 'cache',
               updated_at = CURRENT_TIMESTAMP
          FROM summary_cache AS c
         WHERE t.job_id = %s AND t.state = 'pending'
           AND c.project = %s AND c.content_hash = t.content_hash
        RETURNING t.id, t.file_path, c.summary;
        """,
        (job_id, project),
    )
    return [(int(row[0]), row[1], row[2]) for row in cursor.fetchall()]


def job_row(cursor: Cursor, job_id: int) -> dict[str, Any] | None:
    """Return one job, without its progress."""
    cursor.execute(
        """
        SELECT id, project, status, input_chars, refresh, lease_seconds,
               model, created_at, updated_at, finished_at
          FROM summary_jobs WHERE id = %s;
        """,
        (job_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return dict(
        zip(
            (
                "id",
                "project",
                "status",
                "input_chars",
                "refresh",
                "lease_seconds",
                "model",
                "created_at",
                "updated_at",
                "finished_at",
            ),
            row,
            strict=False,
        )
    )


def job_progress(cursor: Cursor, job_id: int) -> dict[str, int]:
    """Count the tasks of a job by state.

    A lease that has run out reads as pending here, which is what makes a
    lazily reclaimed queue honest between one claim and the next.
    """
    cursor.execute(
        """
        SELECT
            CASE
                WHEN state = 'leased' AND lease_expires_at < NOW() THEN 'pending'
                ELSE state
            END AS state,
            COUNT(*),
            COUNT(*) FILTER (WHERE origin = 'cache'),
            COUNT(*) FILTER (WHERE origin = 'model')
          FROM summary_tasks WHERE job_id = %s GROUP BY 1;
        """,
        (job_id,),
    )
    progress = {
        "total": 0,
        "pending": 0,
        "leased": 0,
        "done": 0,
        "failed": 0,
        "skipped": 0,
        "from_cache": 0,
        "from_model": 0,
    }
    for state, count, cached, generated in cursor.fetchall():
        progress[state] = progress.get(state, 0) + int(count)
        progress["total"] += int(count)
        progress["from_cache"] += int(cached)
        progress["from_model"] += int(generated)
    return progress


def list_jobs(
    cursor: Cursor, project: str | None, status: str | None, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    """List jobs newest first, with the total the page was taken from."""
    cursor.execute(
        """
        SELECT id, project, status, input_chars, refresh, lease_seconds,
               model, created_at, updated_at, finished_at,
               COUNT(*) OVER () AS total
          FROM summary_jobs
         WHERE (%s IS NULL OR project = %s) AND (%s IS NULL OR status = %s)
         ORDER BY id DESC
         LIMIT %s OFFSET %s;
        """,
        (project, project, status, status, limit, offset),
    )
    rows = cursor.fetchall()
    fields = (
        "id",
        "project",
        "status",
        "input_chars",
        "refresh",
        "lease_seconds",
        "model",
        "created_at",
        "updated_at",
        "finished_at",
    )
    total = int(rows[0][-1]) if rows else 0
    return [dict(zip(fields, row, strict=False)) for row in rows], total


def list_job_files(
    cursor: Cursor, job_id: int, state: str | None, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    """Page through the files of a job. Never returns their text."""
    cursor.execute(
        """
        SELECT id, file_path, state, attempts, worker_id, origin,
               lease_expires_at, note, updated_at, COUNT(*) OVER () AS total
          FROM summary_tasks
         WHERE job_id = %s AND (%s IS NULL OR state = %s)
         ORDER BY id
         LIMIT %s OFFSET %s;
        """,
        (job_id, state, state, limit, offset),
    )
    rows = cursor.fetchall()
    fields = (
        "task_id",
        "file_path",
        "state",
        "attempts",
        "worker_id",
        "origin",
        "lease_expires_at",
        "note",
        "updated_at",
    )
    total = int(rows[0][-1]) if rows else 0
    return [dict(zip(fields, row, strict=False)) for row in rows], total


def cancel_job(cursor: Cursor, job_id: int) -> bool:
    """Stop handing out work. Leased tasks are left to expire."""
    cursor.execute(
        """
        UPDATE summary_jobs
           SET status = 'cancelled', finished_at = NOW(),
               updated_at = CURRENT_TIMESTAMP
         WHERE id = %s AND status = 'running';
        """,
        (job_id,),
    )
    return cursor.rowcount > 0


def reclaim_expired(cursor: Cursor, job_id: int) -> int:
    """Return leases that ran out to the queue.

    Run at the head of a claim rather than on a timer: the only moment a
    stale lease matters is when someone is asking for work.
    """
    cursor.execute(
        """
        UPDATE summary_tasks
           SET state = 'pending', lease_token = NULL, worker_id = NULL,
               leased_at = NULL, lease_expires_at = NULL,
               updated_at = CURRENT_TIMESTAMP
         WHERE job_id = %s AND state = 'leased' AND lease_expires_at < NOW();
        """,
        (job_id,),
    )
    return cursor.rowcount


def claim_batch(
    cursor: Cursor,
    job_id: int,
    batch: int,
    token: str,
    worker_id: str,
    lease_seconds: int,
    max_attempts: int,
) -> list[dict[str, Any]]:
    """Take up to `batch` pending tasks under one lease token.

    SKIP LOCKED is what lets a second worker claim at the same moment instead
    of blocking on the first one's rows and then reading them anyway.
    """
    cursor.execute(
        """
        WITH claimed AS (
            SELECT id FROM summary_tasks
             WHERE job_id = %s AND state = 'pending' AND attempts < %s
             ORDER BY id
             LIMIT %s
               FOR UPDATE SKIP LOCKED
        )
        UPDATE summary_tasks AS t
           SET state = 'leased',
               lease_token = %s,
               worker_id = %s,
               attempts = t.attempts + 1,
               leased_at = NOW(),
               lease_expires_at = NOW() + MAKE_INTERVAL(SECS => %s),
               updated_at = CURRENT_TIMESTAMP
          FROM claimed
         WHERE t.id = claimed.id
        RETURNING t.id, t.file_path, t.content_hash, t.attempts;
        """,
        (job_id, max_attempts, batch, token, worker_id, lease_seconds),
    )
    fields = ("task_id", "file_path", "content_hash", "attempts")
    return [dict(zip(fields, row, strict=False)) for row in cursor.fetchall()]


def read_task_content(
    cursor: Cursor, project: str, task_ids: list[int], input_chars: int
) -> dict[int, str]:
    """Read the text of the claimed tasks, exactly as it will be sent."""
    cursor.execute(
        """
        SELECT t.id, LEFT(n.content, %s)
          FROM summary_tasks AS t
          LEFT JOIN graph_nodes AS n
            ON n.project = %s AND n.id = t.file_path AND n.type = 'file'
         WHERE t.id = ANY(%s);
        """,
        (input_chars, project, task_ids),
    )
    return {int(row[0]): row[1] or "" for row in cursor.fetchall()}


def set_task_hash(cursor: Cursor, task_id: int, content_hash: str) -> None:
    """Record the digest of the text actually handed out."""
    cursor.execute(
        "UPDATE summary_tasks SET content_hash = %s WHERE id = %s;",
        (content_hash, task_id),
    )


def skip_tasks(cursor: Cursor, task_ids: list[int], note: str) -> None:
    """Close tasks that cannot be handed to a worker, with the reason."""
    if not task_ids:
        return
    cursor.execute(
        """
        UPDATE summary_tasks
           SET state = 'skipped', note = %s, lease_token = NULL,
               worker_id = NULL, lease_expires_at = NULL,
               updated_at = CURRENT_TIMESTAMP
         WHERE id = ANY(%s);
        """,
        (note, task_ids),
    )


def lock_leased_task(cursor: Cursor, task_id: int, token: str) -> dict[str, Any] | None:
    """Take the row a result is about, but only if the lease still holds.

    A worker that comes back after its lease expired finds nothing here, which
    is what stops it overwriting the answer of whoever took the task next.
    """
    cursor.execute(
        """
        SELECT t.id, t.job_id, t.file_path, t.content_hash, t.attempts,
               j.project, j.status
          FROM summary_tasks AS t JOIN summary_jobs AS j ON j.id = t.job_id
         WHERE t.id = %s AND t.state = 'leased' AND t.lease_token = %s
           FOR UPDATE OF t;
        """,
        (task_id, token),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    fields = (
        "task_id",
        "job_id",
        "file_path",
        "content_hash",
        "attempts",
        "project",
        "job_status",
    )
    return dict(zip(fields, row, strict=False))


def finish_task(cursor: Cursor, task_id: int, note: str | None) -> None:
    """Mark a task answered by the model."""
    cursor.execute(
        """
        UPDATE summary_tasks
           SET state = 'done', origin = 'model', note = %s,
               lease_token = NULL, worker_id = NULL, lease_expires_at = NULL,
               updated_at = CURRENT_TIMESTAMP
         WHERE id = %s;
        """,
        (note, task_id),
    )


def fail_task(cursor: Cursor, task_id: int, error: str, max_attempts: int) -> str:
    """Send a task back to the queue, or give up on it. Returns the state."""
    cursor.execute(
        """
        UPDATE summary_tasks
           SET state = CASE
                   WHEN attempts >= %s THEN 'failed' ELSE 'pending'
               END,
               note = %s, lease_token = NULL, worker_id = NULL,
               lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP
         WHERE id = %s
        RETURNING state;
        """,
        (max_attempts, error, task_id),
    )
    row = cursor.fetchone()
    return row[0] if row else "unknown"


def extend_lease(cursor: Cursor, job_id: int, token: str, lease_seconds: int) -> int:
    """Push back the deadline of a whole batch."""
    cursor.execute(
        """
        UPDATE summary_tasks
           SET lease_expires_at = NOW() + MAKE_INTERVAL(SECS => %s),
               updated_at = CURRENT_TIMESTAMP
         WHERE job_id = %s AND lease_token = %s AND state = 'leased';
        """,
        (lease_seconds, job_id, token),
    )
    return cursor.rowcount


def release_lease(cursor: Cursor, job_id: int, token: str) -> int:
    """Hand an unfinished batch straight back, without waiting it out."""
    cursor.execute(
        """
        UPDATE summary_tasks
           SET state = 'pending', lease_token = NULL, worker_id = NULL,
               leased_at = NULL, lease_expires_at = NULL,
               updated_at = CURRENT_TIMESTAMP
         WHERE job_id = %s AND lease_token = %s AND state = 'leased';
        """,
        (job_id, token),
    )
    return cursor.rowcount


def finish_job_if_drained(cursor: Cursor, job_id: int) -> bool:
    """Close a job once nothing is left to hand out."""
    cursor.execute(
        """
        UPDATE summary_jobs
           SET status = 'done', finished_at = NOW(),
               updated_at = CURRENT_TIMESTAMP
         WHERE id = %s AND status = 'running'
           AND NOT EXISTS (
               SELECT 1 FROM summary_tasks
                WHERE job_id = %s AND state IN ('pending', 'leased')
           );
        """,
        (job_id, job_id),
    )
    return cursor.rowcount > 0
