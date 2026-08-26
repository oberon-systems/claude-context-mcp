import { useCallback, useEffect, useState } from "react";

import { ApiError, get, post } from "../api.js";
import type { IndexJob } from "../types.js";

const POLL_MS = 2000;

/**
 * Start an index run and follow it.
 *
 * The work happens in the API, which holds every tree at /code/<project>, so
 * this only has to ask and then watch: a run over a large codebase outlives
 * any request, and the row it writes is what says how it went.
 */
export function IndexButton({
  project,
  onFinished,
}: {
  project: string;
  onFinished: () => void;
}) {
  const [job, setJob] = useState<IndexJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const path = `/projects/${encodeURIComponent(project)}/index`;

  // Adopt a run already going, so a reload does not lose sight of it.
  useEffect(() => {
    let alive = true;
    get<IndexJob | null>(path)
      .then((found) => {
        if (alive && found !== null && found.status === "running") {
          setJob(found);
        }
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [path]);

  useEffect(() => {
    if (job === null || job.status !== "running") {
      return;
    }
    const timer = setInterval(() => {
      get<IndexJob | null>(path)
        .then((found) => {
          if (found === null) {
            return;
          }
          setJob(found);
          if (found.status !== "running") {
            onFinished();
          }
        })
        .catch(() => undefined);
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [job, path, onFinished]);

  const start = useCallback(
    (fresh: boolean) => {
      setBusy(true);
      setError(null);
      post<IndexJob>(path, { fresh })
        .then(setJob)
        .catch((failure: unknown) =>
          setError(
            failure instanceof ApiError ? failure.message : "could not start",
          ),
        )
        .finally(() => setBusy(false));
    },
    [path],
  );

  const running = job !== null && job.status === "running";

  return (
    <div className="index-control">
      <button
        type="button"
        disabled={busy || running}
        onClick={() => start(false)}
        title="Walk the tree and refresh what changed"
      >
        {running ? "Indexing..." : "Index"}
      </button>
      <button
        type="button"
        className="secondary"
        disabled={busy || running}
        onClick={() => start(true)}
        title="Trust neither cache and parse every file again"
      >
        Fresh
      </button>
      {error !== null && <span className="bad">{error}</span>}
      {!running && job !== null && job.status === "failed" && (
        <span className="bad" title={job.error ?? ""}>
          failed
        </span>
      )}
      {!running && job !== null && job.status === "done" && (
        <span className="muted">{job.files ?? 0} files</span>
      )}
    </div>
  );
}
