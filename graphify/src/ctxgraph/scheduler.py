"""Start the runs the schedule asks for, without anyone asking.

This is the only process that indexes: the trees are mounted here read-only
and the parsers are in this image, so a scheduled run is the same thread a
button press starts, decided by `ctxgraph.schedule` instead of by a request.

Two signals drive it, and both end in `indexjobs.open_run`. A timer covers
every mode: it is coarse, it walks trees nothing touched, and it is the only
signal that cannot go quiet. Over the directories in `auto` there is also a
watch, which is immediate and can go quiet - a tree on a filesystem inotify
says nothing about, or a watch the kernel refused because
`fs.inotify.max_user_watches` is exhausted. That is why the timer stays under
`auto` as a fallback rather than being replaced by the watch.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pathspec
from psycopg2.extensions import connection as Connection
from psycopg2.extensions import cursor as Cursor

from ctxgraph import indexjobs, schedule
from ctxgraph.config import (
    SCHEDULER_STARTS_PER_TICK,
    SCHEDULER_TICK_SECONDS,
)
from ctxgraph.discovery import selects
from ctxgraph.identifiers import project_mount, source_mount
from ctxgraph.selection import resolve as resolve_selection
from ctxgraph.storage import (
    get_db_connection,
    list_all_sources,
    list_indexable_projects,
)

LOG = logging.getLogger(__name__)

# How long a watch that died is left dead before it is tried again. A watch
# fails for reasons a retry does not fix - a filesystem that reports nothing,
# a limit the container cannot raise - and the fallback sweep is what keeps
# those projects indexed meanwhile, so retrying is worth doing rarely and
# worth doing quietly.
WATCH_RETRY_SECONDS = 600

# What one watched directory is: the project it belongs to and the specs that
# decide whether a path under it is one the graph describes.
Target = tuple[str, pathspec.PathSpec | None, pathspec.PathSpec | None]


class Watcher:
    """One inotify watch over every directory a project set to `auto`.

    A single watch for all of them rather than one per project: the watches are
    a kernel resource, and the mapping back from a path to a project is a
    prefix match this already has to do for the specs.
    """

    def __init__(self, targets: dict[str, Target], mark: Callable[[str], None]) -> None:
        """Take the directories to watch and what to call when one changes."""
        self.targets = targets
        self._mark = mark
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="index-watch", daemon=True
        )
        # Longest first, so a directory mounted inside another is matched
        # against itself rather than against the one containing it.
        self._order = sorted(targets, key=len, reverse=True)

    def start(self) -> None:
        """Begin watching, on a thread of its own."""
        self._thread.start()

    def stop(self) -> None:
        """Ask the watch to finish."""
        self._stop.set()

    def alive(self) -> bool:
        """Whether the watch is still running, rather than dead of an error."""
        return self._thread.is_alive()

    def project_of(self, path: str) -> str | None:
        """Which project a changed path belongs to, if the graph describes it."""
        for mount in self._order:
            if path != mount and not path.startswith(f"{mount}{os.sep}"):
                continue
            project, keep_spec, ignore_spec = self.targets[mount]
            rel_path = os.path.relpath(path, mount).replace(os.sep, "/")
            return project if selects(rel_path, keep_spec, ignore_spec) else None
        return None

    def _run(self) -> None:
        # Imported here so the API starts on a host where the watch cannot be
        # built at all; the timer still covers every project in that case.
        from watchfiles import watch

        try:
            # A tree holds directories this process cannot read - a cache
            # written by another user, a mode nobody meant - and one of them
            # would otherwise refuse the watch for every project at once.
            for changes in watch(
                *self._order, stop_event=self._stop, ignore_permission_denied=True
            ):
                for _, path in changes:
                    project = self.project_of(path)
                    if project is not None:
                        self._mark(project)
        except OSError:
            # An exhausted watch limit lands here. It is a host sysctl this
            # container cannot raise, so it is reported and the fallback sweep
            # is left to keep those projects indexed.
            LOG.exception(
                "Watching %d directories failed; those projects fall back to "
                "their interval",
                len(self._order),
            )
        except Exception:  # noqa: BLE001 - a dead thread must say why
            LOG.exception("The index watch stopped")


class Scheduler:
    """The tick loop: what is due, what is watched, and what gets started."""

    def __init__(self, tick_seconds: int = SCHEDULER_TICK_SECONDS) -> None:
        """Take how often to look, leaving every other answer to the database."""
        self._tick_seconds = max(1, tick_seconds)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._dirty: set[str] = set()
        self._watcher: Watcher | None = None
        self._built: datetime | None = None

    def start(self) -> None:
        """Run the loop on a thread of its own."""
        threading.Thread(target=self.run, name="index-scheduler", daemon=True).start()

    def stop(self) -> None:
        """Ask the loop and its watch to finish."""
        self._stop.set()
        if self._watcher is not None:
            self._watcher.stop()

    def mark(self, project: str) -> None:
        """Record that a watched file of a project changed."""
        with self._lock:
            self._dirty.add(project)

    def run(self) -> None:
        """Clear what a dead process left behind, then tick until stopped.

        Nothing here is fatal. The database can be a moment behind this
        service at start up and can go away later, and a scheduler that ended
        on the first such tick would leave every project unindexed until
        somebody noticed the thread was gone.
        """
        LOG.info(
            "Index scheduler ticking every %d seconds, starting at most %d run(s) "
            "a tick",
            self._tick_seconds,
            SCHEDULER_STARTS_PER_TICK,
        )
        swept = False
        while True:
            try:
                if not swept:
                    self.sweep_orphans()
                    swept = True
                self.tick()
            except Exception:  # noqa: BLE001 - one bad tick must not end the loop
                LOG.exception("Index scheduler tick failed")
            if self._stop.wait(self._tick_seconds):
                return

    def sweep_orphans(self) -> None:
        """Close the runs an earlier process was killed in the middle of."""
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                orphaned = indexjobs.fail_orphaned(cursor, datetime.now(UTC))
            conn.commit()
        finally:
            conn.close()
        for job_id, project in orphaned:
            LOG.warning(
                "Index job %d of %s was left running by an earlier process; "
                "closed as failed",
                job_id,
                project,
            )

    def tick(self) -> None:
        """Resolve every project, rebuild the watch, and start what is owed."""
        now = datetime.now(UTC)
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                targets, owed = self._plan(cursor, now)
            conn.commit()
            self._watch(targets)
            for project, root_path, reason in owed[:SCHEDULER_STARTS_PER_TICK]:
                self._begin(conn, project, root_path, reason)
        finally:
            conn.close()

    def _plan(
        self, cursor: Cursor, now: datetime
    ) -> tuple[dict[str, Target], list[tuple[str, str, str]]]:
        """Say what should be watched and which projects are owed a run.

        Ordered by how long each has waited, so a tick that may start one run
        starts the most overdue rather than the alphabetically first.
        """
        aliases: dict[str, list[str]] = defaultdict(list)
        for project, alias, _ in list_all_sources(cursor):
            aliases[project].append(alias)
        targets: dict[str, Target] = {}
        owed: list[tuple[datetime | None, str, str, str]] = []
        for project, root_path in list_indexable_projects(cursor):
            if project not in aliases or not os.path.isdir(project_mount(project)):
                continue
            settled = schedule.for_project(cursor, project, aliases[project])
            targets.update(self._targets(cursor, project, settled.watched))
            with self._lock:
                dirty = project in self._dirty
            last = indexjobs.last_run(cursor, project)
            reason = schedule.due(settled, last, dirty, now)
            if reason is not None:
                owed.append((last, project, root_path, reason))
        owed.sort(key=lambda one: (one[0] is not None, one[0]))
        return targets, [(project, root, why) for _, project, root, why in owed]

    def _targets(
        self, cursor: Cursor, project: str, watched: tuple[str, ...]
    ) -> dict[str, Target]:
        """Return the specs each watched directory of a project is filtered by."""
        found: dict[str, Target] = {}
        for alias in watched:
            mount = source_mount(project, alias)
            if not os.path.isdir(mount):
                continue
            selection = resolve_selection(cursor, project, alias, mount)
            found[mount] = (project, selection.keep, selection.ignore)
        return found

    def _watch(self, targets: dict[str, Target]) -> None:
        """Replace the watch when what it should be watching changed, or died.

        Not on every tick: the specs are re-read each time, and rebuilding a
        watch every 30 seconds would drop the events arriving while it is
        being built. A watch that died is a separate case - it is retried, but
        slowly, because the sweep already covers those projects.
        """
        current = {} if self._watcher is None else self._watcher.targets
        dead = self._watcher is not None and not self._watcher.alive()
        if set(current) == set(targets) and not (dead and self._retry_due()):
            return
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None
        self._built = datetime.now(UTC)
        if not targets:
            LOG.info("Nothing is set to auto; the index watch is off")
            return
        LOG.info(
            "%s %d director%s for changes",
            "Watching again" if dead else "Watching",
            len(targets),
            "y" if len(targets) == 1 else "ies",
        )
        self._watcher = Watcher(targets, self.mark)
        self._watcher.start()

    def _retry_due(self) -> bool:
        """Whether a dead watch has been dead long enough to try again."""
        if self._built is None:
            return True
        return datetime.now(UTC) - self._built >= timedelta(seconds=WATCH_RETRY_SECONDS)

    def _begin(
        self, conn: Connection, project: str, root_path: str, reason: str
    ) -> None:
        """Open a run and hand it to a thread, as `POST /index` does.

        The dirty flag is cleared before the run rather than after: a file
        written while the run is going has not been indexed by it, and the
        project is owed another one.
        """
        with self._lock:
            self._dirty.discard(project)
        try:
            with conn.cursor() as cursor:
                view = indexjobs.open_run(cursor, project, None, fresh=False)
            conn.commit()
        except RuntimeError as refused:
            conn.rollback()
            LOG.info("Not indexing %s: %s", project, refused)
            return
        LOG.info("Indexing %s as job %d (%s)", project, view["id"], reason)
        indexjobs.run_in_background(view["id"], project, root_path, None, False)
