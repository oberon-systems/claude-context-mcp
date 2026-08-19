import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";

import { patch, post, query } from "../api.js";
import { Empty, ErrorBox, Pager, Spinner } from "../components/Common.js";
import { useApi, useDebounced } from "../hooks/useApi.js";
import type { Page, PlanFacets, PlanRow } from "../types.js";

const PAGE = 50;
const ALL = "*";
const GLOBAL = "_global";

export function PlansPage() {
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState(params.get("q") ?? "");
  const [creating, setCreating] = useState(false);
  const debounced = useDebounced(search);

  const project = params.get("project") ?? ALL;
  const status = params.get("status");
  const type = params.get("type");
  const offset = Number(params.get("offset") ?? "0");

  const facets = useApi<PlanFacets>("/plans/facets");
  const plans = useApi<Page<PlanRow>>(
    `/plans${query({
      project,
      status,
      type,
      q: debounced,
      limit: PAGE,
      offset,
    })}`,
  );

  function setParam(key: string, value: string | null) {
    const next = new URLSearchParams(params);
    if (value === null || value === "" || value === ALL) {
      next.delete(key);
    } else {
      next.set(key, value);
    }
    if (key !== "offset") {
      next.delete("offset");
    }
    setParams(next, { replace: key === "offset" });
  }

  async function changeStatus(plan: PlanRow, value: string) {
    await patch(`/plan${query({ id: plan.id })}`, { status: value });
    plans.reload();
    facets.reload();
  }

  return (
    <>
      <div className="row">
        <h1>Plans</h1>
        <button type="button" onClick={() => setCreating(true)}>
          New plan
        </button>
      </div>

      <div className="filters">
        <label>
          Project
          <select
            value={project}
            onChange={(event) => setParam("project", event.target.value)}
          >
            <option value={ALL}>every project</option>
            <option value={GLOBAL}>
              global ({facets.data?.global_plans ?? 0})
            </option>
            {(facets.data?.projects ?? []).map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Status
          <select
            value={status ?? ""}
            onChange={(event) => setParam("status", event.target.value)}
          >
            <option value="">any</option>
            {(facets.data?.statuses ?? []).map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label>
          Type
          <select
            value={type ?? ""}
            onChange={(event) => setParam("type", event.target.value)}
          >
            <option value="">any</option>
            {(facets.data?.types ?? []).map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <input
          className="search"
          placeholder="Search titles and bodies"
          value={search}
          onChange={(event) => {
            setSearch(event.target.value);
            setParam("q", event.target.value);
          }}
        />
      </div>

      {plans.error !== null && <ErrorBox message={plans.error} />}
      {plans.loading && plans.data === null && <Spinner what="plans" />}
      {plans.data !== null && plans.data.items.length === 0 && (
        <Empty>No plan matches these filters.</Empty>
      )}

      {plans.data !== null && plans.data.items.length > 0 && (
        <>
          <table className="grid">
            <thead>
              <tr>
                <th>Plan</th>
                <th>Project</th>
                <th>Type</th>
                <th>Status</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {plans.data.items.map((plan) => (
                <tr key={plan.id}>
                  <td>
                    <Link to={`/plans/${encodeURIComponent(plan.id)}`}>
                      {plan.title}
                    </Link>
                    <div className="muted id">{plan.id}</div>
                  </td>
                  <td>
                    {plan.project === null ? (
                      <span className="chip static">global</span>
                    ) : (
                      plan.project
                    )}
                  </td>
                  <td>
                    <span className={`chip static type-${plan.type}`}>
                      {plan.type}
                    </span>
                  </td>
                  <td>
                    <select
                      value={plan.status}
                      onChange={(event) =>
                        void changeStatus(plan, event.target.value)
                      }
                    >
                      {[
                        ...new Set([
                          plan.status,
                          "active",
                          "completed",
                          "archived",
                        ]),
                      ].map((value) => (
                        <option key={value} value={value}>
                          {value}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="muted">
                    {new Date(plan.updated_at).toLocaleString("en-GB")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pager
            total={plans.data.total}
            limit={plans.data.limit}
            offset={plans.data.offset}
            onOffset={(value) => setParam("offset", String(value))}
          />
        </>
      )}

      {creating && (
        <NewPlan
          projects={facets.data?.projects ?? []}
          onClose={() => setCreating(false)}
        />
      )}
    </>
  );
}

function NewPlan({
  projects,
  onClose,
}: {
  projects: string[];
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const [id, setId] = useState("");
  const [title, setTitle] = useState("");
  const [project, setProject] = useState("");
  const [type, setType] = useState("plan");
  const [error, setError] = useState<string | null>(null);

  async function create() {
    try {
      await post("/plans", {
        id,
        title,
        project,
        type,
        status: "active",
        content: `# ${title}\n`,
      });
      void navigate(`/plans/${encodeURIComponent(id)}`);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal">
        <h2>New plan</h2>
        {error !== null && <ErrorBox message={error} />}
        <label>
          Id, unique across the database
          <input value={id} onChange={(event) => setId(event.target.value)} />
        </label>
        <label>
          Title
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>
        <label>
          Project
          <select
            value={project}
            onChange={(event) => setProject(event.target.value)}
          >
            <option value="">global, listed under every project</option>
            {projects.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Type
          <select
            value={type}
            onChange={(event) => setType(event.target.value)}
          >
            <option value="plan">plan</option>
            <option value="template">template</option>
            <option value="procedure">procedure</option>
          </select>
        </label>
        <div className="row">
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            disabled={id === "" || title === ""}
            onClick={() => void create()}
          >
            Create
          </button>
        </div>
      </div>
    </div>
  );
}
