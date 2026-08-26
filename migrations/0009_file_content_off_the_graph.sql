-- A file node stops carrying a copy of its file.
--
-- The column held a 16000 character slice of every indexed file, and it
-- existed for one reason: a single tree was mounted at a time, so a pass over
-- several projects and a worker on another machine had nowhere else to read
-- from. Every tree is now mounted read-only at /code/<project>, and the API
-- serves file text from there, so the copy is a second source of truth that
-- silently goes stale and truncates what it holds.
--
-- The column itself stays. Memories, suggestions and plans are records rather
-- than files, and their bodies live in it.
--
-- Nothing is lost that a re-index would not rebuild: the text was a copy, and
-- the summaries written from it are on the nodes, not here.

-- +goose Up

UPDATE graph_nodes SET content = NULL
WHERE type = 'file' AND content IS NOT NULL;

-- +goose Down

-- The text cannot be put back from here - it came from files this database
-- has never seen. Re-indexing under the older code is what refills it, which
-- is why the rollback only says so rather than pretending.
SELECT 1;
