-- Plans become global: one table for the whole database, with the project as
-- a tag rather than an owner.
--
-- Every other table here is derived from a tree and comes back with one
-- `make index`, so scoping it to a row of `projects` and cascading the delete
-- costs nothing. A plan is written by an agent and rebuilt by nobody, so the
-- same scoping destroyed it on `make unindex`, kept it from being written
-- before its codebase was indexed, and left no way to store a procedure that
-- belongs to no single project.

-- +goose Up

CREATE TABLE IF NOT EXISTS plans (
    -- Unique across the database, unlike the node ids: a plan id is written by
    -- hand and names a topic, not a path.
    id VARCHAR(255) PRIMARY KEY,
    -- A tag, not a reference. No foreign key, so a plan outlives the project
    -- it was written under and can name a repository nothing has indexed yet.
    -- NULL means the plan is global and is listed under every project.
    project VARCHAR(64),
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    status VARCHAR(50) DEFAULT 'active', -- e.g. active, completed, archived
    metadata JSONB DEFAULT '{}'::JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Two projects holding one plan id abort this migration on the primary key
-- rather than have one of them silently win. The fix is to rename one id and
-- migrate again.
INSERT INTO plans (
    id, project, title, content, status, metadata, created_at, updated_at
)
SELECT
    id,
    project,
    title,
    content,
    status,
    metadata,
    created_at,
    updated_at
FROM project_plans;

DROP TABLE project_plans;

CREATE INDEX IF NOT EXISTS idx_plans_project_status ON plans (project, status);

-- +goose Down
-- The old table cannot hold a global plan: its project column is part of the
-- primary key and references projects. Rolling back therefore drops every plan
-- that has no project, and every plan whose project has since been dropped -
-- outliving that row is the whole point of the column this rollback removes.
CREATE TABLE IF NOT EXISTS project_plans (
    project VARCHAR(64) NOT NULL REFERENCES projects (
        name
    ) ON DELETE CASCADE,
    id VARCHAR(255) NOT NULL,
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    status VARCHAR(50) DEFAULT 'active',
    metadata JSONB DEFAULT '{}'::JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project, id)
);

INSERT INTO project_plans (
    project, id, title, content, status, metadata, created_at, updated_at
)
SELECT
    project,
    id,
    title,
    content,
    status,
    metadata,
    created_at,
    updated_at
FROM plans
WHERE project IN (SELECT projects.name FROM projects);

DROP TABLE plans;
