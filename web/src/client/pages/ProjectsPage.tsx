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
        Nothing is indexed yet. Onboard a codebase with{" "}
        <code>context-install</code> from its directory, then index it here.
      </Empty>
    );
  }

  return (
    <>
      <h1>Indexed projects</h1>
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
              <td className="path">{project.root_path}</td>
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
