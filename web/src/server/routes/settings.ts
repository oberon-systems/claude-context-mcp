import { Router } from "express";

import { readBodyString, route } from "../args.js";
import { dbPool } from "../db.js";
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
      row.rows[0] ?? { ctxkeep: null, ctxignore: null, updated_at: null },
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
