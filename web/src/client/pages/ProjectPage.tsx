import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";

import { get, patch, post, query, remove } from "../api.js";
import {
  Count,
  Empty,
  ErrorBox,
  Freshness,
  Pager,
  SelectionBadge,
  Spinner,
} from "../components/Common.js";
import { DropModal } from "../components/DropModal.js";
import { GraphFrame } from "../components/GraphFrame.js";
import { NodeBrowser } from "../components/NodeBrowser.js";
import { PROJECT_TYPES } from "./ProjectsPage.js";
import { SettingsTab } from "./SettingsTab.js";
import { useApi, useDebounced } from "../hooks/useApi.js";
import type { DropReport, FileRow, Page, ProjectDetail } from "../types.js";

const TABS = ["overview", "graph", "nodes", "files", "settings"] as const;
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
        {project.name.startsWith("_") ? (
          <span className="kind">{project.type}</span>
        ) : (
          <select
            value={project.type}
            onChange={(event) =>
              void patch(`/projects/${encodeURIComponent(name)}`, {
                type: event.target.value,
              }).then(() => {
                detail.reload();
              })
            }
          >
            {[...new Set([project.type, ...PROJECT_TYPES])].map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        )}{" "}
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
          onSources={() => {
            detail.reload();
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
      {tab === "settings" && <SettingsTab project={project.name} />}

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
  onSources,
}: {
  project: ProjectDetail;
  onType: (type: string) => void;
  onSources: () => void;
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

      <h2>Directories</h2>
      <Directories project={project} onChanged={onSources} />

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

/** What a project reads, and the two ways that changes.
 *
 * Neither writes a mount: the compose override is a file on the host and both
 * services hold the mounts they started with, so the API answers with what
 * finishes the job and that is shown rather than summarised.
 */
function Directories({
  project,
  onChanged,
}: {
  project: ProjectDetail;
  onChanged: () => void;
}) {
  const [rootPath, setRootPath] = useState("");
  const [alias, setAlias] = useState("");
  const [hint, setHint] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const path = `/projects/${encodeURIComponent(project.name)}/sources`;

  async function run(work: () => Promise<{ mounts?: string }>) {
    setError(null);
    try {
      const answer = await work();
      setHint(answer.mounts ?? null);
      onChanged();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  return (
    <>
      {error !== null && <ErrorBox message={error} />}
      {project.sources.length === 0 ? (
        <Empty>
          This project reads no directory yet. Name one below, or run{" "}
          <code>context-source {project.name} &lt;alias&gt;</code> from the
          directory itself.
        </Empty>
      ) : (
        <table className="grid">
          <thead>
            <tr>
              <th>Alias</th>
              <th>Host path</th>
              <th title="where the last index run read the selection from">
                Selection
              </th>
              <th />
            </tr>
          </thead>
          <tbody>
            {project.sources.map((source) => (
              <tr key={source.alias}>
                <td>
                  {source.alias === "" ? (
                    <span className="muted">the whole tree</span>
                  ) : (
                    <code>{source.alias}/</code>
                  )}
                </td>
                <td className="path">{source.root_path}</td>
                <td>
                  <SelectionBadge origin={source.keep_source} />{" "}
                  <SelectionBadge origin={source.ignore_source} />
                </td>
                <td>
                  {project.sources.length > 1 && (
                    <button
                      type="button"
                      className="danger"
                      onClick={() =>
                        void run(() =>
                          remove<{ mounts?: string }>(
                            `${path}/${encodeURIComponent(source.alias)}`,
                          ),
                        )
                      }
                    >
                      Drop
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="filters">
        <label>
          Host path
          <input
            value={rootPath}
            placeholder="/home/you/src/mono/services/api"
            onChange={(event) => setRootPath(event.target.value)}
          />
        </label>
        <label>
          Alias, derived from the last segment when empty
          <input
            value={alias}
            onChange={(event) => setAlias(event.target.value)}
          />
        </label>
        <button
          type="button"
          disabled={rootPath === ""}
          onClick={() =>
            void run(async () => {
              const answer = await post<{ mounts?: string }>(path, {
                root_path: rootPath,
                alias,
              });
              setRootPath("");
              setAlias("");
              return answer;
            })
          }
        >
          Add directory
        </button>
      </div>

      {hint !== null && <p className="stale">{hint}</p>}

      {project.sources.length > 1 && (
        <p className="muted">
          Each alias opens every node id that directory produced, so a file of
          the first slice is <code>{project.sources[0].alias}/...</code> in the
          graph. The last directory cannot be dropped: a project with no tree is
          dropped itself.
        </p>
      )}
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
