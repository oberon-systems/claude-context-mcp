-- The built-in project holding agent suggestions: what the graph or the
-- context did not have, and the concrete change that would fix it.
--
-- Until now that was prose. An agent closes a task by naming the gaps it hit -
-- a lookup that came back empty, a summary too thin to answer from, a file
-- type no parser reads - and the sentence was printed into a session and lost
-- with it. So the same gap was rediscovered every session and never
-- accumulated evidence: one reported eight times looked exactly like one
-- reported once.
--
-- Shaped like '_memory' rather than like 'plans': a suggestion is a record,
-- not a tree, so it is a graph_nodes row under a project of its own kind.
-- What it adds over a memory is a lifecycle - open, resolved, wontfix - and a
-- hit count, which is the part that ranks the backlog.
--
-- Not to be confused with the gap the indexer reports, which is a file that
-- was selected but produced no node. That one is about extraction coverage
-- and belongs to a run, not to the database.

-- +goose Up

-- root_path is NOT NULL UNIQUE, so the row needs a value no host path can
-- ever collide with, exactly as '_memory' takes 'memory://agent'.
INSERT INTO projects (name, root_path, indexed_at, type)
VALUES ('_suggestions', 'suggestions://agent', NULL, 'suggestions')
ON CONFLICT (name) DO NOTHING;

-- Every read defaults to the open ones, so the status lives in metadata but
-- is indexed like a column. Partial: no other node type carries this key.
CREATE INDEX IF NOT EXISTS idx_graph_nodes_suggestion
ON graph_nodes ((metadata ->> 'status'))
WHERE type = 'suggestion';

-- +goose Down

DROP INDEX IF EXISTS idx_graph_nodes_suggestion;

-- graph_nodes cascades from projects, so this drops every suggestion with it.
-- The type guard keeps a rollback from deleting a real project that took the
-- name.
DELETE FROM projects
WHERE name = '_suggestions' AND type = 'suggestions';
