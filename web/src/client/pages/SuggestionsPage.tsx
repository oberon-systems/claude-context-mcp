import { useState } from "react";
import { Link, useSearchParams } from "react-router";

import { patch, query } from "../api.js";
import { Empty, ErrorBox, Pager, Spinner } from "../components/Common.js";
import { useApi, useDebounced } from "../hooks/useApi.js";
import type { Page, SuggestionFacets, SuggestionRow } from "../types.js";

const PAGE = 50;
const ALL = "*";
const GLOBAL = "_global";
const STATUSES = ["open", "resolved", "wontfix"];

export function SuggestionsPage() {
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState(params.get("q") ?? "");
  const debounced = useDebounced(search);

  const about = params.get("about") ?? ALL;
  const status = params.get("status");
  const kind = params.get("kind");
  const offset = Number(params.get("offset") ?? "0");

  const facets = useApi<SuggestionFacets>("/suggestions/facets");
  const suggestions = useApi<Page<SuggestionRow>>(
    `/suggestions${query({
      about,
      status,
      kind,
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

  async function changeStatus(row: SuggestionRow, value: string) {
    await patch(`/suggestion${query({ id: row.id })}`, { status: value });
    suggestions.reload();
    facets.reload();
  }

  return (
    <>
      <div className="row">
        <h1>Suggestions</h1>
      </div>
      <p className="muted">
        What the graph could not answer, most often hit first. Written by the
        agents that hit the gap; this page triages them.
      </p>

      <div className="filters">
        <label>
          About
          <select
            value={about}
            onChange={(event) => setParam("about", event.target.value)}
          >
            <option value={ALL}>every project</option>
            <option value={GLOBAL}>
              global ({facets.data?.global_suggestions ?? 0})
            </option>
            {(facets.data?.abouts ?? []).map((name) => (
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
          Kind
          <select
            value={kind ?? ""}
            onChange={(event) => setParam("kind", event.target.value)}
          >
            <option value="">any</option>
            {(facets.data?.kinds ?? []).map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <input
          className="search"
          placeholder="Search titles and details"
          value={search}
          onChange={(event) => {
            setSearch(event.target.value);
            setParam("q", event.target.value);
          }}
        />
      </div>

      {suggestions.error !== null && <ErrorBox message={suggestions.error} />}
      {suggestions.loading && suggestions.data === null && (
        <Spinner what="suggestions" />
      )}
      {suggestions.data !== null && suggestions.data.items.length === 0 && (
        <Empty>No suggestion matches these filters.</Empty>
      )}

      {suggestions.data !== null && suggestions.data.items.length > 0 && (
        <>
          <table className="grid">
            <thead>
              <tr>
                <th>Suggestion</th>
                <th>About</th>
                <th>Kind</th>
                <th>Lever</th>
                <th>Hits</th>
                <th>Status</th>
                <th>Last seen</th>
              </tr>
            </thead>
            <tbody>
              {suggestions.data.items.map((row) => (
                <tr key={row.id}>
                  <td>
                    <Link to={`/suggestions/${encodeURIComponent(row.id)}`}>
                      {row.title}
                    </Link>
                    <div className="muted id">{row.id}</div>
                  </td>
                  <td>
                    {row.about === null ? (
                      <span className="chip static">global</span>
                    ) : (
                      row.about
                    )}
                  </td>
                  <td>
                    {row.kind === null ? (
                      <span className="muted">-</span>
                    ) : (
                      <span className="chip static">{row.kind}</span>
                    )}
                  </td>
                  <td className="muted">{row.lever ?? "-"}</td>
                  <td>{row.hits}</td>
                  <td>
                    <select
                      value={row.status}
                      onChange={(event) =>
                        void changeStatus(row, event.target.value)
                      }
                    >
                      {[...new Set([row.status, ...STATUSES])].map((value) => (
                        <option key={value} value={value}>
                          {value}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="muted">{row.last_seen ?? "never"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pager
            total={suggestions.data.total}
            limit={suggestions.data.limit}
            offset={suggestions.data.offset}
            onOffset={(value) => setParam("offset", String(value))}
          />
        </>
      )}
    </>
  );
}
