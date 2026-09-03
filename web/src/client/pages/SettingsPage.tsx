import { useEffect, useState } from "react";

import { put } from "../api.js";
import { ErrorBox, Spinner } from "../components/Common.js";
import { IndexingEditor } from "../components/IndexingFields.js";
import { useApi } from "../hooks/useApi.js";
import type { SettingsLevel } from "../types.js";

/** The selection every project falls back to.
 *
 * Read last, after the directory and the project have both declined to say
 * what to index - and after a `.ctxkeep` in the tree, which beats all three.
 * Left empty, the fallback is the built-in set of file types the parsers know.
 */
export function SettingsPage() {
  const { data, error, loading, reload } = useApi<SettingsLevel>("/settings");
  const [keep, setKeep] = useState<string | null>(null);
  const [ignore, setIgnore] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  // The editors start from what is stored, and stop tracking it once typed in.
  useEffect(() => {
    if (data !== null) {
      setKeep((current) => current ?? data.ctxkeep ?? "");
      setIgnore((current) => current ?? data.ctxignore ?? "");
    }
  }, [data]);

  if (error !== null) {
    return <ErrorBox message={error} />;
  }
  if (loading || data === null || keep === null || ignore === null) {
    return <Spinner what="the defaults" />;
  }

  async function save() {
    setSaving(true);
    setFailure(null);
    try {
      await put("/settings", { ctxkeep: keep, ctxignore: ignore });
      reload();
    } catch (reason: unknown) {
      setFailure(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <h1>Global defaults</h1>
      <p className="muted">
        What every project falls back to when neither it nor one of its
        directories has said otherwise.
      </p>

      {failure !== null && <ErrorBox message={failure} />}

      <h2>Indexing schedule</h2>
      <p className="muted">
        When a project is indexed without anyone asking. <code>auto</code>{" "}
        watches the mounted directories and starts a run once they have been
        quiet for the throttle - and still sweeps on the interval, because a
        watch is blind on a network filesystem and where the host has run out of
        inotify watches.
      </p>
      <IndexingEditor
        root
        path="/settings/indexing"
        indexing={data.settings?.indexing}
        onSaved={reload}
      />

      <h2>Selection</h2>
      <p className="muted">
        What a project indexes when neither it nor one of its directories has
        said. A <code>.ctxkeep</code> or <code>.ctxignore</code> in a tree beats
        this and everything else; leaving both empty falls back to the built-in
        set of file types the parsers know.
      </p>

      <div className="editors">
        <label>
          ctxkeep - what becomes a node
          <textarea
            value={keep}
            rows={20}
            onChange={(event) => setKeep(event.target.value)}
          />
        </label>
        <label>
          ctxignore - what is pruned, on top of the built-in skip list
          <textarea
            value={ignore}
            rows={20}
            onChange={(event) => setIgnore(event.target.value)}
          />
        </label>
      </div>

      <div className="row">
        {data.updated_at !== null && (
          <span className="muted">
            Last saved {new Date(data.updated_at).toLocaleString("en-GB")}
          </span>
        )}
        <button type="button" disabled={saving} onClick={() => void save()}>
          Save
        </button>
      </div>
    </>
  );
}
