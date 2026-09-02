import { Link } from "react-router";

import {
  Count,
  Empty,
  ErrorBox,
  Freshness,
  Spinner,
} from "../components/Common.js";
import { IndexButton } from "../components/IndexButton.js";
import { useApi } from "../hooks/useApi.js";
import type { Page, Project } from "../types.js";

export function ProjectsPage() {
  const { data, error, loading, reload } =
    useApi<Omit<Page<Project>, "total">>("/projects");

  if (error !== null) {
    return <ErrorBox message={error} />;
  }
  if (loading || data === null) {
    return <Spinner what="projects" />;
  }
  if (data.items.length === 0) {
    return (
      <Empty>
        No project is registered yet. Onboard a codebase with{" "}
        <code>context-install</code> from its directory and it appears here,
        with a button that builds its graph.
      </Empty>
    );
  }

  return (
    <>
      <h1>Projects</h1>
      <table className="grid">
        <thead>
          <tr>
            <th>Project</th>
            <th>Type</th>
            <th>Root</th>
            <th className="num">Nodes</th>
            <th className="num">Edges</th>
            <th className="num">Files</th>
            <th className="num">Plans</th>
            <th>Indexed</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {data.items.map((project) => (
            <tr key={project.name}>
              <td>
                <Link to={`/projects/${encodeURIComponent(project.name)}`}>
                  {project.name}
                </Link>
              </td>
              <td>
                <span className="kind">{project.type}</span>
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
                {project.root_path.includes("://") ? (
                  <span className="muted" title="records, not a tree">
                    -
                  </span>
                ) : project.sources.length === 0 ? (
                  <span className="muted" title="no directory to read yet">
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
    </>
  );
}

/** Where a project reads its files, in one cell.
 *
 * The primary directory, plus how many more there are: a project assembled
 * from several slices of a monorepo has no single root to name, and the
 * project page lists all of them.
 */
function Root({ project }: { project: Project }) {
  if (project.sources.length === 0 && !project.root_path.includes("://")) {
    return <span className="muted">no directory yet</span>;
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
