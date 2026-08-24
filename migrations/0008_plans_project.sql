-- The built-in project holding plans, the last record type still living in a
-- table of its own.
--
-- A plan is a record rather than a tree, which is exactly what a memory and a
-- suggestion are, and every argument that put those in '_memory' and
-- '_suggestions' applies here. The separate table bought nothing and cost the
-- obvious things: plans are the only records `search_code_nodes` cannot
-- reach, the only ones a project drop has to handle by hand, and the only
-- ones backup and restore name column by column.
--
-- The plan id carries over untouched. It is already unique across the
-- database by construction - it names a topic and is written by hand - so it
-- needs none of the '<about>/<id>' scoping a memory id gets to stay unique,
-- and every id an agent has already written down keeps working.
--
-- `plans.project` was a tag and not a reference: no foreign key, and NULL
-- meaning the plan belongs to no project and is listed under all of them.
-- That is what a memory's `metadata ->> 'about'` already is, so it becomes
-- one. The plan's kind becomes the node type, which is what makes 'plan' and
-- 'template' filterable the way any other node type is.

-- +goose Up

-- root_path is NOT NULL UNIQUE, so the row needs a value no host path can
-- ever collide with, exactly as '_memory' takes 'memory://agent'.
INSERT INTO projects (name, root_path, indexed_at, type)
VALUES ('_plans', 'plans://agent', NULL, 'plans')
ON CONFLICT (name) DO NOTHING;

-- graph_nodes has no updated_at column, so the plan's lands in metadata in
-- the format the memory tools already write and read there.
INSERT INTO graph_nodes (
    project, id, name, type, content, metadata, created_at
)
SELECT
    '_plans' AS project,
    p.id,
    p.title,
    p.type,
    p.content,
    COALESCE(p.metadata, '{}'::JSONB) || JSONB_BUILD_OBJECT(
        'about', p.project,
        'status', p.status,
        'summary_source', 'manual',
        'updated_at', TO_CHAR(
            p.updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'
        )
    ) AS metadata,
    p.created_at
FROM plans AS p
ON CONFLICT (project, id) DO NOTHING;

-- Every read defaults to the active ones, so the lifecycle lives in metadata
-- and is indexed like a column, the way a suggestion's status already is.
CREATE INDEX IF NOT EXISTS idx_graph_nodes_plan
ON graph_nodes ((metadata ->> 'status'))
WHERE type IN ('plan', 'template');

-- Its own indexes go with it.
DROP TABLE IF EXISTS plans;

-- +goose Down

CREATE TABLE IF NOT EXISTS plans (
    id VARCHAR(255) PRIMARY KEY,
    project VARCHAR(64),
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    status VARCHAR(50) DEFAULT 'active',
    type VARCHAR(50) NOT NULL DEFAULT 'plan',
    metadata JSONB DEFAULT '{}'::JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_plans_project_status ON plans (project, status);
CREATE INDEX IF NOT EXISTS idx_plans_type_status ON plans (type, status);

-- The four keys the Up added are dropped again, so a plan that had metadata
-- of its own gets exactly that back.
INSERT INTO plans (
    id, project, title, content, status, type, metadata, created_at, updated_at
)
SELECT
    n.id,
    n.metadata ->> 'about' AS project,
    n.name,
    -- Aliases here are documentation; position is what binds. Not "content",
    -- which sqlfluff refuses as an identifier both bare and quoted.
    COALESCE(n.content, '') AS body,
    COALESCE(n.metadata ->> 'status', 'active') AS status,
    n.type,
    n.metadata - 'about' - 'status' - 'summary_source'
    - 'updated_at' AS metadata,
    n.created_at,
    COALESCE(
        (n.metadata ->> 'updated_at')::TIMESTAMP WITH TIME ZONE, n.created_at
    ) AS updated_at
FROM graph_nodes AS n
WHERE n.project = '_plans'
ON CONFLICT (id) DO NOTHING;

DROP INDEX IF EXISTS idx_graph_nodes_plan;

-- graph_nodes cascades from projects, so this drops every plan node with it.
-- The type guard keeps a rollback from deleting a real project that took the
-- name.
DELETE FROM projects
WHERE name = '_plans' AND type = 'plans';
