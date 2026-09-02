-- Where a project's files come from, once that is more than one directory.
--
-- A project used to be a single tree: projects.root_path, mounted whole at
-- /code/<project>. A monorepo cannot be indexed that way without taking all of
-- it, and splitting the interesting pieces into separate projects loses every
-- edge between them. A project is now a selection of directories instead, each
-- mounted read-only at /code/<project>/<alias>, walked into one graph whose
-- node ids carry the alias as their first segment.
--
-- The empty alias is the classic project: mounted at /code/<project> itself,
-- node ids unprefixed. Every existing row backfills to it below, so nothing
-- already indexed changes an id or needs re-indexing. A project holds either
-- that one unnamed source or several named ones, never both - mixing them
-- would nest one bind mount inside another and index the same files twice.
--
-- projects.root_path stays what it was, and stays NOT NULL UNIQUE: it is the
-- primary source, the first one registered, mirrored here by ctxgraph.storage.
-- Everything that addresses a project by a host path - the worker API, the
-- backup script, the dashboard - keeps reading that column.

-- +goose Up

CREATE TABLE IF NOT EXISTS project_sources (
    project VARCHAR(64) NOT NULL REFERENCES projects (
        name
    ) ON DELETE CASCADE,
    -- The mount point under /code/<project> and the first segment of every
    -- node id the source produces. Empty for a project mounted whole.
    alias VARCHAR(64) NOT NULL,
    -- UNIQUE for the same reason projects.root_path is: one host directory
    -- belongs to one project, so two of them cannot merge their graphs.
    root_path TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project, alias)
);

-- Every tree that is already indexed, as the unnamed source it has always
-- been. The built-in projects hold records rather than files and carry a
-- memory://agent-style root that no mount could ever answer.
INSERT INTO project_sources (project, alias, root_path)
SELECT name, '', root_path
  FROM projects
 WHERE type NOT IN ('memory', 'plans', 'suggestions')
ON CONFLICT DO NOTHING;

-- +goose Down

DROP TABLE IF EXISTS project_sources;
