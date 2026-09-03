import { useState } from "react";

import { post, put, remove } from "../api.js";
import {
  Count,
  Empty,
  ErrorBox,
  SelectionBadge,
  Spinner,
} from "../components/Common.js";
import { IndexingEditor } from "../components/IndexingFields.js";
import { useApi } from "../hooks/useApi.js";
import type {
  FileType,
  Page,
  ProjectSchedule,
  ProjectSettings,
  ScanResult,
  SettingsLevel,
} from "../types.js";

// The project level is the empty alias, which a URL path cannot carry. `-` is
// what the mount listing already writes for it, so it is what this spells too.
export const PROJECT_LEVEL = "-";

/** What a project indexes, and where that answer comes from.
 *
 * The two documents are the same commented text a `.ctxkeep` and a
 * `.ctxignore` hold, because they are that: a repository that still ships the
 * pair keeps deciding its own index, and what is edited here takes over only
 * once those files are gone.
 */
export function SettingsTab({ project }: { project: string }) {
  const settings = useApi<ProjectSettings>(
    `/projects/${encodeURIComponent(project)}/settings`,
  );
  const types = useApi<Omit<Page<FileType>, "total" | "limit" | "offset">>(
    `/projects/${encodeURIComponent(project)}/file-types`,
  );
  const schedule = useApi<ProjectSchedule>(
    `/projects/${encodeURIComponent(project)}/schedule`,
  );
  const path = `/projects/${encodeURIComponent(project)}`;

  if (settings.error !== null) {
    return <ErrorBox message={settings.error} />;
  }
  if (settings.data === null) {
    return <Spinner what="the settings" />;
  }

  return (
    <>
      <h2>File types indexed now</h2>
      {types.data === null || types.data.items.length === 0 ? (
        <Empty>
          Nothing is in the graph yet, so there is no answer to what this
          project indexes. Index it and this fills in.
        </Empty>
      ) : (
        <div className="chips">
          {types.data.items.map((entry) => (
            <span key={entry.extension} className="chip static">
              {entry.extension} <Count value={entry.count} />
            </span>
          ))}
        </div>
      )}
      <p className="muted">
        What the last index run actually wrote, not what the selection below
        would pick up. The two differ whenever the selection has changed since.
      </p>

      <h2>Schedule</h2>
      {schedule.data !== null && <Effective schedule={schedule.data} />}
      <IndexingEditor
        key={`project-${settings.data.project?.updated_at ?? "none"}`}
        path={`${path}/indexing/${PROJECT_LEVEL}`}
        indexing={settings.data.project?.settings?.indexing}
        onSaved={() => {
          settings.reload();
          schedule.reload();
        }}
      />

      {settings.data.sources.length > 1 &&
        settings.data.sources.map((source) => (
          <div className="level" key={`schedule-${source.alias}`}>
            <h3>
              <code>{source.alias}/</code> only
            </h3>
            <IndexingEditor
              path={`${path}/indexing/${encodeURIComponent(source.alias)}`}
              indexing={source.settings?.indexing}
              onSaved={() => {
                settings.reload();
                schedule.reload();
              }}
            />
          </div>
        ))}

      <h2>Selection</h2>
      {settings.data.sources.length === 0 ? (
        <Empty>
          This project reads no directory, so there is nothing to select from.
          Add one on the overview tab first.
        </Empty>
      ) : (
        settings.data.sources.map((source) => (
          <Level
            key={source.alias}
            project={project}
            alias={source.alias}
            heading={
              source.alias === "" ? (
                <>
                  The whole tree{" "}
                  <span className="path">{source.root_path}</span>
                </>
              ) : (
                <>
                  <code>{source.alias}/</code>{" "}
                  <span className="path">{source.root_path}</span>
                </>
              )
            }
            level={source}
            origins={[source.keep_source, source.ignore_source]}
            onSaved={() => {
              settings.reload();
            }}
          />
        ))
      )}

      {settings.data.sources.length > 1 && (
        <Level
          project={project}
          alias={PROJECT_LEVEL}
          heading="Every directory of this project"
          level={settings.data.project}
          origins={[]}
          onSaved={() => {
            settings.reload();
          }}
        />
      )}

      <p className="muted">
        Resolved most specific first: the directory, then the project, then the
        global default. A <code>.ctxkeep</code> or <code>.ctxignore</code> still
        in the tree beats all three, and goes on doing so until it is deleted.
      </p>
    </>
  );
}

/** What the levels above come to, once folded into the one run they share.
 *
 * The fold is the API's: the most eager directory decides the project, and
 * only the directories in `auto` are watched. Stating it here is what keeps
 * the rule from becoming folklore about a settings page.
 */
function Effective({ schedule }: { schedule: ProjectSchedule }) {
  const when = (stamp: string | null) =>
    stamp === null ? "never" : new Date(stamp).toLocaleString("en-GB");
  const minutes = (count: number) => `${count} minute${count === 1 ? "" : "s"}`;
  return (
    <>
      <p className={schedule.mode === "off" ? "muted" : undefined}>
        {schedule.mode === "off" ? (
          <>
            Nothing indexes this project on its own. The Index button on the
            overview tab is the only thing that starts a run.
          </>
        ) : schedule.mode === "periodic" ? (
          <>Indexed every {minutes(schedule.interval_minutes)}.</>
        ) : (
          <>
            Watching {schedule.watched.length} of {schedule.levels.length}{" "}
            director
            {schedule.levels.length === 1 ? "y" : "ies"}: indexed when one of
            them changes, at most once every{" "}
            {minutes(schedule.debounce_minutes)}, and swept every{" "}
            {minutes(schedule.interval_minutes)} regardless.
          </>
        )}{" "}
        <span className="muted">
          Last run {when(schedule.last_run)}
          {schedule.next_run !== null && (
            <> - next sweep {when(schedule.next_run)}</>
          )}
          .
        </span>
      </p>
      {!schedule.scheduler && schedule.mode !== "off" && (
        <p className="stale">
          The service is running with INDEX_SCHEDULER off, so nothing acts on
          this. What is saved here takes effect when it is turned back on.
        </p>
      )}
    </>
  );
}

/** One editable level of the selection.
 *
 * `alias` is what the route is addressed by, so the project level arrives as
 * `-`; `origins` is empty for a level no single directory answers for.
 */
function Level({
  project,
  alias,
  heading,
  level,
  origins,
  onSaved,
}: {
  project: string;
  alias: string;
  heading: React.ReactNode;
  level: SettingsLevel | null;
  origins: (string | null)[];
  onSaved: () => void;
}) {
  const [keep, setKeep] = useState(level?.ctxkeep ?? "");
  const [ignore, setIgnore] = useState(level?.ctxignore ?? "");
  const [report, setReport] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const path = `/projects/${encodeURIComponent(project)}`;
  const shadowed = origins.includes("file");

  async function run(work: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="level">
      <div className="row">
        <h3>{heading}</h3>
        {origins.length > 0 && (
          <span>
            ctxkeep <SelectionBadge origin={origins[0] ?? null} /> ctxignore{" "}
            <SelectionBadge origin={origins[1] ?? null} />
          </span>
        )}
      </div>

      {error !== null && <ErrorBox message={error} />}
      {shadowed && (
        <p className="stale">
          A selection file in the tree is deciding this directory. What is saved
          here is stored and unused until that file is deleted.
        </p>
      )}

      <div className="editors">
        <label>
          ctxkeep - what becomes a node. Empty falls back to the level above.
          <textarea
            value={keep}
            rows={16}
            onChange={(event) => setKeep(event.target.value)}
          />
        </label>
        <label>
          ctxignore - what is pruned, on top of the built-in skip list.
          <textarea
            value={ignore}
            rows={16}
            onChange={(event) => setIgnore(event.target.value)}
          />
        </label>
      </div>

      {report !== null && <pre className="report">{report}</pre>}

      <div className="row">
        <button
          type="button"
          disabled={busy || alias === PROJECT_LEVEL}
          title={
            alias === PROJECT_LEVEL
              ? "a scan reads one directory, and this level is every one of them"
              : "propose a selection from the file types this directory holds"
          }
          onClick={() =>
            void run(async () => {
              const scan = await post<ScanResult>(`${path}/scan`, { alias });
              setKeep(scan.ctxkeep);
              setIgnore(scan.ctxignore);
              setReport(scan.report);
            })
          }
        >
          Regenerate from the tree
        </button>
        <button
          type="button"
          className="secondary"
          disabled={busy}
          onClick={() =>
            void run(async () => {
              await remove(
                `${path}/settings/${encodeURIComponent(alias || PROJECT_LEVEL)}`,
              );
              setKeep("");
              setIgnore("");
              setReport(null);
              onSaved();
            })
          }
        >
          Reset to the level above
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() =>
            void run(async () => {
              await put(
                `${path}/settings/${encodeURIComponent(alias || PROJECT_LEVEL)}`,
                { ctxkeep: keep, ctxignore: ignore },
              );
              onSaved();
            })
          }
        >
          Save
        </button>
      </div>
    </div>
  );
}
