import { useState } from "react";

import { remove } from "../api.js";
import { Count, ErrorBox } from "./Common.js";
import type { DropReport } from "../types.js";

export function DropModal({
  report,
  onClose,
  onDropped,
}: {
  report: DropReport;
  onClose: () => void;
  onDropped: () => void;
}) {
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function drop() {
    setBusy(true);
    try {
      await remove(`/projects/${encodeURIComponent(report.name)}`, {
        confirm: true,
      });
      onDropped();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal">
        <h2>Drop {report.name}</h2>
        <p>One {"`make index`"} rebuilds:</p>
        <ul>
          <li>
            <Count value={report.nodes} /> nodes
          </li>
          <li>
            <Count value={report.edges} /> edges
          </li>
          <li>
            <Count value={report.hashes} /> file hashes
          </li>
          <li>
            <Count value={report.embeddings} /> embeddings
          </li>
        </ul>
        <p>Nothing rebuilds:</p>
        <ul>
          <li>
            <Count value={report.summaries} /> manual summaries
          </li>
        </ul>
        <p className="muted">
          <Count value={report.plans} /> plans and{" "}
          <Count value={report.suggestions} /> suggestions tagged with this name
          are kept: the tag is not a foreign key, and both outlive the codebase
          they were written about.
        </p>
        {error !== null && <ErrorBox message={error} />}
        <label>
          Type <code>{report.name}</code> to confirm
          <input
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
            autoFocus
          />
        </label>
        <div className="row">
          <button type="button" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button
            type="button"
            className="danger"
            disabled={typed !== report.name || busy}
            onClick={() => void drop()}
          >
            Drop the graph
          </button>
        </div>
      </div>
    </div>
  );
}
