import { readBodyEnum, readBodyNumber } from "./args.js";

// The key `project_settings.settings` holds a schedule under, and the modes
// it may name, as ctxgraph.config spells both. The bounds are the same ones
// ctxgraph.schedule clamps to when it reads a row: a value refused here can
// still arrive through psql, so neither side is the only guard.
export const INDEXING_KEY = "indexing";
export const INDEXING_MODES = ["off", "periodic", "auto"] as const;
const MIN_INTERVAL = 1;
const MAX_INTERVAL = 10080;
const MIN_DEBOUNCE = 1;
const MAX_DEBOUNCE = 1440;

export type Indexing = {
  mode?: string;
  interval_minutes?: number;
  debounce_minutes?: number;
};

/**
 * Read a schedule out of a request body, or null when it states nothing.
 *
 * A field left out inherits from the level above, so a body that leaves out
 * all of them is a level with nothing to say - which is stored by removing
 * the key rather than by writing an empty object.
 */
export function readIndexing(body: unknown): Indexing | null {
  const value: Indexing = {};
  const mode = readBodyEnum(body, "mode", INDEXING_MODES);
  if (mode !== undefined) {
    value.mode = mode;
  }
  const interval = readBodyNumber(
    body,
    "interval_minutes",
    MIN_INTERVAL,
    MAX_INTERVAL,
  );
  if (interval !== undefined) {
    value.interval_minutes = interval;
  }
  const debounce = readBodyNumber(
    body,
    "debounce_minutes",
    MIN_DEBOUNCE,
    MAX_DEBOUNCE,
  );
  if (debounce !== undefined) {
    value.debounce_minutes = debounce;
  }
  return Object.keys(value).length === 0 ? null : value;
}
