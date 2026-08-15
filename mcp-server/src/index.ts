import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { SSEServerTransport } from "@modelcontextprotocol/sdk/server/sse.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
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

server.setRequestHandler(ListToolsRequestSchema, async () => {
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
    ],
  };
});

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

/** Clamp an optional numeric limit into [1, MAX_RESULTS]. */
function readLimit(args: Record<string, unknown> | undefined): number {
  const value = args?.limit;
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return DEFAULT_RESULTS;
  }
  return Math.min(Math.max(Math.trunc(value), 1), MAX_RESULTS);
}

server.setRequestHandler(CallToolRequestSchema, async (request) => {
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

    throw new Error(`Tool ${name} not found`);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error(`Tool ${name} failed:`, message);
    return {
      content: [{ type: "text", text: `Error: ${message}` }],
      isError: true,
    };
  }
});

const app = express();

// One transport per SSE connection. A single shared variable would let a second
// client overwrite the first one's stream, silently breaking its session.
const transports = new Map<string, SSEServerTransport>();

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
    res.json({ status: "ok", sessions: transports.size });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    res.status(503).json({ status: "error", error: message });
  }
});

app.get("/sse", guardDnsRebinding, async (_req: Request, res: Response) => {
  const transport = new SSEServerTransport("/message", res);
  transports.set(transport.sessionId, transport);
  res.on("close", () => {
    transports.delete(transport.sessionId);
  });

  try {
    await server.connect(transport);
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
  await dbPool.end().catch(() => undefined);
  process.exit(0);
}

process.on("SIGTERM", () => void shutdown("SIGTERM"));
process.on("SIGINT", () => void shutdown("SIGINT"));
