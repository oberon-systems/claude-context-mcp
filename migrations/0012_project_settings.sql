-- Where a project's indexing selection comes from, once that is no longer a
-- pair of files in the tree.
--
-- `.ctxkeep` and `.ctxignore` were read from the root of every mounted source
-- and stored nowhere. Every mount is read-only by contract, so nothing in the
-- stack could edit them: onboarding wrote them once on the host and the only
-- way to change what a project indexes was to edit the repository being
-- indexed. That put configuration of this service inside somebody else's tree.
--
-- The documents move here, resolved most specific first: the directory, then
-- the project, then the global default. A file still in the tree beats all
-- three - repositories already carry one, and they stop deciding an index only
-- when they are deleted. `make install` no longer writes them.
--
-- The `settings` column is empty on purpose. The auto-indexing and
-- auto-summarize knobs ROADMAP.md asks for next belong on this row, and a
-- JSONB column is what keeps them from being a migration each.

-- +goose Up

-- The built-in project the global defaults hang off, alongside _memory,
-- _plans and _suggestions. Like them it holds records rather than files, so
-- root_path gets a scheme no host path can collide with.
INSERT INTO projects (name, root_path, indexed_at, type)
VALUES ('_settings', 'settings://agent', NULL, 'settings')
ON CONFLICT (name) DO NOTHING;

CREATE TABLE IF NOT EXISTS project_settings (
    project VARCHAR(64) NOT NULL REFERENCES projects (
        name
    ) ON DELETE CASCADE,
    -- The directory these settings are for, named as in project_sources. The
    -- empty alias is the project level - and for a project mounted whole that
    -- is also its only directory, which is one row rather than two because for
    -- that project they are the same thing. ('_settings', '') is the global
    -- default every project falls back to.
    alias VARCHAR(64) NOT NULL DEFAULT '',
    -- The two documents, verbatim: comments, blank lines and all. They are
    -- parsed with the gitwildmatch rule that read them off disk, so what is
    -- stored here is what a tree would have held.
    ctxkeep TEXT,
    ctxignore TEXT,
    settings JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project, alias)
);

INSERT INTO project_settings (project, alias)
VALUES ('_settings', '')
ON CONFLICT DO NOTHING;

-- What the last index run actually read each source's selection from: 'file',
-- 'directory', 'project', 'global' or 'default'. The dashboard has no mount of
-- its own and cannot look, so the run records it. NULL until first indexed.
ALTER TABLE project_sources
ADD COLUMN IF NOT EXISTS keep_source VARCHAR(16);

ALTER TABLE project_sources
ADD COLUMN IF NOT EXISTS ignore_source VARCHAR(16);

-- +goose Down

ALTER TABLE project_sources
DROP COLUMN IF EXISTS ignore_source;

ALTER TABLE project_sources
DROP COLUMN IF EXISTS keep_source;

DROP TABLE IF EXISTS project_settings;

-- The type guard keeps a rollback from deleting a real project that took the
-- name, as the _memory row is guarded in 0006.
DELETE FROM projects
WHERE name = '_settings' AND type = 'settings';
