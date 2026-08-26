-- What an index run is, so that something other than a shell can start one.
--
-- Indexing used to be a container the host started: `docker compose run
-- graphify`, one tree, one run, progress only in the log. The API holds the
-- same mounts and the same code, so it can do the work itself - and once the
-- dashboard can ask for it, the run needs a row to report against.
--
-- Shaped like summary_jobs, which already solved this for the summarizing
-- pass: a status, the counts the run wrote, and a partial unique index that
-- keeps one project from being indexed twice at once.

-- +goose Up

CREATE TABLE IF NOT EXISTS index_jobs (
    id SERIAL PRIMARY KEY,
    -- A tag rather than a reference: the first index of a project creates the
    -- projects row, so the job exists before the thing it names.
    project VARCHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'running',
    fresh BOOLEAN NOT NULL DEFAULT FALSE,
    project_type VARCHAR(50),
    -- Filled in when the run ends; NULL while it is going.
    files INTEGER,
    with_node INTEGER,
    entities INTEGER,
    edges INTEGER,
    pruned INTEGER,
    failures INTEGER,
    gaps INTEGER,
    error TEXT,
    started_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP WITH TIME ZONE
);

-- One run per project at a time. Two would fight over the same nodes and the
-- loser would prune what the winner had just written.
CREATE UNIQUE INDEX IF NOT EXISTS idx_index_jobs_one_running
ON index_jobs (project) WHERE status = 'running';

CREATE INDEX IF NOT EXISTS idx_index_jobs_project
ON index_jobs (project, started_at DESC);

-- +goose Down

DROP TABLE IF EXISTS index_jobs;
