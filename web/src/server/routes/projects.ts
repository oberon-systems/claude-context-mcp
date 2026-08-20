import { Router } from "express";

import { HttpError, notFound, route } from "../args.js";
import { count, dbPool } from "../db.js";
import * as sql from "../queries.js";

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
};

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
          "first: the graph goes, and only `make index` brings it back.",
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
