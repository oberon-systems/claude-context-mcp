-- What kind of thing a project is, apart from where it lives. One database
-- holds every indexed codebase, and until now nothing distinguished a source
-- tree from a documentation repository from the agent's own notes.
--
-- Vocabulary: 'codebase' (the default), 'docs', 'config' and 'memory'. The
-- first three are all indexed trees and differ only as a search filter, which
-- is what makes a cross-project search usable. 'memory' is different in kind:
-- it is not derived from a tree at all, cannot be rebuilt by `make index`, and
-- the indexer refuses to write into it.
--
-- Left unconstrained, like plans.status and plans.type. The vocabulary is
-- documented rather than CHECK-enforced, so a new kind of project is not a
-- schema migration.

-- +goose Up

ALTER TABLE projects
ADD COLUMN IF NOT EXISTS type VARCHAR(50) NOT NULL DEFAULT 'codebase';

-- The cross-project search narrows to a type before it touches graph_nodes.
CREATE INDEX IF NOT EXISTS idx_projects_type ON projects (type);

-- The built-in project holding agent memory. It is created here rather than
-- on first write so that it is listed from the start, and root_path is NOT
-- NULL UNIQUE, so it needs a value no host path can ever collide with.
INSERT INTO projects (name, root_path, indexed_at, type)
VALUES ('_memory', 'memory://agent', NULL, 'memory')
ON CONFLICT (name) DO NOTHING;

-- +goose Down

-- graph_nodes cascades from projects, so this drops every memory with it. The
-- type guard keeps a rollback from deleting a real project that took the name.
DELETE FROM projects
WHERE name = '_memory' AND type = 'memory';

DROP INDEX IF EXISTS idx_projects_type;

ALTER TABLE projects
DROP COLUMN IF EXISTS type;
