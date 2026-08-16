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

const listToolsHandler = async (): Promise<ListToolsResult> => {
  return {
    tools: [
      {
        name: "get_code_graph_neighbors",
        description:
          "Get the related nodes and dependencies of a file or code entity",
        inputSchema: {
          type: "object",
          properties: {
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
        description: "Create or update a persistent project execution plan",
        inputSchema: {
          type: "object",
          properties: {
            plan_id: {
              type: "string",
              description: "Unique identifier for the project plan",
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
          },
          required: ["plan_id", "title", "content"],
        },
      },
      {
        name: "get_plans",
        description: "Retrieve project execution plans filtered by status",
        inputSchema: {
          type: "object",
          properties: {
            status: {
              type: "string",
              description: "Filter plans by status (default 'active')",
            },
          },
        },
      },
      {
        name: "get_file_hash",
        description: "Get the stored MD5 hash for a file",
        inputSchema: {
          type: "object",
          properties: {
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
          properties: {},
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

const callToolHandler = async (
  request: CallToolRequest,
): Promise<CallToolResult> => {
  const { name, arguments: args } = request.params;

  // Errors are reported back through the tool result rather than thrown, so a
  // bad argument or a database outage does not tear down the client session.
  try {
    if (name === "get_code_graph_neighbors") {
      const nodeId = requireString(args, "node_id");
      const res = await dbPool.query(
        // The neighbour rows carry the node's type and summary, so a caller
        // learns what it found without a second lookup per id.
        `WITH neighbours AS (
           SELECT target_id AS node_id, relation_type, 'outgoing' AS direction
             FROM graph_edges WHERE source_id = $1
           UNION
           SELECT source_id AS node_id, relation_type, 'incoming' AS direction
             FROM graph_edges WHERE target_id = $1
         )
         SELECT n.node_id, n.relation_type, n.direction,
                g.type, g.file_path, g.summary
           FROM neighbours AS n
           LEFT JOIN graph_nodes AS g ON g.id = n.node_id
          ORDER BY n.direction, n.relation_type, n.node_id
          LIMIT $2`,
        [nodeId, MAX_RESULTS],
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
          WHERE name ILIKE $1 OR id ILIKE $1
          ORDER BY id
          LIMIT $2`,
        [query, readLimit(args)],
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
             SELECT $1::VARCHAR, ARRAY[$1::VARCHAR], 0
           UNION ALL
             SELECT next.id, walk.path || next.id, walk.depth + 1
               FROM walk
               JOIN LATERAL (
                 SELECT CASE
                          WHEN e.source_id = walk.node_id THEN e.target_id
                          ELSE e.source_id
                        END AS id
                   FROM graph_edges e
                  WHERE e.source_id = walk.node_id
                     OR e.target_id = walk.node_id
               ) AS next ON TRUE
              WHERE walk.depth < $3
                AND walk.node_id <> $2
                AND NOT (next.id = ANY (walk.path))
         )
         SELECT path, depth
           FROM walk
          WHERE node_id = $2
          ORDER BY depth
          LIMIT 1`,
        [sourceId, targetId, readHops(args)],
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
        content: [{ type: "text", text: JSON.stringify(res.rows[0], null, 2) }],
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
        `INSERT INTO graph_nodes (id, name, type, summary, metadata)
         VALUES ($1, $2, $3, $4, '{"summary_source": "manual"}'::jsonb)
         ON CONFLICT (id) DO UPDATE SET
           summary = EXCLUDED.summary,
           metadata = graph_nodes.metadata
             || '{"summary_source": "manual"}'::jsonb`,
        [nodeId, nameVal, typeVal, summary],
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
          WHERE id = $1`,
        [nodeId],
      );

      return {
        content: [{ type: "text", text: JSON.stringify(res.rows, null, 2) }],
      };
    }

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

      await dbPool.query(
        `INSERT INTO project_plans (id, title, content, status, updated_at)
         VALUES ($1, $2, $3, $4, CURRENT_TIMESTAMP)
         ON CONFLICT (id) DO UPDATE SET
           title = EXCLUDED.title,
           content = EXCLUDED.content,
           status = EXCLUDED.status,
           updated_at = CURRENT_TIMESTAMP`,
        [planId, title, content, status],
      );

      return {
        content: [
          {
            type: "text",
            text: `Plan ${planId} successfully saved.`,
          },
        ],
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

      const res = await dbPool.query(
        `SELECT id, title, content, status, metadata, created_at, updated_at
           FROM project_plans
          WHERE status = $1
          ORDER BY updated_at DESC`,
        [status],
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
          WHERE file_path = $1`,
        [relPath],
      );

      return {
        content: [{ type: "text", text: JSON.stringify(res.rows, null, 2) }],
      };
    }

    if (name === "clear_file_hash") {
      const relPath = requireString(args, "rel_path");
      await dbPool.query(`DELETE FROM file_hashes WHERE file_path = $1`, [
        relPath,
      ]);

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
        `INSERT INTO file_hashes (file_path, hash, updated_at)
         VALUES ($1, $2, CURRENT_TIMESTAMP)
         ON CONFLICT (file_path) DO UPDATE SET
           hash = EXCLUDED.hash,
           updated_at = CURRENT_TIMESTAMP`,
        [relPath, hash],
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
          ORDER BY updated_at DESC`,
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

// A Server keeps a single transport of its own, so one shared instance would let
// a second client's connection steal the first one's responses. Every session
// gets its own Server; the handlers themselves are stateless and reused.
function createServer(): Server {
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

  server.setRequestHandler(ListToolsRequestSchema, listToolsHandler);
  server.setRequestHandler(CallToolRequestSchema, callToolHandler);

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
    await dbPool.query("SELECT 1");
    res.json({
      status: "ok",
      sessions: transports.size + httpTransports.size,
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

app.get("/graph", (_req: Request, res: Response) => {
  res.redirect(302, VIEWER_URL);
});

app.get("/sse", guardDnsRebinding, async (_req: Request, res: Response) => {
  const transport = new SSEServerTransport("/message", res);
  transports.set(transport.sessionId, transport);
  res.on("close", () => {
    transports.delete(transport.sessionId);
  });

  try {
    await createServer().connect(transport);
  } catch (error) {
    console.error("Failed to establish SSE session:", error);
    transports.delete(transport.sessionId);
  }
});

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
    await createServer().connect(transport);
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
