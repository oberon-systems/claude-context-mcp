-- Schema for the code graph and its vector embeddings.
-- Applied by goose; see migrations/Makefile.

-- +goose Up

CREATE EXTENSION IF NOT EXISTS vector;

-- One row per indexed codebase. Every other table is scoped to it, which is
-- what lets one database serve several projects and lets an agent working in
-- one of them read the graph of another.
--
-- The name is what addresses a project everywhere else: in the `project`
-- argument of the MCP tools and in the `/mcp/<name>` endpoint a client
-- connects to. It is derived from the last segment of the root path, so
-- root_path is UNIQUE to keep two different checkouts from claiming one name
-- and silently merging their graphs.
CREATE TABLE IF NOT EXISTS projects (
    name VARCHAR(64) PRIMARY KEY,
    root_path TEXT NOT NULL UNIQUE,
    indexed_at TIMESTAMP WITH TIME ZONE
);

CREATE TABLE IF NOT EXISTS graph_nodes (
    project VARCHAR(64) NOT NULL REFERENCES projects (
        name
    ) ON DELETE CASCADE,
    -- Unique within a project, not across the database: `README.md` is a node
    -- id in every codebase there is, which is why the key is composite.
    id VARCHAR(255) NOT NULL,
    name VARCHAR(255) NOT NULL,
    -- What the node stands for: file, or an entity kind reported by the
    -- parser (function, method, class, interface, type, struct, trait, enum,
    -- block, target, table, key, heading, image, stage, and play, task,
    -- handler, variable for Ansible, and define, node, resource for Puppet),
    -- or a placeholder for something outside the tree (external_import,
    -- external_symbol).
    type VARCHAR(50) NOT NULL,
    file_path TEXT,
    content TEXT,
    summary TEXT,
    -- metadata carries 'summary_source' - 'auto' for a generated summary,
    -- 'manual' for one written through the MCP save_node_summary tool, and the
    -- indexer only overwrites the former - plus 'source' ('native'/'graphifyy').
    metadata JSONB DEFAULT '{}'::JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project, id)
);

-- Both endpoints of an edge are in the same project. Nothing can produce a
-- cross-project edge today: the indexer is handed one tree at a time and
-- resolves every target inside it, so an edge reaching out of the project
-- would have no way to be discovered. Keeping the constraint single-project
-- makes that explicit rather than leaving a column that is always equal.
CREATE TABLE IF NOT EXISTS graph_edges (
    id SERIAL PRIMARY KEY,
    project VARCHAR(64) NOT NULL,
    source_id VARCHAR(255) NOT NULL,
    target_id VARCHAR(255) NOT NULL,
    -- What the source does with the target: imports, calls, inherits, or,
    -- for Ansible, includes, uses_role, depends_on, reads_vars, uses_template,
    -- uses_file, notifies. Puppet reuses includes, uses_template and notifies,
    -- and adds requires for both `require` and the -> ordering chains.
    relation_type VARCHAR(50) NOT NULL,
    metadata JSONB DEFAULT '{}'::JSONB,
    FOREIGN KEY (project, source_id) REFERENCES graph_nodes (
        project, id
    ) ON DELETE CASCADE,
    FOREIGN KEY (project, target_id) REFERENCES graph_nodes (
        project, id
    ) ON DELETE CASCADE,
    UNIQUE (project, source_id, target_id, relation_type)
);

CREATE TABLE IF NOT EXISTS code_embeddings (
    id SERIAL PRIMARY KEY,
    project VARCHAR(64) NOT NULL,
    node_id VARCHAR(255) NOT NULL,
    content_chunk TEXT NOT NULL,
    -- 1536 dimensions, matching OpenAI / Cohere / Nomic embedding models.
    embedding VECTOR(1536),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project, node_id) REFERENCES graph_nodes (
        project, id
    ) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS project_plans (
    project VARCHAR(64) NOT NULL REFERENCES projects (
        name
    ) ON DELETE CASCADE,
    id VARCHAR(255) NOT NULL,
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    status VARCHAR(50) DEFAULT 'active', -- e.g. active, completed, archived
    metadata JSONB DEFAULT '{}'::JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project, id)
);

CREATE TABLE IF NOT EXISTS file_hashes (
    project VARCHAR(64) NOT NULL REFERENCES projects (
        name
    ) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    hash VARCHAR(32) NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project, file_path)
);

-- Every lookup is inside one project, so the project column leads each index.
CREATE INDEX IF NOT EXISTS idx_graph_nodes_type ON graph_nodes (project, type);
CREATE INDEX IF NOT EXISTS idx_graph_nodes_file_path ON graph_nodes (
    project, file_path
);
CREATE INDEX IF NOT EXISTS idx_graph_edges_source ON graph_edges (
    project, source_id
);
CREATE INDEX IF NOT EXISTS idx_graph_edges_target ON graph_edges (
    project, target_id
);
CREATE INDEX IF NOT EXISTS idx_code_embeddings_node ON code_embeddings (
    project, node_id
);

-- HNSW over cosine distance: the intended lookup is `embedding <=> query`.
-- Unlike ivfflat this index does not need training data, so it can be created
-- before any embedding rows exist.
CREATE INDEX IF NOT EXISTS idx_code_embeddings_vector
ON code_embeddings USING hnsw (embedding vector_cosine_ops);

-- +goose Down
-- Dropped in reference order. The vector extension stays: dropping it takes
-- the type with it, and nothing else in this database owns it.
DROP TABLE IF EXISTS file_hashes;
DROP TABLE IF EXISTS project_plans;
DROP TABLE IF EXISTS code_embeddings;
DROP TABLE IF EXISTS graph_edges;
DROP TABLE IF EXISTS graph_nodes;
DROP TABLE IF EXISTS projects;
