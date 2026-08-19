import { randomUUID } from "node:crypto";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { SSEServerTransport } from "@modelcontextprotocol/sdk/server/sse.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import type {
  CallToolRequest,
  CallToolResult,
  ListToolsResult,
} from "@modelcontextprotocol/sdk/types.js";
import express from "express";
import type { Request, Response } from "express";
import pg from "pg";

const { Pool } = pg;
const dbPool = new Pool({
  connectionString: process.env.DATABASE_URL,
});

// A pool error outside of a query (a dropped backend, for example) is emitted
// on the pool itself; without this listener node treats it as fatal.
dbPool.on("error", (err) => {
  console.error("Unexpected database pool error:", err);
});

const MAX_RESULTS = 50;
const DEFAULT_RESULTS = 20;
// A path search walks the edge table once per hop, so the ceiling is what
// keeps a question about two unrelated nodes from scanning the whole graph.
const MAX_HOPS = 10;
const DEFAULT_HOPS = 6;

// One database holds the graph of every indexed codebase, so every statement
// below is scoped to one project. A session gets its default from the address
// the client connected to (`/mcp/<project>`), and any single call can name a
// different one to read a neighbour's graph without reconnecting.
function projectDescription(sessionProject: string | null): string {
  return sessionProject === null
    ? "Project to query. Required here: this session was opened on /mcp " +
        "without naming one. list_projects returns the available names."
    : `Project to query. Defaults to "${sessionProject}"; name another ` +
        "indexed project to read its graph instead.";
}

// Plans are the exception to the paragraph above. They are written by an
// agent rather than derived from a tree, so they are held in one table for the
// whole database, tagged with a project instead of owned by one. "*" is the
// tag that means every project: it reads all of them, and it writes a plan
// that belongs to none and is listed under all.
function planScopeDescription(
  sessionProject: string | null,
  writing: boolean,
): string {
  const star = writing
    ? '"*" saves it as a global plan, listed under every project.'
    : '"*" lists the plans of every project.';
  const subject = writing ? "Project this plan is about" : "Project to list";
  return sessionProject === null
    ? `${subject}. Required here: this session was opened on /mcp without ` +
        `naming one. ${star}`
    : `${subject}. Defaults to "${sessionProject}". ${star}`;
}

// What the record is, as opposed to where it stands. A template was a status
// until the type column existed, which left it unable to be completed.
const PLAN_TYPE_DESCRIPTION =
  'What the record is, apart from its lifecycle: "plan" (the default) for ' +
  'work executed once, "template" for a form to copy, "procedure" for a ' +
  "routine run on demand. Free text, like status.";

const listToolsHandler = async (
  sessionProject: string | null,
): Promise<ListToolsResult> => {
  const project = {
    type: "string",
    description: projectDescription(sessionProject),
  };
  const planTag = {
    type: "string",
    description: planScopeDescription(sessionProject, true),
  };
  const planFilter = {
    type: "string",
    description: planScopeDescription(sessionProject, false),
  };
  return {
    tools: [
      {
        name: "list_projects",
        description:
          "List the codebases indexed in this database, with their root " +
          "paths and node counts",
        inputSchema: {
          type: "object",
          properties: {},
        },
      },
      {
        name: "drop_project",
        description:
          "Remove an indexed codebase from the database. Reports what the " +
          "drop costs and deletes nothing unless confirm is true",
        inputSchema: {
          type: "object",
          properties: {
            name: {
              type: "string",
              description:
                "Name of the project to drop. Always named explicitly, never " +
                "taken from the session, so a call cannot delete by omission",
            },
            confirm: {
              type: "boolean",
              description:
                "Perform the delete. Absent or false, the tool only reports " +
                "what would be lost",
            },
          },
          required: ["name"],
        },
      },
      {
        name: "get_code_graph_neighbors",
        description:
          "Get the related nodes and dependencies of a file or code entity",
        inputSchema: {
          type: "object",
          properties: {
            project,
            node_id: {
              type: "string",
              description: "File or node identifier, for example src/index.ts",
            },
          },
          required: ["node_id"],
        },
      },
      {
        name: "search_code_nodes",
        description: "Find code nodes by name or identifier",
        inputSchema: {
          type: "object",
          properties: {
            project,
            query: {
              type: "string",
              description: "Substring matched against the node name and id",
            },
            limit: {
              type: "number",
              description: `Maximum rows to return (default ${DEFAULT_RESULTS}, max ${MAX_RESULTS})`,
            },
          },
          required: ["query"],
        },
      },
      {
        name: "shortest_path",
        description:
          "Find the shortest chain of relations between two nodes of the graph",
        inputSchema: {
          type: "object",
          properties: {
            project,
            source_id: {
              type: "string",
              description:
                "Node the path starts from, for example src/index.ts",
            },
            target_id: {
              type: "string",
              description: "Node the path should reach",
            },
            max_hops: {
              type: "number",
              description: `Longest path to consider (default ${DEFAULT_HOPS}, max ${MAX_HOPS})`,
            },
          },
          required: ["source_id", "target_id"],
        },
      },
      {
        name: "save_node_summary",
        description:
          "Save or update a summary for a specific node in the code graph",
        inputSchema: {
          type: "object",
          properties: {
            project,
            node_id: {
              type: "string",
              description: "The unique identifier of the node (e.g. file path)",
            },
            summary: {
              type: "string",
              description: "The summary content for the node",
            },
          },
          required: ["node_id", "summary"],
        },
      },
      {
        name: "get_node_summary",
        description:
          "Retrieve the summary, file path, and type for a specific node",
        inputSchema: {
          type: "object",
          properties: {
            project,
            node_id: {
              type: "string",
              description: "The unique identifier of the node (e.g. file path)",
            },
          },
          required: ["node_id"],
        },
      },
      {
        name: "save_plan",
        description:
          "Create or update a persistent execution plan. Plans are stored " +
          "across projects and outlive the codebase they were written for",
        inputSchema: {
          type: "object",
          properties: {
            project: planTag,
            plan_id: {
              type: "string",
              description:
                "Identifier of the plan, unique across the whole database. " +
                "Saving under an existing one overwrites that plan",
            },
            title: {
              type: "string",
              description: "Title of the project plan",
            },
            content: {
              type: "string",
              description: "Detailed description/roadmap of the plan",
            },
            status: {
              type: "string",
              description:
                "Status of the plan (e.g., active, completed, archived)",
            },
            type: {
              type: "string",
              description: PLAN_TYPE_DESCRIPTION,
            },
          },
          required: ["plan_id", "title", "content"],
        },
      },
      {
        name: "get_plans",
        description:
          "Retrieve execution plans filtered by project and status. Global " +
          "plans, the ones saved without a project, are always included",
        inputSchema: {
          type: "object",
          properties: {
            project: planFilter,
            status: {
              type: "string",
              description: "Filter plans by status (default 'active')",
            },
            type: {
              type: "string",
              description:
                "Filter plans by type (default 'plan'), \"*\" for every " +
                "type. " +
                PLAN_TYPE_DESCRIPTION,
            },
          },
        },
      },
      {
        name: "drop_plan",
        description:
          "Delete a plan outright, for one written by mistake or moved " +
          "elsewhere. Finished work is retired by re-saving it as completed",
        inputSchema: {
          type: "object",
          properties: {
            plan_id: {
              type: "string",
              description:
                "Identifier of the plan to delete. It is unique across the " +
                "database, so no project is named",
            },
          },
          required: ["plan_id"],
        },
      },
      {
        name: "get_file_hash",
        description: "Get the stored MD5 hash for a file",
        inputSchema: {
          type: "object",
          properties: {
            project,
            rel_path: {
              type: "string",
              description: "The project-relative file path",
            },
          },
          required: ["rel_path"],
        },
      },
      {
        name: "clear_file_hash",
        description: "Clear the stored hash for a file, forcing a re-index",
        inputSchema: {
          type: "object",
          properties: {
            project,
            rel_path: {
              type: "string",
              description: "The project-relative file path",
            },
          },
          required: ["rel_path"],
        },
      },
      {
        name: "set_file_hash",
        description: "Manually set the MD5 hash for a file",
        inputSchema: {
          type: "object",
          properties: {
            project,
            rel_path: {
              type: "string",
              description: "The project-relative file path",
            },
            hash: {
              type: "string",
              description: "The MD5 hash to store",
            },
          },
          required: ["rel_path", "hash"],
        },
      },
      {
        name: "list_indexed_files",
        description:
          "List all files currently tracked in the file_hashes table",
        inputSchema: {
          type: "object",
          properties: {
            project,
          },
        },
      },
    ],
  };
};

/** Read a required string argument, rejecting missing and blank values. */
function requireString(
  args: Record<string, unknown> | undefined,
  key: string,
): string {
  const value = args?.[key];
  if (typeof value !== "string" || value.trim() === "") {
    throw new Error(
      `Argument "${key}" is required and must be a non-empty string`,
    );
  }
  return value;
}

/** Clamp an optional hop budget into [1, MAX_HOPS]. */
function readHops(args: Record<string, unknown> | undefined): number {
  const value = args?.max_hops;
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return DEFAULT_HOPS;
  }
  return Math.min(Math.max(Math.trunc(value), 1), MAX_HOPS);
}

/** Clamp an optional numeric limit into [1, MAX_RESULTS]. */
function readLimit(args: Record<string, unknown> | undefined): number {
  const value = args?.limit;
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return DEFAULT_RESULTS;
  }
  return Math.min(Math.max(Math.trunc(value), 1), MAX_RESULTS);
}

/** Pick the project a call is about: its own argument, else the session's. */
function readProject(
  args: Record<string, unknown> | undefined,
  sessionProject: string | null,
): string {
  const value = args?.project;
  if (typeof value === "string" && value.trim() !== "") {
    return value.trim();
  }
  if (sessionProject !== null) {
    return sessionProject;
  }
  throw new Error(
    'Argument "project" is required: this session was opened on /mcp without ' +
      "naming one. Connect to /mcp/<project> instead, or pass the argument. " +
      "list_projects returns the available names.",
  );
}

/** What project a plan call is about: its own argument, else the session's.
 *
 * `explicit` separates a caller that named "*" from one that named nothing on
 * a session without a default, which are both `null` and mean opposite things
 * when writing.
 */
type PlanScope = { explicit: boolean; project: string | null };

function readPlanScope(
  args: Record<string, unknown> | undefined,
  sessionProject: string | null,
): PlanScope {
  const value = args?.project;
  if (typeof value === "string" && value.trim() !== "") {
    const tag = value.trim();
    return { explicit: true, project: tag === "*" ? null : tag };
  }
  return { explicit: false, project: sessionProject };
}

/** Reject an unknown project by name rather than by empty result.
 *
 * A misspelled name is otherwise indistinguishable from an empty graph on
 * every read tool, and turns into a foreign key error on every write one.
 */
async function requireProject(project: string): Promise<string> {
  const res = await dbPool.query(`SELECT 1 FROM projects WHERE name = $1`, [
    project,
  ]);
  if (res.rowCount === 0) {
    const all = await dbPool.query(`SELECT name FROM projects ORDER BY name`);
    const names = all.rows.map((row) => row.name as string).join(", ");
    throw new Error(
      `No project named "${project}". Indexed: ${names || "none"}. ` +
        "Index one with `make index PROJECT=/path/to/it`.",
    );
  }
  return project;
}

// Counted per project rather than summed: one `make index` brings the derived
// rows back, nothing brings a hand-written summary back, and a plan is not
// tied to the project row at all - it is tagged with the name and stays.
const DROP_REPORT = `
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

// count() arrives as a string: node-postgres leaves bigint alone rather than
// losing precision in a number.
type DropReport = {
  root_path: string;
  indexed_at: Date | null;
  nodes: string;
  edges: string;
  hashes: string;
  embeddings: string;
  plans: string;
  summaries: string;
};

/** Render a drop, before or after it happened. */
function describeDrop(
  project: string,
  row: DropReport,
  dropped: boolean,
): string {
  const when =
    row.indexed_at === null
      ? "never indexed"
      : `indexed ${row.indexed_at.toISOString()}`;
  const lines = [
    `${dropped ? "dropped" : "project"} "${project}" (${row.root_path}, ${when})`,
    `  rebuilt by one \`make index\`: ${row.nodes} nodes, ${row.edges} edges, ` +
      `${row.hashes} file hashes, ${row.embeddings} embeddings`,
    `  not rebuilt, gone for good: ${row.summaries} manual summaries`,
    `  kept, tagged with the name: ${row.plans} plans, still readable with ` +
      `get_plans project: "${project}"`,
  ];
  if (!dropped) {
    lines.push(
      "Nothing was deleted. Call again with confirm: true to drop it.",
    );
  }
  return lines.join("\n");
}

function makeCallToolHandler(
  sessionProject: string | null,
): (request: CallToolRequest) => Promise<CallToolResult> {
  return async (request: CallToolRequest): Promise<CallToolResult> => {
    const { name, arguments: args } = request.params;

    // Errors are reported back through the tool result rather than thrown, so
    // a bad argument or a database outage does not tear down the session.
    try {
      if (name === "list_projects") {
        const res = await dbPool.query(
          `SELECT p.name, p.root_path, p.indexed_at, COUNT(n.id) AS nodes
             FROM projects AS p
             LEFT JOIN graph_nodes AS n ON n.project = p.name
            GROUP BY p.name, p.root_path, p.indexed_at
            ORDER BY p.name`,
        );

        return {
          content: [{ type: "text", text: JSON.stringify(res.rows, null, 2) }],
        };
      }

      // Handled ahead of the session default on purpose: the project to drop
      // is the one named in the call, never the one the client connected to.
      if (name === "drop_project") {
        const target = await requireProject(requireString(args, "name"));
        let confirm = false;
        if (args !== undefined && args.confirm !== undefined) {
          if (typeof args.confirm !== "boolean") {
            throw new Error('Argument "confirm" must be a boolean');
          }
          confirm = args.confirm;
        }

        if (!confirm) {
          const res = await dbPool.query<DropReport>(DROP_REPORT, [target]);
          return {
            content: [
              { type: "text", text: describeDrop(target, res.rows[0], false) },
            ],
          };
        }

        // Counted and deleted on one connection inside one transaction, so the
        // receipt cannot describe rows that were never there.
        const client = await dbPool.connect();
        try {
          await client.query("BEGIN");
          const res = await client.query<DropReport>(DROP_REPORT, [target]);
          // graph_nodes and file_hashes cascade from projects, graph_edges
          // and code_embeddings from graph_nodes. Plans have no foreign key
          // and survive the drop, which is what the report says.
          await client.query(`DELETE FROM projects WHERE name = $1`, [target]);
          await client.query("COMMIT");
          return {
            content: [
              { type: "text", text: describeDrop(target, res.rows[0], true) },
            ],
          };
        } catch (error) {
          await client.query("ROLLBACK").catch(() => undefined);
          throw error;
        } finally {
          client.release();
        }
      }

      // The three plan tools are handled ahead of the project lookup below.
      // A plan is not part of any graph: it may name a codebase this database
      // has never indexed, and it stays readable after that codebase is
      // dropped, so requiring the project to exist would refuse both.
      if (name === "save_plan") {
        const planId = requireString(args, "plan_id");
        const title = requireString(args, "title");
        const content = requireString(args, "content");
        let status = "active";
        if (args !== undefined && args.status !== undefined) {
          if (typeof args.status !== "string") {
            throw new Error('Argument "status" must be a string');
          }
          status = args.status;
        }
        let planType = "plan";
        if (args !== undefined && args.type !== undefined) {
          if (typeof args.type !== "string") {
            throw new Error('Argument "type" must be a string');
          }
          planType = args.type;
        }

        const scope = readPlanScope(args, sessionProject);
        // Storing a plan under no project at all is a decision, so it has to
        // be made rather than fallen into by omission.
        if (!scope.explicit && scope.project === null) {
          throw new Error(
            'Argument "project" is required: this session was opened on /mcp ' +
              'without naming one. Name the project the plan is about, or "*" ' +
              "to save it as a global plan.",
          );
        }

        await dbPool.query(
          `INSERT INTO plans (
           id, project, title, content, status, type, updated_at
         )
         VALUES ($1, $2, $3, $4, $5, $6, CURRENT_TIMESTAMP)
         ON CONFLICT (id) DO UPDATE SET
           project = EXCLUDED.project,
           title = EXCLUDED.title,
           content = EXCLUDED.content,
           status = EXCLUDED.status,
           type = EXCLUDED.type,
           updated_at = CURRENT_TIMESTAMP`,
          [planId, scope.project, title, content, status, planType],
        );

        const where =
          scope.project === null ? "global" : `project ${scope.project}`;
        const text = [`Plan ${planId} successfully saved (${where}).`];
        // The tag is free text with nothing to check it, so a typo would
        // otherwise store a plan under a name no session ever asks for.
        if (scope.project !== null) {
          const known = await dbPool.query(
            `SELECT 1 FROM projects WHERE name = $1`,
            [scope.project],
          );
          if (known.rowCount === 0) {
            text.push(
              `No indexed project named "${scope.project}". The plan was ` +
                "stored anyway and will be listed once one is indexed under " +
                "that name.",
            );
          }
        }

        return {
          content: [{ type: "text", text: text.join("\n") }],
        };
      }

      if (name === "get_plans") {
        let status = "active";
        if (args !== undefined && args.status !== undefined) {
          if (typeof args.status !== "string") {
            throw new Error('Argument "status" must be a string');
          }
          status = args.status;
        }
        // Defaulted like the status rather than left open: a template is
        // active for as long as it exists, and an unfiltered listing would
        // put one where an agent reads approved pending work.
        let planType: string | null = "plan";
        if (args !== undefined && args.type !== undefined) {
          if (typeof args.type !== "string") {
            throw new Error('Argument "type" must be a string');
          }
          planType = args.type === "*" ? null : args.type;
        }

        // A null project is no filter at all: either "*" was asked for, or the
        // session named no project and has none to narrow by.
        const scope = readPlanScope(args, sessionProject);
        const res = await dbPool.query(
          `SELECT id, project, title, content, status, type, metadata,
                  created_at, updated_at
             FROM plans
            WHERE ($1::text IS NULL OR project = $1 OR project IS NULL)
              AND status = $2
              AND ($3::text IS NULL OR type = $3)
            ORDER BY (project IS NULL), updated_at DESC`,
          [scope.project, status, planType],
        );

        return {
          content: [{ type: "text", text: JSON.stringify(res.rows, null, 2) }],
        };
      }

      if (name === "drop_plan") {
        const planId = requireString(args, "plan_id");
        const res = await dbPool.query<{
          project: string | null;
          title: string;
          status: string;
        }>(
          `DELETE FROM plans
            WHERE id = $1
        RETURNING project, title, status`,
          [planId],
        );

        // A typo and a delete have to read differently, or the caller cannot
        // tell which of the two just happened.
        if (res.rowCount === 0) {
          return {
            content: [
              {
                type: "text",
                text: `No plan "${planId}". Nothing was deleted.`,
              },
            ],
          };
        }

        const row = res.rows[0];
        const where =
          row.project === null ? "global" : `project ${row.project}`;
        return {
          content: [
            {
              type: "text",
              text:
                `Deleted plan ${planId} (${where}): "${row.title}", ` +
                `status ${row.status}.`,
            },
          ],
        };
      }

      const project = await requireProject(readProject(args, sessionProject));

      if (name === "get_code_graph_neighbors") {
        const nodeId = requireString(args, "node_id");
        const res = await dbPool.query(
          // The neighbour rows carry the node's type and summary, so a caller
          // learns what it found without a second lookup per id.
          `WITH neighbours AS (
           SELECT target_id AS node_id, relation_type, 'outgoing' AS direction
             FROM graph_edges WHERE project = $1 AND source_id = $2
           UNION
           SELECT source_id AS node_id, relation_type, 'incoming' AS direction
             FROM graph_edges WHERE project = $1 AND target_id = $2
         )
         SELECT n.node_id, n.relation_type, n.direction,
                g.type, g.file_path, g.summary
           FROM neighbours AS n
           LEFT JOIN graph_nodes AS g
             ON g.project = $1 AND g.id = n.node_id
          ORDER BY n.direction, n.relation_type, n.node_id
          LIMIT $3`,
          [project, nodeId, MAX_RESULTS],
        );

        return {
          content: [{ type: "text", text: JSON.stringify(res.rows, null, 2) }],
        };
      }

      if (name === "search_code_nodes") {
        const query = `%${requireString(args, "query")}%`;
        const res = await dbPool.query(
          `SELECT id, name, type, file_path, summary
           FROM graph_nodes
          WHERE project = $1 AND (name ILIKE $2 OR id ILIKE $2)
          ORDER BY id
          LIMIT $3`,
          [project, query, readLimit(args)],
        );

        return {
          content: [{ type: "text", text: JSON.stringify(res.rows, null, 2) }],
        };
      }

      if (name === "shortest_path") {
        const sourceId = requireString(args, "source_id");
        const targetId = requireString(args, "target_id");

        // Breadth first, in the database. Edges are followed in both
        // directions because the graph records who imports whom, not which way
        // a reader wants to travel, and the visited path is carried along so a
        // walk cannot loop back through a node it already used.
        const res = await dbPool.query(
          `WITH RECURSIVE walk(node_id, path, depth) AS (
             SELECT $2::VARCHAR, ARRAY[$2::VARCHAR], 0
           UNION ALL
             SELECT next.id, walk.path || next.id, walk.depth + 1
               FROM walk
               JOIN LATERAL (
                 SELECT CASE
                          WHEN e.source_id = walk.node_id THEN e.target_id
                          ELSE e.source_id
                        END AS id
                   FROM graph_edges e
                  WHERE e.project = $1
                    AND (e.source_id = walk.node_id
                      OR e.target_id = walk.node_id)
               ) AS next ON TRUE
              WHERE walk.depth < $4
                AND walk.node_id <> $3
                AND NOT (next.id = ANY (walk.path))
         )
         SELECT path, depth
           FROM walk
          WHERE node_id = $3
          ORDER BY depth
          LIMIT 1`,
          [project, sourceId, targetId, readHops(args)],
        );

        if (res.rows.length === 0) {
          return {
            content: [
              {
                type: "text",
                text: `No path from ${sourceId} to ${targetId} within the hop limit`,
              },
            ],
          };
        }

        return {
          content: [
            { type: "text", text: JSON.stringify(res.rows[0], null, 2) },
          ],
        };
      }

      if (name === "save_node_summary") {
        const nodeId = requireString(args, "node_id");
        const summary = requireString(args, "summary");
        const nameVal = nodeId.split("/").pop() || nodeId;
        const typeVal = "file";

        // The summary is tagged manual so the indexer leaves it alone; without
        // the tag the next `make index` run overwrites it with a generated one.
        await dbPool.query(
          `INSERT INTO graph_nodes (project, id, name, type, summary, metadata)
         VALUES ($1, $2, $3, $4, $5, '{"summary_source": "manual"}'::jsonb)
         ON CONFLICT (project, id) DO UPDATE SET
           summary = EXCLUDED.summary,
           metadata = graph_nodes.metadata
             || '{"summary_source": "manual"}'::jsonb`,
          [project, nodeId, nameVal, typeVal, summary],
        );

        return {
          content: [
            {
              type: "text",
              text: `Summary successfully saved for node: ${nodeId}`,
            },
          ],
        };
      }

      if (name === "get_node_summary") {
        const nodeId = requireString(args, "node_id");
        const res = await dbPool.query(
          `SELECT id, summary, file_path, type
           FROM graph_nodes
          WHERE project = $1 AND id = $2`,
          [project, nodeId],
        );

        return {
          content: [{ type: "text", text: JSON.stringify(res.rows, null, 2) }],
        };
      }

      if (name === "get_file_hash") {
        const relPath = requireString(args, "rel_path");
        const res = await dbPool.query(
          `SELECT hash, updated_at
           FROM file_hashes
          WHERE project = $1 AND file_path = $2`,
          [project, relPath],
        );

        return {
          content: [{ type: "text", text: JSON.stringify(res.rows, null, 2) }],
        };
      }

      if (name === "clear_file_hash") {
        const relPath = requireString(args, "rel_path");
        await dbPool.query(
          `DELETE FROM file_hashes WHERE project = $1 AND file_path = $2`,
          [project, relPath],
        );

        return {
          content: [
            {
              type: "text",
              text: `Hash cleared for file: ${relPath}. Re-indexing will now pick it up.`,
            },
          ],
        };
      }

      if (name === "set_file_hash") {
        const relPath = requireString(args, "rel_path");
        const hash = requireString(args, "hash");
        await dbPool.query(
          `INSERT INTO file_hashes (project, file_path, hash, updated_at)
         VALUES ($1, $2, $3, CURRENT_TIMESTAMP)
         ON CONFLICT (project, file_path) DO UPDATE SET
           hash = EXCLUDED.hash,
           updated_at = CURRENT_TIMESTAMP`,
          [project, relPath, hash],
        );

        return {
          content: [
            {
              type: "text",
              text: `Hash set for file: ${relPath}.`,
            },
          ],
        };
      }

      if (name === "list_indexed_files") {
        const res = await dbPool.query(
          `SELECT file_path, hash, updated_at
           FROM file_hashes
          WHERE project = $1
          ORDER BY updated_at DESC`,
          [project],
        );

        return {
          content: [{ type: "text", text: JSON.stringify(res.rows, null, 2) }],
        };
      }

      throw new Error(`Tool ${name} not found`);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      console.error(`Tool ${name} failed:`, message);
      return {
        content: [{ type: "text", text: `Error: ${message}` }],
        isError: true,
      };
    }
  };
}

// A Server keeps a single transport of its own, so one shared instance would let
// a second client's connection steal the first one's responses. Every session
// gets its own Server, which is also where the session's project is held: it
// comes from the address the client connected to and never changes afterwards.
function createServer(sessionProject: string | null): Server {
  const server = new Server(
    {
      name: "claude-pg-graph-mcp",
      version: "1.0.0",
    },
    {
      capabilities: {
        tools: {},
      },
    },
  );

  server.setRequestHandler(ListToolsRequestSchema, () =>
    listToolsHandler(sessionProject),
  );
  server.setRequestHandler(
    CallToolRequestSchema,
    makeCallToolHandler(sessionProject),
  );

  return server;
}

const app = express();

// One transport per SSE connection. A single shared variable would let a second
// client overwrite the first one's stream, silently breaking its session.
const transports = new Map<string, SSEServerTransport>();

// Streamable HTTP sessions, keyed by the mcp-session-id header the transport
// assigns on initialize. Kept apart from the SSE map because the two transports
// use different session identifiers.
const httpTransports = new Map<string, StreamableHTTPServerTransport>();

const PORT = Number(process.env.PORT ?? 3000);

// The project a client gets when it connects to a bare /mcp or /sse. Left
// unset, such a session has no default and every tool call has to name one;
// the address is the better place to say it, since one server now answers for
// every indexed codebase.
const DEFAULT_PROJECT = process.env.DEFAULT_PROJECT ?? null;

/** Read the project out of a route parameter, falling back to the default. */
function routeProject(req: Request): string | null {
  const value = req.params.project;
  return typeof value === "string" && value !== "" ? value : DEFAULT_PROJECT;
}

function csv(value: string | undefined): string[] {
  return (value ?? "")
    .split(",")
    .map((entry) => entry.trim())
    .filter((entry) => entry !== "");
}

// SDK 0.6 has no DNS rebinding protection of its own (GHSA-w48q-cv73-mx4w), so
// the Host and Origin checks it gained in 1.24 are done here instead. Without
// them any web page the user visits can resolve a name to loopback and drive
// this server through the browser.
const ALLOWED_HOSTS = new Set(
  csv(process.env.ALLOWED_HOSTS).length > 0
    ? csv(process.env.ALLOWED_HOSTS)
    : [
        `localhost:${PORT}`,
        `127.0.0.1:${PORT}`,
        `[::1]:${PORT}`,
        "localhost",
        "127.0.0.1",
        "mcp-server:3000",
      ],
);
// Empty by default: legitimate MCP clients send no Origin header at all, so any
// request that carries one is browser traffic and is refused.
const ALLOWED_ORIGINS = new Set(csv(process.env.ALLOWED_ORIGINS));

function guardDnsRebinding(
  req: Request,
  res: Response,
  next: express.NextFunction,
): void {
  if (process.env.ALLOWED_HOSTS !== "*") {
    const host = req.headers.host;
    if (host === undefined || !ALLOWED_HOSTS.has(host)) {
      res.status(403).send(`Host "${host ?? ""}" is not allowed`);
      return;
    }
  }

  const origin = req.headers.origin;
  if (origin !== undefined && !ALLOWED_ORIGINS.has(origin)) {
    res.status(403).send(`Origin "${origin}" is not allowed`);
    return;
  }

  next();
}

app.get("/health", async (_req: Request, res: Response) => {
  try {
    // Naming the indexed projects here is what lets `make status` answer the
    // question one server for many codebases raises: which ones are in there.
    const indexed = await dbPool.query(
      `SELECT name FROM projects ORDER BY name`,
    );
    res.json({
      status: "ok",
      sessions: transports.size + httpTransports.size,
      projects: indexed.rows.map((row) => row.name as string),
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    res.status(503).json({ status: "error", error: message });
  }
});

// The page itself is rendered by the viewer service, which shares the
// indexer image because the renderer lives there. Redirecting keeps one
// address to remember rather than two ports.
const VIEWER_URL = process.env.VIEWER_URL ?? "http://localhost:3001/graph";

// `/graph/<project>` and `/graph?project=<project>` both reach the viewer,
// which draws the one it is given and lists them all when it is given none.
function graphRedirect(req: Request, res: Response): void {
  const project = req.params.project ?? req.query.project;
  const suffix =
    typeof project === "string" && project !== ""
      ? `?project=${encodeURIComponent(project)}`
      : "";
  res.redirect(302, `${VIEWER_URL}${suffix}`);
}

app.get("/graph", graphRedirect);
app.get("/graph/:project", graphRedirect);

async function handleSse(req: Request, res: Response): Promise<void> {
  const transport = new SSEServerTransport("/message", res);
  transports.set(transport.sessionId, transport);
  res.on("close", () => {
    transports.delete(transport.sessionId);
  });

  try {
    await createServer(routeProject(req)).connect(transport);
  } catch (error) {
    console.error("Failed to establish SSE session:", error);
    transports.delete(transport.sessionId);
  }
}

app.get("/sse", guardDnsRebinding, handleSse);
app.get("/sse/:project", guardDnsRebinding, handleSse);

app.post("/message", guardDnsRebinding, async (req: Request, res: Response) => {
  const sessionId = req.query.sessionId;
  if (typeof sessionId !== "string") {
    res.status(400).send("Missing sessionId query parameter");
    return;
  }

  const transport = transports.get(sessionId);
  if (!transport) {
    res.status(404).send("Unknown session");
    return;
  }

  await transport.handlePostMessage(req, res);
});

// Streamable HTTP, the transport that replaces SSE in the current MCP spec.
// No body parser is mounted anywhere in this app: handleRequest reads the raw
// stream itself, and SSEServerTransport above breaks on an already consumed
// body, so both endpoints are left to parse their own requests.
async function handleStreamableHttp(
  req: Request,
  res: Response,
): Promise<void> {
  const sessionId = req.headers["mcp-session-id"];

  if (typeof sessionId === "string") {
    const existing = httpTransports.get(sessionId);
    if (!existing) {
      res.status(404).send("Unknown session");
      return;
    }
    await existing.handleRequest(req, res);
    return;
  }

  // Only an initialize POST may arrive without a session id. A GET or DELETE
  // without one has no session to stream from or tear down.
  if (req.method !== "POST") {
    res.status(400).send("Missing mcp-session-id header");
    return;
  }

  const transport = new StreamableHTTPServerTransport({
    sessionIdGenerator: () => randomUUID(),
    onsessioninitialized: (id) => {
      httpTransports.set(id, transport);
    },
  });
  transport.onclose = () => {
    if (transport.sessionId !== undefined) {
      httpTransports.delete(transport.sessionId);
    }
  };

  try {
    await createServer(routeProject(req)).connect(transport);
    await transport.handleRequest(req, res);
  } catch (error) {
    console.error("Failed to establish Streamable HTTP session:", error);
    if (transport.sessionId !== undefined) {
      httpTransports.delete(transport.sessionId);
    }
    if (!res.headersSent) {
      res.status(500).send("Failed to establish session");
    }
  }
}

app.post("/mcp", guardDnsRebinding, handleStreamableHttp);
app.get("/mcp", guardDnsRebinding, handleStreamableHttp);
app.delete("/mcp", guardDnsRebinding, handleStreamableHttp);
app.post("/mcp/:project", guardDnsRebinding, handleStreamableHttp);
app.get("/mcp/:project", guardDnsRebinding, handleStreamableHttp);
app.delete("/mcp/:project", guardDnsRebinding, handleStreamableHttp);

const httpServer = app.listen(PORT, "0.0.0.0", () => {
  console.log(`MCP Server running on port ${PORT}`);
});

async function shutdown(signal: string): Promise<void> {
  console.log(`Received ${signal}, shutting down`);
  httpServer.close();
  for (const transport of transports.values()) {
    await transport.close().catch(() => undefined);
  }
  transports.clear();
  for (const transport of httpTransports.values()) {
    await transport.close().catch(() => undefined);
  }
  httpTransports.clear();
  await dbPool.end().catch(() => undefined);
  process.exit(0);
}

process.on("SIGTERM", () => void shutdown("SIGTERM"));
process.on("SIGINT", () => void shutdown("SIGINT"));
