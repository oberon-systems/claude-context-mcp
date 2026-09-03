import { Router } from "express";

import { readBodyString, route } from "../args.js";
import { dbPool } from "../db.js";
import { INDEXING_KEY, readIndexing } from "../indexing.js";
import * as sql from "../queries.js";

// The built-in project the global defaults hang off, as migration 0012
// creates it and ctxgraph.config.SETTINGS_PROJECT names it. Its one row is
// what every project falls back to when neither it nor one of its directories
// has said what to index.
const SETTINGS_PROJECT = "_settings";

export const settingsRouter = Router();

settingsRouter.get(
  "/settings",
  route(async (_req, res) => {
    const row = await dbPool.query(sql.PROJECT_LEVEL_SETTINGS, [
      SETTINGS_PROJECT,
      "",
    ]);
    res.json(
      row.rows[0] ?? {
        ctxkeep: null,
        ctxignore: null,
        settings: {},
        updated_at: null,
      },
    );
  }),
);

settingsRouter.put(
  "/settings",
  route(async (req, res) => {
    const body = req.body as Record<string, unknown> | undefined;
    // Empty is NULL rather than an empty string, for the same reason a
    // project's own row treats it so: a level that says nothing has to be
    // indistinguishable from one that is not there.
    const keep = readBodyString(body, "ctxkeep")?.trim();
    const ignore = readBodyString(body, "ctxignore")?.trim();
    const saved = await dbPool.query(sql.SAVE_SETTINGS, [
      SETTINGS_PROJECT,
      "",
      keep === undefined || keep === "" ? null : `${keep}\n`,
      ignore === undefined || ignore === "" ? null : `${ignore}\n`,
    ]);
    res.json(saved.rows[0]);
  }),
);

// The schedule every project falls back to. Stored beside the selection
// documents rather than in a column of its own, so the knobs that follow are
// keys of one object instead of a migration each.
settingsRouter.put(
  "/settings/indexing",
  route(async (req, res) => {
    const value = readIndexing(req.body);
    const saved =
      value === null
        ? await dbPool.query(sql.CLEAR_INDEXING, [
            SETTINGS_PROJECT,
            "",
            INDEXING_KEY,
          ])
        : await dbPool.query(sql.SAVE_INDEXING, [
            SETTINGS_PROJECT,
            "",
            JSON.stringify({ [INDEXING_KEY]: value }),
          ]);
    res.json(saved.rows[0] ?? { project: SETTINGS_PROJECT, alias: "" });
  }),
);
