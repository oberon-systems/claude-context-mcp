import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";

import { patch, post } from "../api.js";
import {
  Count,
  Empty,
  ErrorBox,
  Freshness,
  SelectionBadge,
  Spinner,
} from "../components/Common.js";
import { IndexButton } from "../components/IndexButton.js";
import { useApi } from "../hooks/useApi.js";
import type { Page, Project } from "../types.js";

// The vocabulary of ctxgraph.config.KNOWN_PROJECT_TYPES, minus the ones that
// hold records rather than a tree. The server refuses those either way; the
// select simply never offers them.
export const PROJECT_TYPES = ["codebase", "docs", "config"];

// The columns worth ordering by. Every one is a number the row already
// carries, so the sort is done here rather than asked of the database:
// /projects answers with every project and no paging at all.
const SORTABLE = {
  nodes: "Nodes",
  edges: "Edges",
  files: "Files",
  plans: "Plans",
  indexed: "Indexed",
} as const;
type SortKey = keyof typeof SORTABLE;

/** A project that holds records written by an agent rather than an indexed tree. */
function isBuiltin(project: Project): boolean {
  return project.name.startsWith("_");
}

/** What a column sorts on. Freshness is an age, so it sorts on the age. */
function sortValue(project: Project, key: SortKey): number | null {
  return key === "indexed" ? project.stale_seconds : project[key];
}

export function ProjectsPage() {
  const [params, setParams] = useSearchParams();
  const [creating, setCreating] = useState(false);
  const { data, error, loading, reload } =
    useApi<Omit<Page<Project>, "total">>("/projects");

  // The box drives itself and mirrors into the URL, rather than reading back
  // from it: setSearchParams is asynchronous, so a second keystroke arriving
  // before the first has landed would write over it a character at a time.
  const [search, setSearch] = useState(params.get("q") ?? "");
  const sort = params.get("sort") as SortKey | null;
  const descending = params.get("dir") !== "asc";

  function setParam(key: string, value: string | null) {
    const next = new URLSearchParams(params);
    if (value === null || value === "") {
      next.delete(key);
    } else {
      next.set(key, value);
    }
    setParams(next, { replace: true });
  }

  function sortBy(key: SortKey) {
    const next = new URLSearchParams(params);
    next.set("sort", key);
    // A second press on the same column reverses it. A first press on a new
    // one starts high, which is the interesting end of every one of them.
    next.set("dir", sort === key && descending ? "asc" : "desc");
    setParams(next, { replace: true });
  }

  const shown = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const matched = (data?.items ?? []).filter(
      (project) =>
        needle === "" ||
        project.name.toLowerCase().includes(needle) ||
        project.root_path.toLowerCase().includes(needle),
    );
    if (sort === null || !(sort in SORTABLE)) {
      return matched;
    }
    return [...matched].sort((left, right) => {
      const a = sortValue(left, sort);
      const b = sortValue(right, sort);
      // A project that was never indexed has no age and no place on the
      // scale, so it settles at the bottom whichever way the column points
      // rather than reading as the freshest thing in the table.
      if (a === null || b === null) {
        return a === b ? 0 : a === null ? 1 : -1;
      }
      return descending ? b - a : a - b;
    });
  }, [data, search, sort, descending]);

  async function changeType(project: Project, type: string) {
    await patch(`/projects/${encodeURIComponent(project.name)}`, { type });
    reload();
  }

  if (error !== null) {
    return <ErrorBox message={error} />;
  }
  if (loading || data === null) {
    return <Spinner what="projects" />;
  }

  return (
    <>
      <div className="row">
        <h1>Projects</h1>
        <button type="button" onClick={() => setCreating(true)}>
          New project
        </button>
      </div>

      {data.items.length === 0 ? (
        <Empty>
          No project is registered yet. Onboard a codebase with{" "}
          <code>context-install</code> from its directory, or register one here
          and give it a directory afterwards.
        </Empty>
      ) : (
        <>
          <input
            className="search"
            placeholder="Search projects by name or path"
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setParam("q", event.target.value);
            }}
          />

          <table className="grid">
            <thead>
              <tr>
                <th>Project</th>
                <th>Type</th>
                <th>Root</th>
                <th className="num">
                  <SortHeader
                    label={SORTABLE.nodes}
                    active={sort === "nodes"}
                    descending={descending}
                    onSort={() => sortBy("nodes")}
                  />
                </th>
                <th className="num">
                  <SortHeader
                    label={SORTABLE.edges}
                    active={sort === "edges"}
                    descending={descending}
                    onSort={() => sortBy("edges")}
                  />
                </th>
                <th className="num">
                  <SortHeader
                    label={SORTABLE.files}
                    active={sort === "files"}
                    descending={descending}
                    onSort={() => sortBy("files")}
                  />
                </th>
                <th className="num">
                  <SortHeader
                    label={SORTABLE.plans}
                    active={sort === "plans"}
                    descending={descending}
                    onSort={() => sortBy("plans")}
                  />
                </th>
                <th>
                  <SortHeader
                    label={SORTABLE.indexed}
                    active={sort === "indexed"}
                    descending={descending}
                    onSort={() => sortBy("indexed")}
                  />
                </th>
                <th title="where the last index run read the selection from">
                  Sel
                </th>
                <th />
              </tr>
            </thead>
            <tbody>
              {shown.map((project) => (
                <tr key={project.name}>
                  <td>
                    <Link to={`/projects/${encodeURIComponent(project.name)}`}>
                      {project.name}
                    </Link>
                  </td>
                  <td>
                    {isBuiltin(project) ? (
                      <span className="kind">{project.type}</span>
                    ) : (
                      <select
                        value={project.type}
                        onChange={(event) =>
                          void changeType(project, event.target.value)
                        }
                      >
                        {[...new Set([project.type, ...PROJECT_TYPES])].map(
                          (value) => (
                            <option key={value} value={value}>
                              {value}
                            </option>
                          ),
                        )}
                      </select>
                    )}
                  </td>
                  <td className="path">
                    <Root project={project} />
                  </td>
                  <td className="num">
                    <Count value={project.nodes} />
                  </td>
                  <td className="num">
                    <Count value={project.edges} />
                  </td>
                  <td className="num">
                    <Count value={project.files} />
                  </td>
                  <td className="num">
                    {project.plans === 0 ? (
                      <span className="muted">0</span>
                    ) : (
                      <Link
                        to={`/plans?project=${encodeURIComponent(project.name)}`}
                      >
                        {project.plans}
                      </Link>
                    )}
                  </td>
                  <td>
                    <Freshness
                      indexedAt={project.indexed_at}
                      staleSeconds={project.stale_seconds}
                    />
                  </td>
                  <td>
                    <Selection project={project} />
                  </td>
                  <td>
                    {project.sources.length === 0 ? (
                      <span
                        className="muted"
                        title={
                          isBuiltin(project)
                            ? "records, not a tree"
                            : "no directory to read yet"
                        }
                      >
                        -
                      </span>
                    ) : (
                      <IndexButton project={project.name} onFinished={reload} />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {shown.length === 0 && <Empty>No project matches that.</Empty>}
        </>
      )}

      {creating && <NewProject onClose={() => setCreating(false)} />}
    </>
  );
}

function SortHeader({
  label,
  active,
  descending,
  onSort,
}: {
  label: string;
  active: boolean;
  descending: boolean;
  onSort: () => void;
}) {
  return (
    <button
      type="button"
      className={active ? "link sort active" : "link sort"}
      onClick={onSort}
    >
      {label}
      {active && <span aria-hidden="true">{descending ? " v" : " ^"}</span>}
    </button>
  );
}

/** Where a project read its selection, as one badge for the whole project.
 *
 * A project assembled from slices can read one from a file and another from
 * here, and the distinction that matters in a list is whether a repository is
 * still deciding any of it - so a single FILE anywhere shows as FILE.
 */
function Selection({ project }: { project: Project }) {
  const origins = project.sources.flatMap((source) =>
    [source.keep_source, source.ignore_source].filter(
      (origin): origin is string => origin !== null,
    ),
  );
  if (origins.length === 0) {
    return <SelectionBadge origin={null} />;
  }
  if (origins.includes("file")) {
    return <SelectionBadge origin="file" />;
  }
  const stored = origins.find((origin) => origin !== "default");
  return <SelectionBadge origin={stored ?? "default"} />;
}

/** Where a project reads its files, in one cell.
 *
 * The primary directory, plus how many more there are: a project assembled
 * from several slices of a monorepo has no single root to name, and the
 * project page lists all of them.
 */
function Root({ project }: { project: Project }) {
  if (project.sources.length === 0) {
    // A built-in project carries a scheme rather than a path - there is no
    // tree behind it and never will be.
    return (
      <span className="muted">
        {isBuiltin(project) ? project.root_path : "no directory yet"}
      </span>
    );
  }
  return (
    <>
      {project.root_path}
      {project.sources.length > 1 && (
        <span className="muted"> +{project.sources.length - 1} more</span>
      )}
    </>
  );
}

/** Register a project, which is a row rather than a mount.
 *
 * Nothing here can write the compose override or recreate a service, so the
 * directory is stored and the API's own reply says what finishes the job.
 */
function NewProject({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [rootPath, setRootPath] = useState("");
  const [type, setType] = useState("codebase");
  const [error, setError] = useState<string | null>(null);

  async function create() {
    try {
      await post("/projects", { name, root_path: rootPath, type });
      void navigate(`/projects/${encodeURIComponent(name)}`);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal">
        <h2>New project</h2>
        {error !== null && <ErrorBox message={error} />}
        <label>
          Name, which is also its <code>/mcp/&lt;name&gt;</code> address
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <label>
          Host path of its tree, or empty for a project that reads nothing yet
          <input
            value={rootPath}
            placeholder="/home/you/src/project"
            onChange={(event) => setRootPath(event.target.value)}
          />
        </label>
        <label>
          Type
          <select
            value={type}
            onChange={(event) => setType(event.target.value)}
          >
            {PROJECT_TYPES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <p className="muted">
          This writes the row and mounts nothing: the compose override is a file
          on the host and both services hold the mounts they started with. Run{" "}
          <code>make mounts</code> there, then index it from here.
        </p>
        <div className="row">
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            disabled={name === ""}
            onClick={() => void create()}
          >
            Create
          </button>
        </div>
      </div>
    </div>
  );
}
