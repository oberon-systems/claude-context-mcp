import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { patch, query, remove } from "../api.js";
import { ErrorBox, Markdown, Spinner } from "../components/Common.js";
import { useApi } from "../hooks/useApi.js";
import type { Suggestion } from "../types.js";

const STATUSES = ["open", "resolved", "wontfix"];

export function SuggestionPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const suggestion = useApi<Suggestion>(`/suggestion${query({ id })}`);

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (suggestion.data !== null) {
      setDraft(suggestion.data.detail);
    }
  }, [suggestion.data]);

  async function apply(body: Record<string, string>) {
    try {
      await patch(`/suggestion${query({ id })}`, body);
      setEditing(false);
      setError(null);
      suggestion.reload();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function drop() {
    try {
      await remove(`/suggestion${query({ id })}`);
      void navigate("/suggestions");
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  if (suggestion.error !== null) {
    return <ErrorBox message={suggestion.error} />;
  }
  if (suggestion.data === null) {
    return <Spinner what="the suggestion" />;
  }

  const row = suggestion.data;

  return (
    <>
      <p>
        <Link to="/suggestions">Back to the suggestions</Link>
      </p>
      <h1>{row.title}</h1>
      <p className="muted id">{row.id}</p>
      <dl className="inline">
        <dt>About</dt>
        <dd>{row.about ?? "global"}</dd>
        <dt>Kind</dt>
        <dd>{row.kind ?? "-"}</dd>
        <dt>Lever</dt>
        <dd>{row.lever ?? "-"}</dd>
        <dt>Hits</dt>
        <dd>{row.hits}</dd>
        <dt>First seen</dt>
        <dd>{row.first_seen ?? "-"}</dd>
        <dt>Last seen</dt>
        <dd>{row.last_seen ?? "-"}</dd>
      </dl>

      {error !== null && <ErrorBox message={error} />}

      <div className="row">
        <label>
          Status
          <select
            value={row.status}
            onChange={(event) => void apply({ status: event.target.value })}
          >
            {[...new Set([row.status, ...STATUSES])].map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <button type="button" onClick={() => setEditing(!editing)}>
          {editing ? "Preview" : "Edit"}
        </button>
        {editing && (
          <button type="button" onClick={() => void apply({ detail: draft })}>
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
        <Markdown text={row.detail} />
      )}
    </>
  );
}
