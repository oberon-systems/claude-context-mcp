-- Work handed to a summarizing worker that is not on this machine.
--
-- The local pass holds one loaded model and walks the file list in process,
-- so it needs no state beyond the summary it writes. A worker reached over
-- HTTP can lose power in the middle of a batch, and two of them can ask for
-- work at the same moment, so the file list becomes rows: a job is one
-- project's worth of files to describe, a task is one file, and a lease that
-- expires without an answer is picked up by whoever asks next.
--
-- The cache key moves to sha256 here too. Every stored row is an md5 digest
-- that can never be hit again once the code changes - the key is the digest
-- of the text the model was shown, and the digest function is what changed -
-- so they are deleted rather than migrated. file_hashes.hash stays md5: it
-- answers "did this file change", and widening it would re-parse every file
-- of every project for nothing.

-- +goose Up

DELETE FROM summary_cache;

ALTER TABLE summary_cache ALTER COLUMN content_hash TYPE VARCHAR(64);

-- status and state are unconstrained VARCHAR, like plans.status: the
-- vocabulary belongs to the code, and a CHECK would turn a new outcome into a
-- schema migration. Jobs are running, done or cancelled; tasks are pending,
-- leased, done, failed or skipped.
CREATE TABLE IF NOT EXISTS summary_jobs (
    id BIGSERIAL PRIMARY KEY,
    project VARCHAR(64) NOT NULL REFERENCES projects (
        name
    ) ON DELETE CASCADE,
    status VARCHAR(20) NOT NULL DEFAULT 'running',
    -- How much of graph_nodes.content the worker is shown, and therefore what
    -- the cache is keyed by. Held per job, so one started with a smaller
    -- window keeps its own key for its whole life.
    input_chars INTEGER NOT NULL,
    -- Describe files that already carry a model summary, ignoring the cache.
    -- The remote half of FORCE_REEXTRACT.
    refresh BOOLEAN NOT NULL DEFAULT FALSE,
    lease_seconds INTEGER NOT NULL DEFAULT 300,
    -- What the requester asked for. Informational: nothing here loads a model.
    model TEXT,
    metadata JSONB DEFAULT '{}'::JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP WITH TIME ZONE
);

CREATE TABLE IF NOT EXISTS summary_tasks (
    -- An id of its own rather than the natural key: the path travels in a
    -- URL, and a surrogate keeps the endpoints free of escaping.
    id BIGSERIAL PRIMARY KEY,
    job_id BIGINT NOT NULL REFERENCES summary_jobs (id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    -- sha256 of exactly the text handed to the worker, rewritten at lease
    -- time from the text actually sent, so a re-index under an open job
    -- cannot key the cache by text nobody saw.
    content_hash VARCHAR(64) NOT NULL,
    state VARCHAR(20) NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    -- Issued per claim and held by every row of that batch. A result carrying
    -- a token the row no longer holds is a worker that came back after its
    -- lease expired, and it is refused rather than allowed to overwrite what
    -- the next worker wrote.
    lease_token UUID,
    worker_id VARCHAR(64),
    leased_at TIMESTAMP WITH TIME ZONE,
    lease_expires_at TIMESTAMP WITH TIME ZONE,
    -- How the summary was settled: model, or cache.
    origin VARCHAR(16),
    -- Why a task was skipped, failed, or answered with nothing useful.
    note TEXT,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (job_id, file_path)
);

-- The claim drains pending rows, so its index is partial and shrinks as the
-- job finishes.
CREATE INDEX IF NOT EXISTS idx_summary_tasks_claim
ON summary_tasks (job_id, id) WHERE state = 'pending';

-- The reclaim pass ahead of it reads only live leases.
CREATE INDEX IF NOT EXISTS idx_summary_tasks_expiry
ON summary_tasks (lease_expires_at) WHERE state = 'leased';

CREATE INDEX IF NOT EXISTS idx_summary_tasks_job_state
ON summary_tasks (job_id, state);

CREATE INDEX IF NOT EXISTS idx_summary_jobs_project_status
ON summary_jobs (project, status);

-- One live job per project. A second would duplicate every generation the
-- first has not reached yet, and the cache only absorbs that after the time
-- is already spent.
CREATE UNIQUE INDEX IF NOT EXISTS idx_summary_jobs_one_running
ON summary_jobs (project) WHERE status = 'running';

-- +goose Down

DROP TABLE IF EXISTS summary_tasks;
DROP TABLE IF EXISTS summary_jobs;
DELETE FROM summary_cache;
ALTER TABLE summary_cache ALTER COLUMN content_hash TYPE VARCHAR(32);
