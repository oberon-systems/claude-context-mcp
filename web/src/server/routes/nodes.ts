import { Router } from "express";

import {
  badRequest,
  notFound,
  readFlag,
  readLimit,
  readOffset,
  readQuery,
  requireQuery,
  route,
} from "../args.js";
import { count, dbPool } from "../db.js";
import * as sql from "../queries.js";
import { requireProject } from "./projects.js";

// Node content is whole file bodies. A page of them would be measured in
// megabytes, so it is sent only when asked for and only this far.
const MAX_CONTENT = 100_000;

// A one-character search matches most of a large graph and costs a full scan
// to say so.
const MIN_QUERY = 2;

type Paged = { total: string };

function pattern(value: string | null): string | null {
  if (value === null || value.trim().length < MIN_QUERY) {
    return null;
  }
  return `%${value.trim()}%`;
}

function paged<T extends Paged>(
  rows: T[],
  limit: number,
  offset: number,
): { items: Omit<T, "total">[]; total: number; limit: number; offset: number } {
  const items = rows.map((row) => {
    const { total: _total, ...rest } = row;
    return rest;
  });
  return {
    items,
    total: rows.length === 0 ? 0 : count(rows[0].total),
    limit,
    offset,
  };
}

export const nodesRouter = Router();

nodesRouter.get(
  "/projects/:name/nodes",
  route(async (req, res) => {
    const name = await requireProject(req.params.name);
    const limit = readLimit(req);
    const offset = readOffset(req);
    const rows = await dbPool.query<Paged>(sql.NODES, [
      name,
      pattern(readQuery(req, "q")),
      readQuery(req, "type"),
      readQuery(req, "file"),
      limit,
      offset,
    ]);
    res.json(paged(rows.rows, limit, offset));
  }),
);

nodesRouter.get(
  "/projects/:name/node",
  route(async (req, res) => {
    const name = await requireProject(req.params.name);
    const id = requireQuery(req, "id");
    const rows = await dbPool.query<{ content_length: string | null }>(
      sql.NODE,
      [name, id],
    );
    if (rows.rowCount === 0) {
      throw notFound(`No node "${id}" in project "${name}"`);
    }

    const row = rows.rows[0];
    const length = count(row.content_length);
    const detail = {
      ...row,
      content_length: length,
      content: null as string | null,
      content_truncated: false,
    };

    if (readFlag(req, "content") && length > 0) {
      const body = await dbPool.query<{ content: string | null }>(
        sql.NODE_CONTENT,
        [name, id, MAX_CONTENT],
      );
      detail.content = body.rows[0].content;
      detail.content_truncated = length > MAX_CONTENT;
    }

    res.json(detail);
  }),
);

nodesRouter.get(
  "/projects/:name/neighbors",
  route(async (req, res) => {
    const name = await requireProject(req.params.name);
    const id = requireQuery(req, "id");
    const direction = readQuery(req, "direction") ?? "both";
    if (!["in", "out", "both"].includes(direction)) {
      throw badRequest('Query parameter "direction" must be in, out or both');
    }
    const limit = readLimit(req);
    const offset = readOffset(req);
    const rows = await dbPool.query<Paged>(sql.NEIGHBORS, [
      name,
      id,
      direction,
      limit,
      offset,
    ]);
    res.json(paged(rows.rows, limit, offset));
  }),
);

nodesRouter.get(
  "/projects/:name/files",
  route(async (req, res) => {
    const name = await requireProject(req.params.name);
    const limit = readLimit(req);
    const offset = readOffset(req);
    const rows = await dbPool.query<Paged & { entities: string }>(sql.FILES, [
      name,
      pattern(readQuery(req, "q")),
      limit,
      offset,
    ]);
    const page = paged(rows.rows, limit, offset);
    res.json({
      ...page,
      items: page.items.map((item) => ({
        ...item,
        entities: count(item.entities),
      })),
    });
  }),
);
