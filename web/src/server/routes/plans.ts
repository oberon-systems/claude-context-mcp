import { Router } from "express";

import {
  notFound,
  readBodyString,
  readLimit,
  readOffset,
  readQuery,
  requireBodyString,
  requireQuery,
  route,
} from "../args.js";
import { count, dbPool } from "../db.js";
import * as sql from "../queries.js";

// The project column is a free-text tag with no foreign key, so these two are
// selections rather than names: every project, and the plans tagged with none.
const ALL_PROJECTS = "*";
const GLOBAL_ONLY = "_global";

const MIN_QUERY = 2;

function searchPattern(value: string | null): string | null {
  if (value === null || value.trim().length < MIN_QUERY) {
    return null;
  }
  return `%${value.trim()}%`;
}

/** A plan's project as it is stored: a name, or null for a global plan. */
function tag(value: string | undefined): string | null {
  if (value === undefined || value.trim() === "" || value === ALL_PROJECTS) {
    return null;
  }
  return value;
}

export const plansRouter = Router();

plansRouter.get(
  "/plans",
  route(async (req, res) => {
    const scope = readQuery(req, "project");
    const limit = readLimit(req);
    const offset = readOffset(req);
    const rows = await dbPool.query<{
      total: string;
      content_length: string;
    }>(sql.PLANS, [
      scope === null || scope === ALL_PROJECTS || scope === GLOBAL_ONLY
        ? null
        : scope,
      scope === GLOBAL_ONLY,
      readQuery(req, "status"),
      readQuery(req, "type"),
      searchPattern(readQuery(req, "q")),
      limit,
      offset,
    ]);

    res.json({
      items: rows.rows.map(({ total: _total, ...plan }) => ({
        ...plan,
        content_length: count(plan.content_length),
      })),
      total: rows.rowCount === 0 ? 0 : count(rows.rows[0].total),
      limit,
      offset,
    });
  }),
);

plansRouter.get(
  "/plans/facets",
  route(async (_req, res) => {
    const rows = await dbPool.query<{
      projects: string[] | null;
      statuses: string[] | null;
      types: string[] | null;
      global_plans: string;
    }>(sql.PLAN_FACETS);
    const row = rows.rows[0];
    res.json({
      projects: (row.projects ?? []).sort(),
      statuses: (row.statuses ?? []).sort(),
      types: (row.types ?? []).sort(),
      global_plans: count(row.global_plans),
    });
  }),
);

plansRouter.get(
  "/plan",
  route(async (req, res) => {
    const id = requireQuery(req, "id");
    const rows = await dbPool.query(sql.PLAN, [id]);
    if (rows.rowCount === 0) {
      throw notFound(`No plan "${id}"`);
    }
    res.json(rows.rows[0]);
  }),
);

plansRouter.post(
  "/plans",
  route(async (req, res) => {
    const body = req.body as unknown;
    const id = requireBodyString(body, "id");
    const rows = await dbPool.query<{ created: boolean }>(sql.SAVE_PLAN, [
      id,
      tag(readBodyString(body, "project")),
      requireBodyString(body, "title"),
      requireBodyString(body, "content"),
      readBodyString(body, "status") ?? "active",
      readBodyString(body, "type") ?? "plan",
    ]);
    res.status(rows.rows[0].created ? 201 : 200).json({
      id,
      created: rows.rows[0].created,
    });
  }),
);

plansRouter.patch(
  "/plan",
  route(async (req, res) => {
    const id = requireQuery(req, "id");
    const body = req.body as unknown;
    // The project is the one field whose null is a value rather than an
    // omission, so whether it was named travels alongside it.
    const project = readBodyString(body, "project");
    const rows = await dbPool.query(sql.PATCH_PLAN, [
      id,
      readBodyString(body, "title") ?? null,
      readBodyString(body, "content") ?? null,
      readBodyString(body, "status") ?? null,
      readBodyString(body, "type") ?? null,
      project !== undefined,
      tag(project),
    ]);
    if (rows.rowCount === 0) {
      throw notFound(`No plan "${id}"`);
    }
    res.json(rows.rows[0]);
  }),
);

plansRouter.delete(
  "/plan",
  route(async (req, res) => {
    const id = requireQuery(req, "id");
    const rows = await dbPool.query(sql.DROP_PLAN, [id]);
    if (rows.rowCount === 0) {
      throw notFound(`No plan "${id}". Nothing was deleted.`);
    }
    res.json(rows.rows[0]);
  }),
);
