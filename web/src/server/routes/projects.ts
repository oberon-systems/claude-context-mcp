import { Router } from "express";

import {
  HttpError,
  badRequest,
  notFound,
  readBodyString,
  route,
} from "../args.js";
import { UpstreamError, upstream } from "../content.js";
import { count, dbPool } from "../db.js";
import { INDEXING_KEY, readIndexing } from "../indexing.js";
import * as sql from "../queries.js";

type ProjectSource = {
  alias: string;
  root_path: string;
  // Where the last index run read this directory's selection from: "file",
  // "directory", "project", "global" or "default". Null until first indexed.
  keep_source: string | null;
  ignore_source: string | null;
};

// The vocabulary of ctxgraph.config.KNOWN_PROJECT_TYPES. The column is
// unconstrained on purpose (migration 0006), so this is where a dashboard
// write is held to it.
const PROJECT_TYPES = ["codebase", "docs", "config"] as const;

// The types that hold records written by an agent rather than an indexed
// tree. Refused in both directions: a project turned into one of these would
// have every record it holds deleted by the next index run, and one turned
// out of it would be indexed over.
const BUILTIN_TYPES = new Set(["memory", "plans", "suggestions", "settings"]);

type ProjectRow = {
  name: string;
  type: string;
  root_path: string;
  indexed_at: Date | null;
  stale_seconds: string | null;
  nodes: string;
  edges: string;
  files: string;
  plans: string;
  sources: ProjectSource[];
};

type SettingsRow = {
  alias: string;
  root_path: string;
  keep_source: string | null;
  ignore_source: string | null;
  ctxkeep: string | null;
  ctxignore: string | null;
  updated_at: Date | null;
};

type LevelRow = {
  ctxkeep: string | null;
  ctxignore: string | null;
  updated_at: Date | null;
};

// The built-in project the global defaults hang off, as migration 0012
// creates it and ctxgraph.config.SETTINGS_PROJECT names it.
const SETTINGS_PROJECT = "_settings";

// The project level is the empty alias, which a URL path cannot carry. `-` is
// what the mount listing already writes for it, so it is what the dashboard
// spells it too.
function alias(raw: string): string {
  return raw === "-" ? "" : raw;
}

type DropReportRow = {
  root_path: string;
  indexed_at: Date | null;
  nodes: string;
  edges: string;
  hashes: string;
  embeddings: string;
  plans: string;
  suggestions: string;
  summaries: string;
};

function project(row: ProjectRow) {
  return {
    name: row.name,
    type: row.type,
    root_path: row.root_path,
    sources: row.sources,
    indexed_at: row.indexed_at,
    stale_seconds:
      row.stale_seconds === null ? null : Number(row.stale_seconds),
    nodes: count(row.nodes),
    edges: count(row.edges),
    files: count(row.files),
    plans: count(row.plans),
  };
}

function dropReport(name: string, row: DropReportRow, dropped: boolean) {
  return {
    name,
    root_path: row.root_path,
    indexed_at: row.indexed_at,
    nodes: count(row.nodes),
    edges: count(row.edges),
    hashes: count(row.hashes),
    embeddings: count(row.embeddings),
    plans: count(row.plans),
    suggestions: count(row.suggestions),
    summaries: count(row.summaries),
    dropped,
  };
}

export async function requireProject(name: string): Promise<string> {
  const res = await dbPool.query(sql.PROJECT_EXISTS, [name]);
  if (res.rowCount === 0) {
    throw notFound(`No indexed project named "${name}"`);
  }
  return name;
}

export const projectsRouter = Router();

type IndexJob = {
  id: number;
  project: string;
  status: string;
  files: number | null;
  error: string | null;
};

/** Pass an API failure on with its own status rather than as a 500. */
function passOn(error: unknown): never {
  if (error instanceof UpstreamError) {
    throw new HttpError(error.status, error.message);
  }
  throw error;
}

projectsRouter.post(
  "/projects/:name/index",
  route(async (req, res) => {
    const name = await requireProject(req.params.name);
    const body = req.body as { fresh?: unknown } | undefined;
    const job = await upstream<IndexJob>(
      "POST",
      "/index",
      {},
      { project: name, fresh: body?.fresh === true },
    ).catch(passOn);
    res.status(202).json(job);
  }),
);

projectsRouter.get(
  "/projects/:name/index",
  route(async (req, res) => {
    const name = await requireProject(req.params.name);
    const body = await upstream<{ jobs: IndexJob[] }>("GET", "/index", {
      project: name,
      limit: "1",
    }).catch(passOn);
    res.json(body.jobs[0] ?? null);
  }),
);

// What a project reads. Adding or dropping a directory is stored here and
// mounted nowhere: the compose override is a file on the host, and both
// services read the mounts they were started with, so the API says as much in
// its reply and `make mounts` is what finishes the job.
projectsRouter.post(
  "/projects/:name/sources",
  route(async (req, res) => {
    const name = await requireProject(req.params.name);
    const body = req.body as { root_path?: unknown; alias?: unknown };
    const answer = await upstream<unknown>(
      "POST",
      `/projects/${encodeURIComponent(name)}/sources`,
      {},
      {
        root_path: String(body?.root_path ?? ""),
        alias: String(body?.alias ?? ""),
      },
    ).catch(passOn);
    res.status(201).json(answer);
  }),
);

projectsRouter.delete(
  "/projects/:name/sources/:alias",
  route(async (req, res) => {
    const name = await requireProject(req.params.name);
    const answer = await upstream<unknown>(
      "DELETE",
      `/projects/${encodeURIComponent(name)}/sources/` +
        encodeURIComponent(req.params.alias),
    ).catch(passOn);
    res.json(answer);
  }),
);

// Registering a project is the one route that must not require one first.
// Nothing is mounted by it either: the row comes first and `make mounts` on
// the host finishes the job, which is what the API's reply says.
projectsRouter.post(
  "/projects",
  route(async (req, res) => {
    const body = req.body as Record<string, unknown> | undefined;
    const answer = await upstream<unknown>(
      "POST",
      "/projects",
      {},
      {
        name: readBodyString(body, "name") ?? "",
        root_path: readBodyString(body, "root_path") ?? "",
        alias: readBodyString(body, "alias") ?? "",
        project_type: readBodyString(body, "type") ?? "",
      },
    ).catch(passOn);
    res.status(201).json(answer);
  }),
);

projectsRouter.patch(
  "/projects/:name",
  route(async (req, res) => {
    const name = await requireProject(req.params.name);
    const type = readBodyString(req.body, "type");
    if (type === undefined) {
      throw badRequest('Send {"type": "codebase" | "docs" | "config"}');
    }
    if (BUILTIN_TYPES.has(type)) {
      throw new HttpError(
        409,
        `"${type}" holds records written by an agent rather than an indexed ` +
          "tree, and indexing into it would delete every one of them. Only " +
          `${PROJECT_TYPES.join(", ")} can be set here.`,
      );
    }
    if (!(PROJECT_TYPES as readonly string[]).includes(type)) {
      throw badRequest(
        `"${type}" is not a project type. Expected one of ` +
          `${PROJECT_TYPES.join(", ")}.`,
      );
    }
    const current = await dbPool.query<{ type: string }>(sql.PROJECT_TYPE, [
      name,
    ]);
    if (BUILTIN_TYPES.has(current.rows[0].type)) {
      throw new HttpError(
        409,
        `${name} holds agent ${current.rows[0].type} rather than an indexed ` +
          "tree. Its type is what keeps an index run out of it.",
      );
    }
    const updated = await dbPool.query(sql.PATCH_PROJECT_TYPE, [name, type]);
    res.json(updated.rows[0]);
  }),
);

projectsRouter.get(
  "/projects",
  route(async (_req, res) => {
    const rows = await dbPool.query<ProjectRow>(sql.PROJECTS);
    res.json({ items: rows.rows.map(project) });
  }),
);

projectsRouter.get(
  "/projects/:name",
  route(async (req, res) => {
    const name = await requireProject(req.params.name);
    const [row, types, relations, extras] = await Promise.all([
      dbPool.query<ProjectRow>(sql.PROJECT, [name]),
      dbPool.query<{ type: string; count: string }>(sql.PROJECT_NODE_TYPES, [
        name,
      ]),
      dbPool.query<{ relation_type: string; count: string }>(
        sql.PROJECT_RELATIONS,
        [name],
      ),
      dbPool.query<Record<string, string>>(sql.PROJECT_EXTRAS, [name]),
    ]);

    res.json({
      ...project(row.rows[0]),
      types: types.rows.map((r) => ({ type: r.type, count: count(r.count) })),
      relations: relations.rows.map((r) => ({
        relation_type: r.relation_type,
        count: count(r.count),
      })),
      manual_summaries: count(extras.rows[0].manual_summaries),
      summarised: count(extras.rows[0].summarised),
      hashed_files: count(extras.rows[0].hashed_files),
      embeddings: count(extras.rows[0].embeddings),
    });
  }),
);

// What a project indexes, per directory, and where that answer comes from.
// The rows are read here rather than through the API: the documents are in
// this database, and only the origin needs the mount the API holds.
projectsRouter.get(
  "/projects/:name/settings",
  route(async (req, res) => {
    const name = await requireProject(req.params.name);
    const [sources, project, global] = await Promise.all([
      dbPool.query<SettingsRow>(sql.PROJECT_SETTINGS, [name]),
      dbPool.query<LevelRow>(sql.PROJECT_LEVEL_SETTINGS, [name, ""]),
      dbPool.query<LevelRow>(sql.PROJECT_LEVEL_SETTINGS, [
        SETTINGS_PROJECT,
        "",
      ]),
    ]);
    res.json({
      sources: sources.rows,
      project: project.rows[0] ?? null,
      global: global.rows[0] ?? null,
    });
  }),
);

projectsRouter.get(
  "/projects/:name/file-types",
  route(async (req, res) => {
    const name = await requireProject(req.params.name);
    const rows = await dbPool.query<{ extension: string; count: string }>(
      sql.PROJECT_FILE_TYPES,
      [name],
    );
    res.json({
      items: rows.rows.map((row) => ({
        extension: row.extension,
        count: count(row.count),
      })),
    });
  }),
);

projectsRouter.put(
  "/projects/:name/settings/:alias",
  route(async (req, res) => {
    const name = await requireProject(req.params.name);
    const body = req.body as Record<string, unknown> | undefined;
    // An empty document is stored as NULL rather than as an empty string:
    // that is how a level stops speaking for one half and lets the level
    // above answer, and the two must not be different states.
    const keep = readBodyString(body, "ctxkeep")?.trim();
    const ignore = readBodyString(body, "ctxignore")?.trim();
    const saved = await dbPool.query(sql.SAVE_SETTINGS, [
      name,
      alias(req.params.alias),
      keep === undefined || keep === "" ? null : `${keep}\n`,
      ignore === undefined || ignore === "" ? null : `${ignore}\n`,
    ]);
    res.json(saved.rows[0]);
  }),
);

// When a project indexes itself. The levels are the selection's own, and a
// field left out inherits from the one above it.
projectsRouter.put(
  "/projects/:name/indexing/:alias",
  route(async (req, res) => {
    const name = await requireProject(req.params.name);
    const value = readIndexing(req.body);
    const level = alias(req.params.alias);
    const saved =
      value === null
        ? await dbPool.query(sql.CLEAR_INDEXING, [name, level, INDEXING_KEY])
        : await dbPool.query(sql.SAVE_INDEXING, [
            name,
            level,
            JSON.stringify({ [INDEXING_KEY]: value }),
          ]);
    res.json(saved.rows[0] ?? { project: name, alias: level });
  }),
);

// What the schedule of a project comes to once its directories are folded
// into the single run they share. Asked of the API rather than worked out
// here: the fold is one rule, and a second implementation of it in another
// language would eventually disagree with the one that acts on it.
projectsRouter.get(
  "/projects/:name/schedule",
  route(async (req, res) => {
    const name = await requireProject(req.params.name);
    const schedule = await upstream<unknown>(
      "GET",
      `/projects/${encodeURIComponent(name)}/schedule`,
    ).catch(passOn);
    res.json(schedule);
  }),
);

projectsRouter.delete(
  "/projects/:name/settings/:alias",
  route(async (req, res) => {
    const name = await requireProject(req.params.name);
    const dropped = await dbPool.query(sql.CLEAR_SETTINGS, [
      name,
      alias(req.params.alias),
    ]);
    res.json(dropped.rows[0] ?? { project: name, alias: req.params.alias });
  }),
);

// Propose a selection for one directory from the file types it actually
// holds. The scan needs the tree, so it runs where the mounts are.
projectsRouter.post(
  "/projects/:name/scan",
  route(async (req, res) => {
    const name = await requireProject(req.params.name);
    const body = req.body as { alias?: unknown } | undefined;
    const answer = await upstream<unknown>(
      "POST",
      `/projects/${encodeURIComponent(name)}/scan`,
      {},
      { alias: alias(String(body?.alias ?? "")) },
    ).catch(passOn);
    res.json(answer);
  }),
);

projectsRouter.get(
  "/projects/:name/drop-report",
  route(async (req, res) => {
    const name = await requireProject(req.params.name);
    const report = await dbPool.query<DropReportRow>(sql.DROP_REPORT, [name]);
    res.json(dropReport(name, report.rows[0], false));
  }),
);

projectsRouter.delete(
  "/projects/:name",
  route(async (req, res) => {
    const name = await requireProject(req.params.name);
    const body = req.body as Record<string, unknown> | undefined;
    if (body?.confirm !== true) {
      throw new HttpError(
        409,
        'Send {"confirm": true} to drop the project. Ask for the drop report ' +
          "first: the graph goes, and only indexing it again brings it back.",
      );
    }

    // Counted and deleted on one connection in one transaction, so the receipt
    // cannot describe rows that were never there.
    const client = await dbPool.connect();
    try {
      await client.query("BEGIN");
      const report = await client.query<DropReportRow>(sql.DROP_REPORT, [name]);
      await client.query(sql.DROP_PROJECT, [name]);
      await client.query("COMMIT");
      res.json(dropReport(name, report.rows[0], true));
    } catch (error) {
      await client.query("ROLLBACK");
      throw error;
    } finally {
      client.release();
    }
  }),
);
