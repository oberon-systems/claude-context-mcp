import { useState } from "react";

// The viewer draws the whole graph in one request. Past a few thousand nodes
// that is a slow page and sometimes a failed one, so the cost is stated and
// the frame is mounted only when it is accepted.
const GRAPH_WARN_NODES = 5000;

export function GraphFrame({
  project,
  nodes,
}: {
  project: string;
  nodes: number;
}) {
  const [forced, setForced] = useState(false);
  const src = `/graph?project=${encodeURIComponent(project)}`;
  const heavy = nodes > GRAPH_WARN_NODES;

  if (nodes === 0) {
    return (
      <div className="empty">
        This project has no nodes to draw. Index it first.
      </div>
    );
  }

  if (heavy && !forced) {
    return (
      <div className="empty">
        <p>
          This project has {nodes.toLocaleString("en-US")} nodes. The viewer
          renders the whole graph in one request and may be slow or time out.
        </p>
        <button type="button" onClick={() => setForced(true)}>
          Render anyway
        </button>
      </div>
    );
  }

  return (
    <>
      <p className="muted">
        Served by the viewer through this origin.{" "}
        <a href={src} target="_blank" rel="noreferrer">
          Open in a new tab
        </a>
      </p>
      <iframe
        className="graph"
        src={src}
        title={`Code graph of ${project}`}
        sandbox="allow-scripts allow-same-origin"
      />
    </>
  );
}
