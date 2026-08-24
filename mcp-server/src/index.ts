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

// The built-in project holding what an agent wrote down rather than what an
// indexer derived. It is a project so the graph tools already read it; it is
// named apart so nothing indexes a tree into it.
const MEMORY_PROJECT = "_memory";

// The built-in project holding what the graph could not answer: a lookup that
// came back empty, a summary too thin to use, a file type no parser reads.
// Kept apart from memory because a suggestion is a defect of the tooling
// rather than a fact about a codebase, and so it has a lifecycle and a count.
const SUGGESTIONS_PROJECT = "_suggestions";

// The built-in project holding plans. A plan id names a topic and is written
// by hand, so it is already unique across the database and is stored as the
// node id unchanged - none of the `<about>/<id>` scoping a memory needs. The
// project a plan is about is a tag in `metadata ->> 'about'`, exactly as a
// memory's is, and NULL there means the plan belongs to none.
const PLANS_PROJECT = "_plans";

// A record's node id is `<about>/<id>`, which is what keeps two repositories
// from colliding on `commit-style`. A record about no repository in
// particular needs a segment of its own, and "*" is not one.
const GLOBAL_SCOPE = "_global";

// What a project is, apart from where it lives. Free text in the database, so
// this list is what the tools advertise rather than what they enforce.
const PROJECT_TYPES =
  '"codebase" (the default), "docs", "config" - all indexed trees, differing ' +
  'only as a search filter - and "memory" and "suggestions", which are not ' +
  "trees at all: the built-in " +
  MEMORY_PROJECT +
  " and " +
  SUGGESTIONS_PROJECT +
  " hold records written through save_memory and save_suggestion.";

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

// Searching is the one graph read that is not confined to a project: "*"
// spans the database, and a project type narrows that span to the half of it
// worth reading.
function searchScopeDescription(sessionProject: string | null): string {
  const base =
    sessionProject === null
      ? "Project to search. This session was opened on /mcp without naming " +
        "one, so a search here spans every project unless one is named."
      : `Project to search. Defaults to "${sessionProject}".`;
  return (
    base + ' "*" searches every project; pass project_type to narrow that.'
  );
}

// A memory or a suggestion is tagged with what it is about, the way a plan
// is: the repository it applies to, or "*" for one that applies to none in
// particular. A read always sees the global records on top of whatever scope
// it named.
function recordScopeDescription(
  sessionProject: string | null,
  noun: string,
): string {
  const base =
    sessionProject === null
      ? `What this ${noun} is about. Required when writing: this session was ` +
        "opened on /mcp without naming a project."
      : `What this ${noun} is about. Defaults to "${sessionProject}".`;
  return (
    base +
    ` "*" is a ${noun} about no project in particular, and those are read ` +
    "alongside every scope."
  );
}

// What the record is, as opposed to where it stands. A template was a status
// until the type column existed, which left it unable to be completed.
const PLAN_TYPE_DESCRIPTION =
  'What the record is, apart from its lifecycle: "plan" (the default) for ' +
  'work executed once, "template" for a form to copy, "procedure" for a ' +
  "routine run on demand. Free text, like status.";

// The three vocabularies a suggestion carries. Free text in the database, so
// these are what the tools advertise rather than what they enforce.
const SUGGESTION_KIND_DESCRIPTION =
  'What kind of gap this is: "empty-lookup" (a query that found nothing ' +
  'that exists), "missing-summary", "thin-summary" (one that reads like a ' +
  'summary and answers nothing), "not-indexed", "no-parser", "stale-index", ' +
  '"missing-tool". Free text';

const SUGGESTION_LEVER_DESCRIPTION =
  'Which lever closing this gap moves: "tokens" (the answer was re-derived ' +
  'by hand), "coverage" (the graph does not describe it at all) or ' +
  '"runtime" (it was answerable, but slowly). Free text';

const SUGGESTION_STATUS_DESCRIPTION =
  'Where this stands: "open" (the default), "resolved" once the change ' +
  'landed, "wontfix" when it will not. Free text, like a plan\'s status';

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
  const memoryScope = {
    type: "string",
    description: recordScopeDescription(sessionProject, "memory"),
  };
  const suggestionScope = {
    type: "string",
    description: recordScopeDescription(sessionProject, "suggestion"),
  };
  return {
    tools: [
      {
        name: "list_projects",
        description:
          "List the projects in this database, with their type, root paths " +
          "and node counts. Types are " +
          PROJECT_TYPES,
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
        description:
          "Find nodes by name or identifier, in one project or across every " +
          "project of a kind",
        inputSchema: {
          type: "object",
          properties: {
            project: {
              type: "string",
              description: searchScopeDescription(sessionProject),
            },
            project_type: {
              type: "string",
              description:
                "Search every project of this kind instead of one project. " +
                "Cannot be combined with a named project. Types are " +
                PROJECT_TYPES,
            },
            query: {
              type: "string",
              description: "Substring matched against the node name and id",
            },
            limit: {
              type: "number",
              description: `Maximum rows to return (default ${DEFAULT_RESULTS}, max ${MAX_RESULTS}). A search spanning projects splits it between them rather than spending it all on the first`,
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
        name: "save_memory",
        description:
          "Write down something worth keeping across sessions: a convention, " +
          "a decision, a fact about how a repository works. Stored in the " +
          "built-in " +
          MEMORY_PROJECT +
          " project, not derived from any tree, so nothing re-indexes it away",
        inputSchema: {
          type: "object",
          properties: {
            about: memoryScope,
            memory_id: {
              type: "string",
              description:
                "Short slug naming the topic, for example " +
                '"commit-style". Unique within one `about` scope; saving ' +
                "under an existing one overwrites that memory",
            },
            title: {
              type: "string",
              description: "One line naming what this is",
            },
            text: {
              type: "string",
              description: "The memory itself",
            },
            summary: {
              type: "string",
              description:
                "One-line gist, which is what listings and searches show. " +
                "Taken from the title when absent",
            },
            tags: {
              type: "array",
              items: { type: "string" },
              description: "Free-text labels a later read can filter on",
            },
          },
          required: ["memory_id", "title", "text"],
        },
      },
      {
        name: "get_memory",
        description:
          "Read stored memories in full. Without a memory_id it lists the " +
          "scope, and memories about no project in particular are always " +
          "included",
        inputSchema: {
          type: "object",
          properties: {
            about: memoryScope,
            memory_id: {
              type: "string",
              description:
                "Read one memory. Either the slug, which is looked up in " +
                'the named scope and then globally, or a full "<about>/<slug>" id',
            },
            tags: {
              type: "array",
              items: { type: "string" },
              description: "Keep only memories carrying all of these tags",
            },
            query: {
              type: "string",
              description:
                "Substring matched against the title, the gist and the text",
            },
            limit: {
              type: "number",
              description: `Maximum memories to return (default ${DEFAULT_RESULTS}, max ${MAX_RESULTS})`,
            },
          },
        },
      },
      {
        name: "drop_memory",
        description:
          "Delete a memory that turned out to be wrong or is no longer true",
        inputSchema: {
          type: "object",
          properties: {
            memory_id: {
              type: "string",
              description:
                'Full "<about>/<slug>" id, as get_memory reports it. A bare ' +
                "slug is resolved against `about`",
            },
            about: memoryScope,
          },
          required: ["memory_id"],
        },
      },
      {
        name: "save_suggestion",
        description:
          "Record a gap: something the graph or the context did not have, " +
          "and the concrete change that would fix it. Saving under an id " +
          "that already exists is how a gap is reported again - it keeps the " +
          "first sighting, moves the last, counts the hit, and reopens a " +
          "suggestion that had been resolved. Stored in the built-in " +
          SUGGESTIONS_PROJECT +
          " project",
        inputSchema: {
          type: "object",
          properties: {
            about: suggestionScope,
            suggestion_id: {
              type: "string",
              description:
                'Short slug naming the gap, for example "hcl-no-parser". ' +
                "Derive it from the gap itself and keep it stable: that is " +
                "what makes the second report count rather than duplicate. " +
                "Unique within one `about` scope",
            },
            title: {
              type: "string",
              description: "One line naming what was missing",
            },
            detail: {
              type: "string",
              description:
                "What was missing, what had to be done by hand instead, and " +
                "the concrete change that would remove the need",
            },
            summary: {
              type: "string",
              description:
                "One-line gist, which is what listings show. Taken from the " +
                "title when absent",
            },
            kind: { type: "string", description: SUGGESTION_KIND_DESCRIPTION },
            lever: {
              type: "string",
              description: SUGGESTION_LEVER_DESCRIPTION,
            },
            status: {
              type: "string",
              description: SUGGESTION_STATUS_DESCRIPTION,
            },
            bump: {
              type: "boolean",
              description:
                "Whether this call is a fresh sighting (default true). Pass " +
                "false to correct the wording or set the status without " +
                "counting a hit",
            },
          },
          required: ["suggestion_id", "title", "detail"],
        },
      },
      {
        name: "get_suggestions",
        description:
          "Read recorded gaps, the most often hit first. Defaults to the " +
          "open ones, and gaps about no project in particular are always " +
          "included",
        inputSchema: {
          type: "object",
          properties: {
            about: suggestionScope,
            suggestion_id: {
              type: "string",
              description:
                "Read one suggestion. Either the slug, which is looked up " +
                "in the named scope and then globally, or a full " +
                '"<about>/<slug>" id',
            },
            status: {
              type: "string",
              description:
                SUGGESTION_STATUS_DESCRIPTION +
                '. Defaults to "open"; "*" reads every status',
            },
            kind: {
              type: "string",
              description: "Keep only gaps of this kind",
            },
            query: {
              type: "string",
              description:
                "Substring matched against the title, the gist and the detail",
            },
            limit: {
              type: "number",
              description: `Maximum suggestions to return (default ${DEFAULT_RESULTS}, max ${MAX_RESULTS})`,
            },
          },
        },
      },
      {
        name: "drop_suggestion",
        description:
          "Delete a suggestion written by mistake. A gap that has since been " +
          'closed is retired instead, with save_suggestion status: "resolved" ' +
          "and bump: false, so the count it accumulated is not lost",
        inputSchema: {
          type: "object",
          properties: {
            suggestion_id: {
              type: "string",
              description:
                'Full "<about>/<slug>" id, as get_suggestions reports it. A ' +
                "bare slug is resolved against `about`",
            },
            about: suggestionScope,
          },
          required: ["suggestion_id"],
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

/** Read an optional string argument, rejecting a present but wrong-typed one. */
function readOptionalString(
  args: Record<string, unknown> | undefined,
  key: string,
): string | null {
  const value = args?.[key];
  if (value === undefined || value === null) {
    return null;
  }
  if (typeof value !== "string") {
    throw new Error(`Argument "${key}" must be a string`);
  }
  return value.trim() === "" ? null : value.trim();
}

/** Read an optional array-of-strings argument. */
function readTags(
  args: Record<string, unknown> | undefined,
  key: string,
): string[] | null {
  const value = args?.[key];
  if (value === undefined || value === null) {
    return null;
  }
  if (!Array.isArray(value) || value.some((tag) => typeof tag !== "string")) {
    throw new Error(`Argument "${key}" must be an array of strings`);
  }
  const tags = (value as string[]).map((tag) => tag.trim()).filter(Boolean);
  return tags.length === 0 ? null : tags;
}

/** Which project a search covers, or null for every project in the database.
 *
 * Unlike readProject this never throws on a session without a default: a
 * search that names nothing spans the database rather than refusing.
 */
function readSearchProject(
  args: Record<string, unknown> | undefined,
  sessionProject: string | null,
): string | null {
  const named = readOptionalString(args, "project");
  if (named !== null) {
    return named === "*" ? null : named;
  }
  if (readOptionalString(args, "project_type") !== null) {
    return null;
  }
  return sessionProject;
}

/** The scope tag a memory or suggestion call is about, and whether it was
 * named outright.
 *
 * `explicit` separates a caller that named "*" from one that named nothing on
 * a session without a default, exactly as it does for a plan: both are null
 * and they mean opposite things when writing.
 */
function readRecordScope(
  args: Record<string, unknown> | undefined,
  sessionProject: string | null,
): { explicit: boolean; about: string | null } {
  const named = readOptionalString(args, "about");
  if (named !== null) {
    return { explicit: true, about: named === "*" ? null : named };
  }
  return { explicit: false, about: sessionProject };
}

/** The node id a record is stored under: its scope, then its slug. */
function scopedRecordId(about: string | null, recordId: string): string {
  if (recordId.includes("/")) {
    return recordId;
  }
  return `${about ?? GLOBAL_SCOPE}/${recordId}`;
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
//
// Memories and suggestions are counted by what they are about rather than by
// which project row holds them, because they all live under a built-in
// project. Counting them by `g.project` would report zero for every codebase
// and hide exactly the records a drop leaves pointing at a name that is gone.
const DROP_REPORT = `
  SELECT p.root_path, p.indexed_at, p.type,
         (SELECT count(*) FROM graph_nodes AS g
           WHERE g.project = p.name) AS nodes,
         (SELECT count(*) FROM graph_nodes AS g
           WHERE g.type = 'memory'
             AND (g.project = p.name
                  OR g.metadata ->> 'about' = p.name)) AS memories,
         (SELECT count(*) FROM graph_nodes AS g
           WHERE g.type = 'suggestion'
             AND (g.project = p.name
                  OR g.metadata ->> 'about' = p.name)) AS suggestions,
         (SELECT count(*) FROM graph_edges AS e
           WHERE e.project = p.name) AS edges,
         (SELECT count(*) FROM file_hashes AS f
           WHERE f.project = p.name) AS hashes,
         (SELECT count(*) FROM code_embeddings AS c
           WHERE c.project = p.name) AS embeddings,
         (SELECT count(*) FROM graph_nodes AS l
           WHERE l.project = '_plans'
             AND l.metadata ->> 'about' = p.name) AS plans,
         (SELECT count(*) FROM graph_nodes AS g
           WHERE g.project = p.name
             AND g.type <> 'memory'
             AND g.metadata ->> 'summary_source' = 'manual') AS summaries
    FROM projects AS p
   WHERE p.name = $1`;

// count() arrives as a string: node-postgres leaves bigint alone rather than
// losing precision in a number.
type DropReport = {
  root_path: string;
  indexed_at: Date | null;
  type: string;
  nodes: string;
  memories: string;
  suggestions: string;
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
  // Nothing rebuilds a built-in project: there is no tree behind one, so its
  // nodes belong on the other side of the ledger from a codebase's.
  const derived =
    row.type === "memory" || row.type === "suggestions"
      ? []
      : [
          `  rebuilt by one \`make index\`: ${row.nodes} nodes, ` +
            `${row.edges} edges, ${row.hashes} file hashes, ` +
            `${row.embeddings} embeddings`,
        ];
  const lost = [`${row.summaries} manual summaries`];
  // A record about this project lives under the built-in project holding it,
  // so dropping this one does not cascade it away - it is kept, like a plan,
  // and goes on naming a project that is gone.
  const builtin = row.type === "memory" || row.type === "suggestions";
  if (row.memories !== "0" && builtin) {
    lost.push(`${row.memories} memories, which no index run brings back`);
  }
  const kept = [
    `${row.plans} plans, still readable with get_plans project: "${project}"`,
  ];
  if (row.memories !== "0" && !builtin) {
    kept.push(`${row.memories} memories about it, in ${MEMORY_PROJECT}`);
  }
  if (row.suggestions !== "0") {
    kept.push(
      `${row.suggestions} suggestions about it, in ${SUGGESTIONS_PROJECT}`,
    );
  }
  const lines = [
    `${dropped ? "dropped" : "project"} "${project}" (${row.root_path}, ${when})`,
    ...derived,
    `  not rebuilt, gone for good: ${lost.join(", ")}`,
    `  kept, tagged with the name: ${kept.join(", ")}`,
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
          `SELECT p.name, p.type, p.root_path, p.indexed_at, COUNT(n.id) AS nodes
             FROM projects AS p
             LEFT JOIN graph_nodes AS n ON n.project = p.name
            GROUP BY p.name, p.type, p.root_path, p.indexed_at
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
          // and code_embeddings from graph_nodes. Plans live under '_plans'
          // and only name this project in metadata, so they survive the drop,
          // which is what the report says.
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
      // A plan belongs to '_plans' rather than to the graph it is about: it
      // may name a codebase this database has never indexed, and it stays
      // readable after that codebase is dropped, so requiring the project to
      // exist would refuse both.
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

        const client = await dbPool.connect();
        try {
          await client.query("BEGIN");
          // Re-created rather than assumed: dropping the project would
          // otherwise turn every later save into a foreign key error.
          await client.query(
            `INSERT INTO projects (name, root_path, type)
             VALUES ($1, 'plans://agent', 'plans')
             ON CONFLICT (name) DO NOTHING`,
            [PLANS_PROJECT],
          );
          await client.query(
            `INSERT INTO graph_nodes (
               project, id, name, type, content, metadata
             )
             VALUES ($1, $2, $3, $4, $5,
                     JSONB_BUILD_OBJECT(
                       'about', $6::text,
                       'status', $7::text,
                       'summary_source', 'manual',
                       'updated_at', to_char(
                         now() AT TIME ZONE 'UTC',
                         'YYYY-MM-DD"T"HH24:MI:SS"Z"'
                       )
                     ))
             ON CONFLICT (project, id) DO UPDATE SET
               name = EXCLUDED.name,
               type = EXCLUDED.type,
               content = EXCLUDED.content,
               metadata = graph_nodes.metadata || EXCLUDED.metadata`,
            [
              PLANS_PROJECT,
              planId,
              title,
              planType,
              content,
              scope.project,
              status,
            ],
          );
          await client.query("COMMIT");
        } catch (error) {
          await client.query("ROLLBACK").catch(() => undefined);
          throw error;
        } finally {
          client.release();
        }

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
          `SELECT id,
                  metadata ->> 'about' AS project,
                  name AS title,
                  content,
                  metadata ->> 'status' AS status,
                  type,
                  metadata - 'about' - 'status' - 'summary_source'
                    - 'updated_at' AS metadata,
                  created_at,
                  metadata ->> 'updated_at' AS updated_at
             FROM graph_nodes
            WHERE project = $1
              AND ($2::text IS NULL
                   OR metadata ->> 'about' = $2
                   OR metadata ->> 'about' IS NULL)
              AND metadata ->> 'status' = $3
              AND ($4::text IS NULL OR type = $4)
            ORDER BY (metadata ->> 'about' IS NULL),
                     metadata ->> 'updated_at' DESC`,
          [PLANS_PROJECT, scope.project, status, planType],
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
          `DELETE FROM graph_nodes
            WHERE project = $1 AND id = $2
        RETURNING metadata ->> 'about' AS project,
                  name AS title,
                  metadata ->> 'status' AS status`,
          [PLANS_PROJECT, planId],
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

      // The memory tools always work on the built-in project, never on the
      // session's, so they sit above the project lookup like the plan tools.
      if (name === "save_memory") {
        const memoryId = requireString(args, "memory_id");
        const title = requireString(args, "title");
        const text = requireString(args, "text");
        const gist = readOptionalString(args, "summary") ?? title;
        const tags = readTags(args, "tags") ?? [];
        const scope = readRecordScope(args, sessionProject);

        // Storing a memory against no project at all is a decision, so it is
        // made rather than fallen into by omission - as with save_plan.
        if (!scope.explicit && scope.about === null) {
          throw new Error(
            'Argument "about" is required: this session was opened on /mcp ' +
              'without naming a project. Name the project this is about, or "*" ' +
              "for a memory about none in particular.",
          );
        }
        if (memoryId.includes("/")) {
          throw new Error(
            'Argument "memory_id" is a slug, not a path: the scope is taken ' +
              'from "about" and prefixed automatically.',
          );
        }

        const nodeId = scopedRecordId(scope.about, memoryId);
        const client = await dbPool.connect();
        try {
          await client.query("BEGIN");
          // Re-created rather than assumed: dropping the project would
          // otherwise turn every later save into a foreign key error.
          await client.query(
            `INSERT INTO projects (name, root_path, type)
             VALUES ($1, 'memory://agent', 'memory')
             ON CONFLICT (name) DO NOTHING`,
            [MEMORY_PROJECT],
          );
          await client.query(
            `INSERT INTO graph_nodes (
               project, id, name, type, summary, content, metadata
             )
             VALUES ($1, $2, $3, 'memory', $4, $5,
                     JSONB_BUILD_OBJECT(
                       'about', $6::text,
                       'tags', $7::jsonb,
                       'summary_source', 'manual',
                       'updated_at', to_char(
                         now() AT TIME ZONE 'UTC',
                         'YYYY-MM-DD"T"HH24:MI:SS"Z"'
                       )
                     ))
             ON CONFLICT (project, id) DO UPDATE SET
               name = EXCLUDED.name,
               type = 'memory',
               summary = EXCLUDED.summary,
               content = EXCLUDED.content,
               metadata = graph_nodes.metadata || EXCLUDED.metadata`,
            [
              MEMORY_PROJECT,
              nodeId,
              title,
              gist,
              text,
              scope.about,
              JSON.stringify(tags),
            ],
          );
          await client.query("COMMIT");
        } catch (error) {
          await client.query("ROLLBACK");
          throw error;
        } finally {
          client.release();
        }

        const where =
          scope.about === null ? "no project in particular" : scope.about;
        const lines = [`Memory ${nodeId} saved (about ${where}).`];
        // The scope is free text with nothing to check it, exactly as a plan's
        // project tag is, so a typo would file it where nothing looks.
        if (scope.about !== null) {
          const known = await dbPool.query(
            `SELECT 1 FROM projects WHERE name = $1`,
            [scope.about],
          );
          if (known.rowCount === 0) {
            lines.push(
              `No project named "${scope.about}" in this database. The memory ` +
                "was stored anyway and will be read once one is indexed under " +
                "that name.",
            );
          }
        }

        return { content: [{ type: "text", text: lines.join("\n") }] };
      }

      if (name === "get_memory") {
        const scope = readRecordScope(args, sessionProject);
        const wanted = readOptionalString(args, "memory_id");
        const tags = readTags(args, "tags");
        const query = readOptionalString(args, "query");

        // A bare slug is looked for in the named scope and globally, since
        // those are the two places a read of that scope can see.
        const ids =
          wanted === null
            ? null
            : wanted.includes("/")
              ? [wanted]
              : [
                  scopedRecordId(scope.about, wanted),
                  scopedRecordId(null, wanted),
                ];

        const res = await dbPool.query(
          `SELECT id, name AS title, summary, content,
                  metadata ->> 'about' AS about,
                  metadata -> 'tags' AS tags,
                  metadata ->> 'updated_at' AS updated_at,
                  created_at
             FROM graph_nodes
            WHERE project = $1
              AND type = 'memory'
              AND ($2::text[] IS NULL OR id = ANY ($2))
              AND ($3::text IS NULL
                   OR metadata ->> 'about' = $3
                   OR metadata ->> 'about' IS NULL)
              AND ($4::jsonb IS NULL OR metadata -> 'tags' @> $4)
              AND ($5::text IS NULL
                   OR name ILIKE $5 OR summary ILIKE $5 OR content ILIKE $5)
            ORDER BY (metadata ->> 'about' IS NULL), id
            LIMIT $6`,
          [
            MEMORY_PROJECT,
            ids,
            scope.about,
            tags === null ? null : JSON.stringify(tags),
            query === null ? null : `%${query}%`,
            readLimit(args),
          ],
        );

        return {
          content: [{ type: "text", text: JSON.stringify(res.rows, null, 2) }],
        };
      }

      if (name === "drop_memory") {
        const memoryId = requireString(args, "memory_id");
        const scope = readRecordScope(args, sessionProject);
        const nodeId = scopedRecordId(scope.about, memoryId);
        const res = await dbPool.query<{ name: string }>(
          `DELETE FROM graph_nodes
            WHERE project = $1 AND id = $2 AND type = 'memory'
        RETURNING name`,
          [MEMORY_PROJECT, nodeId],
        );

        // A typo and a delete have to read differently, or the caller cannot
        // tell which of the two just happened.
        if (res.rowCount === 0) {
          return {
            content: [
              {
                type: "text",
                text: `No memory "${nodeId}". Nothing was deleted.`,
              },
            ],
          };
        }

        return {
          content: [
            {
              type: "text",
              text: `Dropped memory ${nodeId} ("${res.rows[0].name}").`,
            },
          ],
        };
      }

      // The suggestion tools sit here for the same reason the memory ones do:
      // they always work on the built-in project, never on the session's.
      if (name === "save_suggestion") {
        const suggestionId = requireString(args, "suggestion_id");
        const title = requireString(args, "title");
        const detail = requireString(args, "detail");
        const gist = readOptionalString(args, "summary") ?? title;
        const kind = readOptionalString(args, "kind");
        const lever = readOptionalString(args, "lever");
        const status = readOptionalString(args, "status");
        const bump = args?.bump === false ? 0 : 1;
        const scope = readRecordScope(args, sessionProject);

        if (!scope.explicit && scope.about === null) {
          throw new Error(
            'Argument "about" is required: this session was opened on /mcp ' +
              'without naming a project. Name the project this gap is in, or "*" ' +
              "for one that belongs to none in particular.",
          );
        }
        if (suggestionId.includes("/")) {
          throw new Error(
            'Argument "suggestion_id" is a slug, not a path: the scope is ' +
              'taken from "about" and prefixed automatically.',
          );
        }

        const nodeId = scopedRecordId(scope.about, suggestionId);
        let saved: { hits: number; status: string; created: boolean };
        const client = await dbPool.connect();
        try {
          await client.query("BEGIN");
          // Re-created rather than assumed, as save_memory does: dropping the
          // project would otherwise turn every later save into a foreign key
          // error.
          await client.query(
            `INSERT INTO projects (name, root_path, type)
             VALUES ($1, 'suggestions://agent', 'suggestions')
             ON CONFLICT (name) DO NOTHING`,
            [SUGGESTIONS_PROJECT],
          );
          // The tail object is what makes a repeat report accumulate instead
          // of overwrite, so it is concatenated last and every sticky field
          // falls back to what is already stored. Reopening on a bump is
          // deliberate: a gap hit again is not a resolved one.
          const res = await client.query<{
            hits: number;
            status: string;
            created: boolean;
          }>(
            `INSERT INTO graph_nodes (
               project, id, name, type, summary, content, metadata
             )
             VALUES ($1, $2, $3, 'suggestion', $4, $5,
                     JSONB_BUILD_OBJECT(
                       'about', $6::text,
                       'kind', $7::text,
                       'lever', $8::text,
                       'status', COALESCE($9::text, 'open'),
                       'hits', 1,
                       'first_seen', to_char(
                         now() AT TIME ZONE 'UTC',
                         'YYYY-MM-DD"T"HH24:MI:SS"Z"'
                       ),
                       'last_seen', to_char(
                         now() AT TIME ZONE 'UTC',
                         'YYYY-MM-DD"T"HH24:MI:SS"Z"'
                       )
                     ))
             ON CONFLICT (project, id) DO UPDATE SET
               name = EXCLUDED.name,
               type = 'suggestion',
               summary = EXCLUDED.summary,
               content = EXCLUDED.content,
               metadata = graph_nodes.metadata
                 || EXCLUDED.metadata
                 || JSONB_BUILD_OBJECT(
                      'kind',
                      COALESCE($7::text, graph_nodes.metadata ->> 'kind'),
                      'lever',
                      COALESCE($8::text, graph_nodes.metadata ->> 'lever'),
                      'status',
                      COALESCE($9::text,
                               CASE WHEN $10::int > 0 THEN 'open' END,
                               graph_nodes.metadata ->> 'status',
                               'open'),
                      'first_seen',
                      COALESCE(graph_nodes.metadata ->> 'first_seen',
                               EXCLUDED.metadata ->> 'first_seen'),
                      'hits',
                      COALESCE((graph_nodes.metadata ->> 'hits')::int, 0)
                        + $10::int
                    )
             RETURNING (metadata ->> 'hits')::int AS hits,
                       metadata ->> 'status' AS status,
                       (xmax = 0) AS created`,
            [
              SUGGESTIONS_PROJECT,
              nodeId,
              title,
              gist,
              detail,
              scope.about,
              kind,
              lever,
              status,
              bump,
            ],
          );
          saved = res.rows[0];
          await client.query("COMMIT");
        } catch (error) {
          await client.query("ROLLBACK");
          throw error;
        } finally {
          client.release();
        }

        const where =
          scope.about === null ? "no project in particular" : scope.about;
        const verb = saved.created ? "recorded" : "updated";
        const lines = [
          `Suggestion ${nodeId} ${verb} (about ${where}, ` +
            `${saved.status}, hits ${saved.hits}).`,
        ];
        // The scope is free text with nothing to check it, exactly as a
        // memory's is, so a typo would file it where nothing looks.
        if (scope.about !== null) {
          const known = await dbPool.query(
            `SELECT 1 FROM projects WHERE name = $1`,
            [scope.about],
          );
          if (known.rowCount === 0) {
            lines.push(
              `No project named "${scope.about}" in this database. The ` +
                "suggestion was stored anyway and will be read once one is " +
                "indexed under that name.",
            );
          }
        }

        return { content: [{ type: "text", text: lines.join("\n") }] };
      }

      if (name === "get_suggestions") {
        const scope = readRecordScope(args, sessionProject);
        const wanted = readOptionalString(args, "suggestion_id");
        const named = readOptionalString(args, "status");
        const status = named === "*" ? null : (named ?? "open");
        const kind = readOptionalString(args, "kind");
        const query = readOptionalString(args, "query");

        const ids =
          wanted === null
            ? null
            : wanted.includes("/")
              ? [wanted]
              : [
                  scopedRecordId(scope.about, wanted),
                  scopedRecordId(null, wanted),
                ];

        const res = await dbPool.query(
          `SELECT id, name AS title, summary, content AS detail,
                  metadata ->> 'about' AS about,
                  metadata ->> 'kind' AS kind,
                  metadata ->> 'lever' AS lever,
                  metadata ->> 'status' AS status,
                  COALESCE((metadata ->> 'hits')::int, 0) AS hits,
                  metadata ->> 'first_seen' AS first_seen,
                  metadata ->> 'last_seen' AS last_seen
             FROM graph_nodes
            WHERE project = $1
              AND type = 'suggestion'
              AND ($2::text[] IS NULL OR id = ANY ($2))
              AND ($3::text IS NULL
                   OR metadata ->> 'about' = $3
                   OR metadata ->> 'about' IS NULL)
              AND ($4::text IS NULL OR metadata ->> 'status' = $4)
              AND ($5::text IS NULL OR metadata ->> 'kind' = $5)
              AND ($6::text IS NULL
                   OR name ILIKE $6 OR summary ILIKE $6 OR content ILIKE $6)
            ORDER BY COALESCE((metadata ->> 'hits')::int, 0) DESC,
                     metadata ->> 'last_seen' DESC NULLS LAST
            LIMIT $7`,
          [
            SUGGESTIONS_PROJECT,
            ids,
            scope.about,
            status,
            kind,
            query === null ? null : `%${query}%`,
            readLimit(args),
          ],
        );

        return {
          content: [{ type: "text", text: JSON.stringify(res.rows, null, 2) }],
        };
      }

      if (name === "drop_suggestion") {
        const suggestionId = requireString(args, "suggestion_id");
        const scope = readRecordScope(args, sessionProject);
        const nodeId = scopedRecordId(scope.about, suggestionId);
        const res = await dbPool.query<{ name: string }>(
          `DELETE FROM graph_nodes
            WHERE project = $1 AND id = $2 AND type = 'suggestion'
        RETURNING name`,
          [SUGGESTIONS_PROJECT, nodeId],
        );

        if (res.rowCount === 0) {
          return {
            content: [
              {
                type: "text",
                text: `No suggestion "${nodeId}". Nothing was deleted.`,
              },
            ],
          };
        }

        return {
          content: [
            {
              type: "text",
              text: `Dropped suggestion ${nodeId} ("${res.rows[0].name}").`,
            },
          ],
        };
      }

      // Above the project lookup below, because a search is the one read that
      // can span the database: "*" and a project type name no single project.
      if (name === "search_code_nodes") {
        const pattern = `%${requireString(args, "query")}%`;
        const limit = readLimit(args);
        const named = readSearchProject(args, sessionProject);
        const kind = readOptionalString(args, "project_type");

        if (named !== null && kind !== null) {
          throw new Error(
            'Arguments "project" and "project_type" cannot be combined: ' +
              "project_type narrows a search across projects, so pass " +
              'project: "*" or leave it out.',
          );
        }
        if (named !== null) {
          await requireProject(named);
        }

        // Round robin, not concatenation: taking each project's first hit,
        // then each project's second, is what keeps a search across the
        // database from spending its whole limit on whichever project sorts
        // first - while still returning as many rows as were asked for.
        const res = await dbPool.query(
          `WITH scope AS (
             SELECT name, type FROM projects
              WHERE ($1::text IS NULL OR name = $1)
                AND ($2::text IS NULL OR type = $2)
           ),
           hits AS (
             SELECT n.project, s.type AS project_type, n.id, n.name, n.type,
                    n.file_path, n.summary,
                    ROW_NUMBER() OVER (
                      PARTITION BY n.project ORDER BY n.id
                    ) AS rn
               FROM graph_nodes AS n
               JOIN scope AS s ON s.name = n.project
              WHERE n.name ILIKE $3 OR n.id ILIKE $3
           )
           SELECT project, project_type, id, name, type, file_path, summary
             FROM hits
            ORDER BY rn, project, id
            LIMIT $4`,
          [named, kind, pattern, limit],
        );

        // A single project keeps the shape it always had; the project columns
        // are noise when every row carries the same two values. Across
        // projects the rows are regrouped, since the round robin above orders
        // them by rank and a reader wants them by project.
        const rows =
          named === null
            ? [...res.rows].sort((a, b) =>
                a.project === b.project
                  ? String(a.id).localeCompare(String(b.id))
                  : String(a.project).localeCompare(String(b.project)),
              )
            : res.rows.map(
                ({ project: _p, project_type: _t, ...rest }) => rest,
              );

        if (rows.length === 0 && kind !== null) {
          const types = await dbPool.query(
            `SELECT DISTINCT type FROM projects ORDER BY type`,
          );
          const known = types.rows.map((row) => row.type as string).join(", ");
          return {
            content: [
              {
                type: "text",
                text:
                  `No node matching the query in any project of type ` +
                  `"${kind}". Types in this database: ${known || "none"}.`,
              },
            ],
          };
        }

        return {
          content: [{ type: "text", text: JSON.stringify(rows, null, 2) }],
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
