// Every SQL string the dashboard runs.
//
// The graph and plan queries are deliberate copies of the MCP tools in
// mcp-server/src/index.ts: the two services share a schema, not a process. A
// column added to `plans` touches this file, that one, and scripts/backup.sh.

export const PROJECTS = `
  SELECT p.name, p.root_path, p.indexed_at,
         EXTRACT(EPOCH FROM (now() - p.indexed_at)) AS stale_seconds,
         (SELECT count(*) FROM graph_nodes AS g
           WHERE g.project = p.name) AS nodes,
         (SELECT count(*) FROM graph_edges AS e
           WHERE e.project = p.name) AS edges,
         (SELECT count(*) FROM graph_nodes AS g
           WHERE g.project = p.name AND g.type = 'file') AS files,
         (SELECT count(*) FROM plans AS l
           WHERE l.project = p.name) AS plans
    FROM projects AS p
   ORDER BY p.name`;

export const PROJECT = `
  SELECT p.name, p.root_path, p.indexed_at,
         EXTRACT(EPOCH FROM (now() - p.indexed_at)) AS stale_seconds,
         (SELECT count(*) FROM graph_nodes AS g
           WHERE g.project = p.name) AS nodes,
         (SELECT count(*) FROM graph_edges AS e
           WHERE e.project = p.name) AS edges,
         (SELECT count(*) FROM graph_nodes AS g
           WHERE g.project = p.name AND g.type = 'file') AS files,
         (SELECT count(*) FROM plans AS l
           WHERE l.project = p.name) AS plans
    FROM projects AS p
   WHERE p.name = $1`;

export const PROJECT_NODE_TYPES = `
  SELECT type, count(*) AS count
    FROM graph_nodes
   WHERE project = $1
   GROUP BY type
   ORDER BY count DESC, type`;

export const PROJECT_RELATIONS = `
  SELECT relation_type, count(*) AS count
    FROM graph_edges
   WHERE project = $1
   GROUP BY relation_type
   ORDER BY count DESC, relation_type`;

export const PROJECT_EXTRAS = `
  SELECT (SELECT count(*) FROM graph_nodes AS g
           WHERE g.project = $1
             AND g.metadata ->> 'summary_source' = 'manual') AS manual_summaries,
         (SELECT count(*) FROM graph_nodes AS g
           WHERE g.project = $1 AND g.summary IS NOT NULL
             AND g.summary <> '') AS summarised,
         (SELECT count(*) FROM file_hashes AS f
           WHERE f.project = $1) AS hashed_files,
         (SELECT count(*) FROM code_embeddings AS c
           WHERE c.project = $1) AS embeddings`;

// Mirrors DROP_REPORT in mcp-server/src/index.ts, so the modal and
// `make unindex` cannot disagree about what a drop costs.
export const DROP_REPORT = `
  SELECT p.root_path, p.indexed_at,
         (SELECT count(*) FROM graph_nodes AS g
           WHERE g.project = p.name) AS nodes,
         (SELECT count(*) FROM graph_edges AS e
           WHERE e.project = p.name) AS edges,
         (SELECT count(*) FROM file_hashes AS f
           WHERE f.project = p.name) AS hashes,
         (SELECT count(*) FROM code_embeddings AS c
           WHERE c.project = p.name) AS embeddings,
         (SELECT count(*) FROM plans AS l
           WHERE l.project = p.name) AS plans,
         (SELECT count(*) FROM graph_nodes AS g
           WHERE g.project = p.name
             AND g.metadata ->> 'summary_source' = 'manual') AS summaries
    FROM projects AS p
   WHERE p.name = $1`;

export const PROJECT_EXISTS = `SELECT 1 FROM projects WHERE name = $1`;

export const DROP_PROJECT = `DELETE FROM projects WHERE name = $1`;

// $2 is the ILIKE pattern or null, $3 the node type, $4 the file path.
export const NODES = `
  SELECT id, name, type, file_path, summary,
         count(*) OVER () AS total
    FROM graph_nodes
   WHERE project = $1
     AND ($2::text IS NULL OR name ILIKE $2 OR id ILIKE $2)
     AND ($3::text IS NULL OR type = $3)
     AND ($4::text IS NULL OR file_path = $4)
   ORDER BY id
   LIMIT $5 OFFSET $6`;

export const NODE = `
  SELECT id, name, type, file_path, summary, metadata, created_at,
         length(content) AS content_length
    FROM graph_nodes
   WHERE project = $1 AND id = $2`;

export const NODE_CONTENT = `
  SELECT substring(content FROM 1 FOR $3) AS content
    FROM graph_nodes
   WHERE project = $1 AND id = $2`;

// The union of both directions, as get_code_graph_neighbors builds it, with
// $3 narrowing it to one and a window count so the page can be walked.
export const NEIGHBORS = `
  WITH neighbours AS (
    SELECT target_id AS node_id, relation_type, 'outgoing' AS direction
      FROM graph_edges
     WHERE project = $1 AND source_id = $2 AND $3::text <> 'in'
    UNION
    SELECT source_id AS node_id, relation_type, 'incoming' AS direction
      FROM graph_edges
     WHERE project = $1 AND target_id = $2 AND $3::text <> 'out'
  )
  SELECT n.node_id, n.relation_type, n.direction,
         g.type, g.file_path, g.summary,
         count(*) OVER () AS total
    FROM neighbours AS n
    LEFT JOIN graph_nodes AS g ON g.project = $1 AND g.id = n.node_id
   ORDER BY n.direction, n.relation_type, n.node_id
   LIMIT $4 OFFSET $5`;

// File nodes, not file_hashes: hashes are written only for the parsers in the
// ctxgraph package, so that table is not an inventory of the indexed tree.
export const FILES = `
  SELECT f.id, f.file_path, f.summary,
         (SELECT count(*) FROM graph_nodes AS e
           WHERE e.project = $1 AND e.file_path = f.file_path
             AND e.type <> 'file') AS entities,
         h.hash, h.updated_at AS hash_updated_at,
         count(*) OVER () AS total
    FROM graph_nodes AS f
    LEFT JOIN file_hashes AS h
      ON h.project = $1 AND h.file_path = f.id
   WHERE f.project = $1 AND f.type = 'file'
     AND ($2::text IS NULL OR f.id ILIKE $2)
   ORDER BY f.id
   LIMIT $3 OFFSET $4`;

// $1 is the project tag or null for every project, $2 the literal string
// '_global' switch, $3 status, $4 type, $5 the search pattern.
export const PLANS = `
  SELECT id, project, title, status, type, metadata,
         created_at, updated_at, length(content) AS content_length,
         count(*) OVER () AS total
    FROM plans
   WHERE ($1::text IS NULL OR project = $1)
     AND ($2::boolean IS NOT TRUE OR project IS NULL)
     AND ($3::text IS NULL OR status = $3)
     AND ($4::text IS NULL OR type = $4)
     AND ($5::text IS NULL OR title ILIKE $5 OR content ILIKE $5)
   ORDER BY (project IS NULL), updated_at DESC
   LIMIT $6 OFFSET $7`;

export const PLAN_FACETS = `
  SELECT
    (SELECT array_agg(DISTINCT project) FROM plans
      WHERE project IS NOT NULL) AS projects,
    (SELECT array_agg(DISTINCT status) FROM plans) AS statuses,
    (SELECT array_agg(DISTINCT type) FROM plans) AS types,
    (SELECT count(*) FROM plans WHERE project IS NULL) AS global_plans`;

export const PLAN = `
  SELECT id, project, title, content, status, type, metadata,
         created_at, updated_at
    FROM plans
   WHERE id = $1`;

// The same upsert save_plan runs in mcp-server/src/index.ts. Keep the two in
// step: a plan written here has to read back identically from the agent.
export const SAVE_PLAN = `
  INSERT INTO plans (id, project, title, content, status, type, updated_at)
  VALUES ($1, $2, $3, $4, $5, $6, CURRENT_TIMESTAMP)
  ON CONFLICT (id) DO UPDATE SET
    project = EXCLUDED.project,
    title = EXCLUDED.title,
    content = EXCLUDED.content,
    status = EXCLUDED.status,
    type = EXCLUDED.type,
    updated_at = CURRENT_TIMESTAMP
  RETURNING (xmax = 0) AS created`;

// COALESCE rather than a rebuilt statement: the list view changes one field
// at a time and has no reason to ship a whole plan back to do it.
export const PATCH_PLAN = `
  UPDATE plans
     SET title = COALESCE($2, title),
         content = COALESCE($3, content),
         status = COALESCE($4, status),
         type = COALESCE($5, type),
         project = CASE WHEN $6::boolean THEN $7 ELSE project END,
         updated_at = CURRENT_TIMESTAMP
   WHERE id = $1
  RETURNING id, project, title, content, status, type, metadata,
            created_at, updated_at`;

export const DROP_PLAN = `
  DELETE FROM plans
   WHERE id = $1
  RETURNING id, project, title, status, type`;
