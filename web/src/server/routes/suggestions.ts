import { Router } from "express";

import {
  notFound,
  readBodyString,
  readLimit,
  readOffset,
  readQuery,
  requireQuery,
  route,
} from "../args.js";
import { count, dbPool } from "../db.js";
import * as sql from "../queries.js";

// The about tag is free text with no foreign key, exactly as a plan's project
// is, so these two are selections rather than names: every scope, and the
// suggestions about none.
const ALL_SCOPES = "*";
const GLOBAL_ONLY = "_global";

const MIN_QUERY = 2;

function searchPattern(value: string | null): string | null {
  if (value === null || value.trim().length < MIN_QUERY) {
    return null;
  }
  return `%${value.trim()}%`;
}

export const suggestionsRouter = Router();

// There is no POST here on purpose: a suggestion is written by an agent that
// hit the gap, and the count it carries is only meaningful if nothing else
// creates one. The dashboard triages what is already recorded.
suggestionsRouter.get(
  "/suggestions",
  route(async (req, res) => {
    const scope = readQuery(req, "about");
    const limit = readLimit(req);
    const offset = readOffset(req);
    const rows = await dbPool.query<{
      total: string;
      hits: number;
      detail_length: string;
    }>(sql.SUGGESTIONS, [
      scope === null || scope === ALL_SCOPES || scope === GLOBAL_ONLY
        ? null
        : scope,
      scope === GLOBAL_ONLY,
      readQuery(req, "status"),
      readQuery(req, "kind"),
      searchPattern(readQuery(req, "q")),
      limit,
      offset,
    ]);

    res.json({
      items: rows.rows.map(({ total: _total, ...suggestion }) => ({
        ...suggestion,
        detail_length: count(suggestion.detail_length),
      })),
      total: rows.rowCount === 0 ? 0 : count(rows.rows[0].total),
      limit,
      offset,
    });
  }),
);

suggestionsRouter.get(
  "/suggestions/facets",
  route(async (_req, res) => {
    const rows = await dbPool.query<{
      abouts: string[] | null;
      statuses: string[] | null;
      kinds: string[] | null;
      global_suggestions: string;
    }>(sql.SUGGESTION_FACETS);
    const row = rows.rows[0];
    res.json({
      abouts: (row.abouts ?? []).sort(),
      statuses: (row.statuses ?? []).sort(),
      kinds: (row.kinds ?? []).sort(),
      global_suggestions: count(row.global_suggestions),
    });
  }),
);

suggestionsRouter.get(
  "/suggestion",
  route(async (req, res) => {
    const id = requireQuery(req, "id");
    const rows = await dbPool.query(sql.SUGGESTION, [id]);
    if (rows.rowCount === 0) {
      throw notFound(`No suggestion "${id}"`);
    }
    res.json(rows.rows[0]);
  }),
);

suggestionsRouter.patch(
  "/suggestion",
  route(async (req, res) => {
    const id = requireQuery(req, "id");
    const body = req.body as unknown;
    const rows = await dbPool.query(sql.PATCH_SUGGESTION, [
      id,
      readBodyString(body, "title") ?? null,
      readBodyString(body, "summary") ?? null,
      readBodyString(body, "detail") ?? null,
      readBodyString(body, "status") ?? null,
      readBodyString(body, "kind") ?? null,
      readBodyString(body, "lever") ?? null,
    ]);
    if (rows.rowCount === 0) {
      throw notFound(`No suggestion "${id}"`);
    }
    res.json(rows.rows[0]);
  }),
);

suggestionsRouter.delete(
  "/suggestion",
  route(async (req, res) => {
    const id = requireQuery(req, "id");
    const rows = await dbPool.query(sql.DROP_SUGGESTION, [id]);
    if (rows.rowCount === 0) {
      throw notFound(`No suggestion "${id}". Nothing was deleted.`);
    }
    res.json(rows.rows[0]);
  }),
);
