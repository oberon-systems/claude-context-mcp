-- Schema for the code graph and its vector embeddings.
-- Replayed by the postgres entrypoint only when the data directory is empty.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS graph_nodes (
    id VARCHAR(255) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    -- One of: file, class, function, table, external_import.
    type VARCHAR(50) NOT NULL,
    file_path TEXT,
    content TEXT,
    summary TEXT,
    metadata JSONB DEFAULT '{}'::JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS graph_edges (
    id SERIAL PRIMARY KEY,
    source_id VARCHAR(255) REFERENCES graph_nodes (id) ON DELETE CASCADE,
    target_id VARCHAR(255) REFERENCES graph_nodes (id) ON DELETE CASCADE,
    -- One of: imports, calls, inherits, uses.
    relation_type VARCHAR(50) NOT NULL,
    metadata JSONB DEFAULT '{}'::JSONB,
    UNIQUE (source_id, target_id, relation_type)
);

CREATE TABLE IF NOT EXISTS code_embeddings (
    id SERIAL PRIMARY KEY,
    node_id VARCHAR(255) REFERENCES graph_nodes (id) ON DELETE CASCADE,
    content_chunk TEXT NOT NULL,
    -- 1536 dimensions, matching OpenAI / Cohere / Nomic embedding models.
    embedding VECTOR(1536),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS project_plans (
    id VARCHAR(255) PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    status VARCHAR(50) DEFAULT 'active', -- e.g. active, completed, archived
    metadata JSONB DEFAULT '{}'::JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_graph_nodes_type ON graph_nodes (type);
CREATE INDEX IF NOT EXISTS idx_graph_nodes_file_path ON graph_nodes (file_path);
CREATE INDEX IF NOT EXISTS idx_graph_edges_source ON graph_edges (source_id);
CREATE INDEX IF NOT EXISTS idx_graph_edges_target ON graph_edges (target_id);
CREATE INDEX IF NOT EXISTS idx_code_embeddings_node ON code_embeddings (node_id);

-- HNSW over cosine distance: the intended lookup is `embedding <=> query`.
-- Unlike ivfflat this index does not need training data, so it can be created
-- before any embedding rows exist.
CREATE INDEX IF NOT EXISTS idx_code_embeddings_vector
ON code_embeddings USING hnsw (embedding vector_cosine_ops);
