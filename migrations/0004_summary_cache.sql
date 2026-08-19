-- What the summarizing model answered, keyed by the text it was shown.
--
-- The graphifyy pass drops and rewrites every node it owns on each run, so a
-- file node's summary does not survive an index run the way a row of
-- file_hashes does. Without this table a re-index would send the whole code
-- corpus back through the model to arrive at the same sentences.
--
-- Keyed on the hash of the exact text handed to the model rather than on the
-- path: two identical files cost one generation, and a file that moved keeps
-- its summary.

-- +goose Up

CREATE TABLE IF NOT EXISTS summary_cache (
    project VARCHAR(64) NOT NULL REFERENCES projects (
        name
    ) ON DELETE CASCADE,
    content_hash VARCHAR(32) NOT NULL,
    summary TEXT NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project, content_hash)
);

-- +goose Down
DROP TABLE IF EXISTS summary_cache;
