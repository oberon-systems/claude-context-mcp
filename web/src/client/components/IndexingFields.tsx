import { useState } from "react";

import { put } from "../api.js";
import { ErrorBox } from "./Common.js";
import type { Indexing } from "../types.js";

// What a schedule may say, as ctxgraph.config spells it, with what each mode
// means where the reader is - a select showing three words explains nothing.
const MODES: [string, string][] = [
  ["off", "off - indexed when asked, and not otherwise"],
  ["periodic", "periodic - every N minutes"],
  ["auto", "auto - when a file changes, and every N minutes regardless"],
];

// What an empty field means. The global level has no level above it, so what
// it falls back to is the built-in default rather than another row.
const INHERIT = "inherit from the level above";
const BUILT_IN = "the built-in default: off, every 60 minutes";

// The three fields as typed rather than as stored: empty is how a level
// declines to answer, and that is a state a number cannot hold.
export type IndexingDraft = {
  mode: string;
  interval: string;
  debounce: string;
};

export function draftOf(indexing: Indexing | undefined): IndexingDraft {
  return {
    mode: indexing?.mode ?? "",
    interval: indexing?.interval_minutes?.toString() ?? "",
    debounce: indexing?.debounce_minutes?.toString() ?? "",
  };
}

/** Turn a draft into a request body, an empty field meaning "not mine to say". */
export function bodyOf(draft: IndexingDraft) {
  return {
    mode: draft.mode,
    interval_minutes: draft.interval === "" ? null : Number(draft.interval),
    debounce_minutes: draft.debounce === "" ? null : Number(draft.debounce),
  };
}

export const EMPTY: IndexingDraft = { mode: "", interval: "", debounce: "" };

export function IndexingFields({
  draft,
  onChange,
  root = false,
}: {
  draft: IndexingDraft;
  onChange: (draft: IndexingDraft) => void;
  root?: boolean;
}) {
  const empty = root ? BUILT_IN : INHERIT;
  return (
    <div className="filters">
      <label>
        When it indexes
        <select
          value={draft.mode}
          onChange={(event) => onChange({ ...draft, mode: event.target.value })}
        >
          <option value="">{empty}</option>
          {MODES.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </label>
      <label>
        Every, minutes
        <input
          type="number"
          min={1}
          max={10080}
          placeholder={root ? "default" : "inherit"}
          value={draft.interval}
          onChange={(event) =>
            onChange({ ...draft, interval: event.target.value })
          }
        />
      </label>
      <label>
        And no more often than, minutes
        <input
          type="number"
          min={1}
          max={1440}
          placeholder={root ? "default" : "inherit"}
          value={draft.debounce}
          onChange={(event) =>
            onChange({ ...draft, debounce: event.target.value })
          }
        />
      </label>
    </div>
  );
}

/** The fields of one level, with the two buttons that write them.
 *
 * "Reset" saves an empty draft rather than deleting a row: the same request
 * clears the key, and a level with nothing to say is a level that inherits.
 */
export function IndexingEditor({
  path,
  indexing,
  onSaved,
  root = false,
}: {
  path: string;
  indexing: Indexing | undefined;
  onSaved: () => void;
  root?: boolean;
}) {
  const [draft, setDraft] = useState<IndexingDraft>(draftOf(indexing));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(value: IndexingDraft) {
    setBusy(true);
    setError(null);
    try {
      await put(path, bodyOf(value));
      setDraft(value);
      onSaved();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {error !== null && <ErrorBox message={error} />}
      <IndexingFields draft={draft} onChange={setDraft} root={root} />
      <div className="row">
        <button
          type="button"
          className="secondary"
          disabled={busy}
          onClick={() => void save(EMPTY)}
        >
          {root ? "Back to the built-in defaults" : "Inherit everything"}
        </button>
        <button type="button" disabled={busy} onClick={() => void save(draft)}>
          Save
        </button>
      </div>
    </>
  );
}
