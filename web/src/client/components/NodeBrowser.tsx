import { useState } from "react";

import { query } from "../api.js";
import { useApi, useDebounced } from "../hooks/useApi.js";
import type { Neighbor, NodeDetail, NodeRow, Page } from "../types.js";
import { Count, Empty, ErrorBox, Pager, Spinner } from "./Common.js";

const PAGE = 50;

export function NodeBrowser({
  project,
  type,
  onType,
  selected,
  onSelect,
}: {
  project: string;
  type: string | null;
  onType: (value: string | null) => void;
  selected: string | null;
  onSelect: (id: string | null) => void;
}) {
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const debounced = useDebounced(search);

  const path = `/projects/${encodeURIComponent(project)}/nodes${query({
    q: debounced,
    type,
    limit: PAGE,
    offset,
  })}`;
  const { data, error, loading } = useApi<Page<NodeRow>>(path);

  return (
    <>
      <div className="row">
        <input
          className="search"
          placeholder="Search names and ids"
          value={search}
          onChange={(event) => {
            setSearch(event.target.value);
            setOffset(0);
          }}
        />
        {type !== null && (
          <button type="button" onClick={() => onType(null)}>
            type: {type} (clear)
          </button>
        )}
      </div>

      {error !== null && <ErrorBox message={error} />}
      {loading && data === null && <Spinner what="nodes" />}
      {data !== null && data.items.length === 0 && (
        <Empty>No node matches that.</Empty>
      )}

      {data !== null && data.items.length > 0 && (
        <>
          <table className="grid">
            <thead>
              <tr>
                <th>Name</th>
                <th>Type</th>
                <th>File</th>
                <th>Summary</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((node) => (
                <tr
                  key={node.id}
                  className={node.id === selected ? "selected" : undefined}
                >
                  <td>
                    <button
                      type="button"
                      className="link"
                      onClick={() => onSelect(node.id)}
                    >
                      {node.name}
                    </button>
                  </td>
                  <td>{node.type}</td>
                  <td className="path">{node.file_path}</td>
                  <td className="summary">{node.summary}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pager
            total={data.total}
            limit={data.limit}
            offset={data.offset}
            onOffset={setOffset}
          />
        </>
      )}

      {selected !== null && (
        <NodePanel
          project={project}
          id={selected}
          onClose={() => onSelect(null)}
          onSelect={onSelect}
        />
      )}
    </>
  );
}

function NodePanel({
  project,
  id,
  onClose,
  onSelect,
}: {
  project: string;
  id: string;
  onClose: () => void;
  onSelect: (id: string) => void;
}) {
  const [showSource, setShowSource] = useState(false);
  const [offset, setOffset] = useState(0);
  const base = `/projects/${encodeURIComponent(project)}`;
  const node = useApi<NodeDetail>(
    `${base}/node${query({ id, content: showSource ? 1 : null })}`,
  );
  const neighbors = useApi<Page<Neighbor>>(
    `${base}/neighbors${query({ id, limit: PAGE, offset })}`,
  );

  return (
    <aside className="drawer">
      <div className="row">
        <h2>Node</h2>
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>
      <code className="id">{id}</code>

      {node.error !== null && <ErrorBox message={node.error} />}
      {node.data !== null && (
        <>
          <dl>
            <dt>Type</dt>
            <dd>{node.data.type}</dd>
            <dt>File</dt>
            <dd className="path">{node.data.file_path ?? "-"}</dd>
            <dt>Summary</dt>
            <dd>{node.data.summary || <span className="muted">none</span>}</dd>
          </dl>

          {node.data.content_length > 0 && (
            <>
              <button type="button" onClick={() => setShowSource(!showSource)}>
                {showSource ? "Hide source" : "Show source"} (
                <Count value={node.data.content_length} /> chars)
              </button>
              {showSource && node.data.content !== null && (
                <>
                  {node.data.content_truncated && (
                    <p className="muted">
                      Truncated: the first 100,000 characters only.
                    </p>
                  )}
                  <pre className="source">{node.data.content}</pre>
                </>
              )}
            </>
          )}
        </>
      )}

      <h3>Neighbours</h3>
      {neighbors.error !== null && <ErrorBox message={neighbors.error} />}
      {neighbors.data !== null && neighbors.data.items.length === 0 && (
        <Empty>This node has no edges.</Empty>
      )}
      {neighbors.data !== null && neighbors.data.items.length > 0 && (
        <>
          <table className="grid">
            <thead>
              <tr>
                <th>Direction</th>
                <th>Relation</th>
                <th>Node</th>
              </tr>
            </thead>
            <tbody>
              {neighbors.data.items.map((edge) => (
                <tr
                  key={`${edge.direction}-${edge.relation_type}-${edge.node_id}`}
                >
                  <td>{edge.direction}</td>
                  <td>{edge.relation_type}</td>
                  <td>
                    <button
                      type="button"
                      className="link"
                      onClick={() => {
                        setOffset(0);
                        setShowSource(false);
                        onSelect(edge.node_id);
                      }}
                    >
                      {edge.node_id}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pager
            total={neighbors.data.total}
            limit={neighbors.data.limit}
            offset={neighbors.data.offset}
            onOffset={setOffset}
          />
        </>
      )}
    </aside>
  );
}
