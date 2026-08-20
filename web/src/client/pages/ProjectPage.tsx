import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";

import { get, query } from "../api.js";
import {
  Count,
  Empty,
  ErrorBox,
  Freshness,
  Pager,
  Spinner,
} from "../components/Common.js";
import { DropModal } from "../components/DropModal.js";
import { GraphFrame } from "../components/GraphFrame.js";
import { NodeBrowser } from "../components/NodeBrowser.js";
import { useApi, useDebounced } from "../hooks/useApi.js";
import type { DropReport, FileRow, Page, ProjectDetail } from "../types.js";

const TABS = ["overview", "graph", "nodes", "files"] as const;
type Tab = (typeof TABS)[number];

export function ProjectPage() {
  const { name = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const [report, setReport] = useState<DropReport | null>(null);

  const tab = (params.get("tab") ?? "overview") as Tab;
  const detail = useApi<ProjectDetail>(`/projects/${encodeURIComponent(name)}`);

  function setParam(key: string, value: string | null) {
    const next = new URLSearchParams(params);
    if (value === null || value === "") {
      next.delete(key);
    } else {
      next.set(key, value);
    }
    setParams(next, { replace: true });
  }

  if (detail.error !== null) {
    return <ErrorBox message={detail.error} />;
  }
  if (detail.data === null) {
    return <Spinner what="the project" />;
  }
  const project = detail.data;

  return (
    <>
      <div className="row">
        <h1>{project.name}</h1>
        <button
          type="button"
          className="danger"
          onClick={() => {
            void get<DropReport>(
              `/projects/${encodeURIComponent(name)}/drop-report`,
            ).then(setReport);
          }}
        >
          Drop project
        </button>
      </div>
      <p>
        <span className="kind">{project.type}</span>{" "}
        <span className="path">{project.root_path}</span>
      </p>
      <p>
        <Freshness
          indexedAt={project.indexed_at}
          staleSeconds={project.stale_seconds}
        />
      </p>

      <nav className="tabs">
        {TABS.map((entry) => (
          <button
            key={entry}
            type="button"
            className={entry === tab ? "active" : undefined}
            onClick={() => setParam("tab", entry === "overview" ? null : entry)}
          >
            {entry}
          </button>
        ))}
      </nav>

      {tab === "overview" && (
        <Overview
          project={project}
          onType={(type) => {
            const next = new URLSearchParams(params);
            next.set("tab", "nodes");
            next.set("type", type);
            setParams(next);
          }}
        />
      )}
      {tab === "graph" && (
        <GraphFrame project={project.name} nodes={project.nodes} />
      )}
      {tab === "nodes" && (
        <NodeBrowser
          project={project.name}
          type={params.get("type")}
          onType={(value) => setParam("type", value)}
          selected={params.get("node")}
          onSelect={(id) => setParam("node", id)}
        />
      )}
      {tab === "files" && <FileList project={project.name} />}

      {report !== null && (
        <DropModal
          report={report}
          onClose={() => setReport(null)}
          onDropped={() => {
            setReport(null);
            void navigate("/");
          }}
        />
      )}
    </>
  );
}

function Overview({
  project,
  onType,
}: {
  project: ProjectDetail;
  onType: (type: string) => void;
}) {
  return (
    <>
      <div className="tiles">
        <Tile label="Nodes" value={project.nodes} />
        <Tile label="Edges" value={project.edges} />
        <Tile label="Files" value={project.files} />
        <Tile label="Summarised" value={project.summarised} />
        <Tile label="Manual summaries" value={project.manual_summaries} />
        <Tile label="Embeddings" value={project.embeddings} />
        <Tile label="Plans" value={project.plans} />
      </div>

      <h2>Node types</h2>
      <div className="chips">
        {project.types.map((entry) => (
          <button
            key={entry.type}
            type="button"
            className="chip"
            onClick={() => onType(entry.type)}
          >
            {entry.type} <Count value={entry.count} />
          </button>
        ))}
      </div>

      <h2>Relations</h2>
      {project.relations.length === 0 ? (
        <Empty>No edges. Nothing in this tree resolved to anything else.</Empty>
      ) : (
        <div className="chips">
          {project.relations.map((entry) => (
            <span key={entry.relation_type} className="chip static">
              {entry.relation_type} <Count value={entry.count} />
            </span>
          ))}
        </div>
      )}

      <p className="muted">
        File hashes cover {project.hashed_files.toLocaleString("en-US")} of{" "}
        {project.files.toLocaleString("en-US")} files: they are written only for
        the parsers in the ctxgraph package, so they are not an inventory of
        what was indexed.
      </p>
      <p>
        <Link to={`/plans?project=${encodeURIComponent(project.name)}`}>
          Plans tagged with this project
        </Link>
      </p>
    </>
  );
}

function Tile({ label, value }: { label: string; value: number }) {
  return (
    <div className="tile">
      <span className="tile-value">{value.toLocaleString("en-US")}</span>
      <span className="tile-label">{label}</span>
    </div>
  );
}

function FileList({ project }: { project: string }) {
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const debounced = useDebounced(search);
  const { data, error, loading } = useApi<Page<FileRow>>(
    `/projects/${encodeURIComponent(project)}/files${query({
      q: debounced,
      limit: 50,
      offset,
    })}`,
  );

  return (
    <>
      <input
        className="search"
        placeholder="Search paths"
        value={search}
        onChange={(event) => {
          setSearch(event.target.value);
          setOffset(0);
        }}
      />
      {error !== null && <ErrorBox message={error} />}
      {loading && data === null && <Spinner what="files" />}
      {data !== null && data.items.length === 0 && (
        <Empty>No file matches that.</Empty>
      )}
      {data !== null && data.items.length > 0 && (
        <>
          <table className="grid">
            <thead>
              <tr>
                <th>Path</th>
                <th className="num">Entities</th>
                <th>Summary</th>
                <th>Hashed</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((file) => (
                <tr key={file.id}>
                  <td className="path">{file.id}</td>
                  <td className="num">
                    <Count value={file.entities} />
                  </td>
                  <td className="summary">{file.summary}</td>
                  <td>
                    {file.hash === null ? (
                      <span className="muted">no</span>
                    ) : (
                      "yes"
                    )}
                  </td>
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
    </>
  );
}
