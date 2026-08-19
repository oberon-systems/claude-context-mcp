import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { patch, query, remove } from "../api.js";
import { ErrorBox, Markdown, Spinner } from "../components/Common.js";
import { useApi } from "../hooks/useApi.js";
import type { Plan } from "../types.js";

export function PlanPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const plan = useApi<Plan>(`/plan${query({ id })}`);

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (plan.data !== null) {
      setDraft(plan.data.content);
    }
  }, [plan.data]);

  async function save() {
    try {
      await patch(`/plan${query({ id })}`, { content: draft });
      setEditing(false);
      setError(null);
      plan.reload();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function drop() {
    try {
      await remove(`/plan${query({ id })}`);
      void navigate("/plans");
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  if (plan.error !== null) {
    return <ErrorBox message={plan.error} />;
  }
  if (plan.data === null) {
    return <Spinner what="the plan" />;
  }

  return (
    <>
      <p>
        <Link to="/plans">Back to the plans</Link>
      </p>
      <h1>{plan.data.title}</h1>
      <p className="muted id">{plan.data.id}</p>
      <dl className="inline">
        <dt>Project</dt>
        <dd>{plan.data.project ?? "global"}</dd>
        <dt>Type</dt>
        <dd>{plan.data.type}</dd>
        <dt>Status</dt>
        <dd>{plan.data.status}</dd>
        <dt>Updated</dt>
        <dd>{new Date(plan.data.updated_at).toLocaleString("en-GB")}</dd>
      </dl>

      {error !== null && <ErrorBox message={error} />}

      <div className="row">
        <button type="button" onClick={() => setEditing(!editing)}>
          {editing ? "Preview" : "Edit"}
        </button>
        {editing && (
          <button type="button" onClick={() => void save()}>
            Save
          </button>
        )}
        <button type="button" className="danger" onClick={() => void drop()}>
          Delete
        </button>
      </div>

      {editing ? (
        <textarea
          className="editor"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
        />
      ) : (
        <Markdown text={plan.data.content} />
      )}
    </>
  );
}
