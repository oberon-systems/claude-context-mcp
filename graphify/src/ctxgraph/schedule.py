"""When a project is indexed without being asked, and where that was decided.

The schedule lives beside the selection, in `project_settings.settings`, and is
resolved the same way: the directory, then the project, then the global
default, most specific first and one field at a time. A project that says
nothing is indexed by hand, exactly as every project was before this existed.

A run always covers a whole project - the walk prunes every node it did not
find, and the code extractor resolves cross-file edges in one pass - so the
directories of a project fold into a single decision: the most eager of them
wins, and a directory that says `off` is not watched. That is the one thing a
directory level buys, and it is worth the fold: in a monorepo it watches the
slice being worked on and leaves the vendored slice that churns alone.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from psycopg2.extensions import cursor as Cursor

from ctxgraph.config import (
    DEFAULT_INDEX_DEBOUNCE_MINUTES,
    DEFAULT_INDEX_INTERVAL_MINUTES,
    DEFAULT_INDEXING_MODE,
    INDEXING_KEY,
    INDEXING_MODES,
    MAX_INDEX_DEBOUNCE_MINUTES,
    MAX_INDEX_INTERVAL_MINUTES,
    MIN_INDEX_DEBOUNCE_MINUTES,
    MIN_INDEX_INTERVAL_MINUTES,
)
from ctxgraph.selection import Origin, levels
from ctxgraph.storage import read_settings_json

LOG = logging.getLogger(__name__)

INTERVAL = "interval_minutes"
DEBOUNCE = "debounce_minutes"
MODE = "mode"
FIELDS = (MODE, INTERVAL, DEBOUNCE)

DEFAULTS: dict[str, str | int] = {
    MODE: DEFAULT_INDEXING_MODE,
    INTERVAL: DEFAULT_INDEX_INTERVAL_MINUTES,
    DEBOUNCE: DEFAULT_INDEX_DEBOUNCE_MINUTES,
}
BOUNDS: dict[str, tuple[int, int]] = {
    INTERVAL: (MIN_INDEX_INTERVAL_MINUTES, MAX_INDEX_INTERVAL_MINUTES),
    DEBOUNCE: (MIN_INDEX_DEBOUNCE_MINUTES, MAX_INDEX_DEBOUNCE_MINUTES),
}
# How eager each mode is. The fold takes the largest, so a project reading
# several directories runs as often as its most demanding one asks.
EAGERNESS = {mode: rank for rank, mode in enumerate(INDEXING_MODES)}


@dataclass(frozen=True)
class Schedule:
    """One level's answer, and where each half of it came from."""

    mode: str
    interval_minutes: int
    debounce_minutes: int
    origins: dict[str, Origin]


@dataclass(frozen=True)
class ProjectSchedule:
    """What a project as a whole does, folded from its directories."""

    mode: str
    interval_minutes: int
    debounce_minutes: int
    watched: tuple[str, ...]
    per_alias: dict[str, Schedule]


def clean_mode(value: object) -> str | None:
    """Return a stored mode fit to act on, or None when it names none."""
    if isinstance(value, str) and value in INDEXING_MODES:
        return value
    LOG.warning("indexing mode %r is not one of %s", value, list(INDEXING_MODES))
    return None


def clean_minutes(field: str, value: object) -> int | None:
    """Return a stored duration clamped into its bounds, or None if it is not one.

    These rows are editable in psql as well as in the dashboard, so a number is
    clamped and reported rather than trusted: an interval of zero would ask for
    a run on every tick, and the scheduler would give it one.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        LOG.warning("indexing %s is %r, which is not a whole number", field, value)
        return None
    low, high = BOUNDS[field]
    clamped = max(low, min(high, value))
    if clamped != value:
        LOG.warning("indexing %s of %d clamped to %d", field, value, clamped)
    return clamped


def clean(field: str, value: object) -> str | int | None:
    """Return a stored value fit to act on, whichever field it belongs to."""
    if field == MODE:
        return clean_mode(value)
    return clean_minutes(field, value)


def resolve(cursor: Cursor, project: str, alias: str) -> Schedule:
    """Settle one directory's schedule, and say where each field came from.

    Every field is resolved on its own, as the two selection documents are: a
    project may set `auto` while the interval behind its fallback sweep is
    still the global one.
    """
    found: dict[str, tuple[Origin, str | int]] = {}
    for origin, name, key in levels(project, alias):
        stored = read_settings_json(cursor, name, key).get(INDEXING_KEY)
        if not isinstance(stored, dict):
            continue
        for field in FIELDS:
            if field in found or stored.get(field) is None:
                continue
            value = clean(field, stored[field])
            if value is not None:
                found[field] = (origin, value)
    settled = {
        field: found.get(field, ("default", DEFAULTS[field])) for field in FIELDS
    }
    return Schedule(
        mode=settled[MODE][1],
        interval_minutes=settled[INTERVAL][1],
        debounce_minutes=settled[DEBOUNCE][1],
        origins={field: settled[field][0] for field in FIELDS},
    )


def fold(per_alias: dict[str, Schedule]) -> ProjectSchedule:
    """Reduce the directories of a project to the one run they share.

    The most eager mode wins, and the numbers come from the directories that
    asked to be indexed at all: an `off` directory has an interval, but it is
    not asking for anything, and letting it lower the project's would be a
    setting acting after being turned off.
    """
    active = [one for one in per_alias.values() if one.mode != "off"]
    if not active:
        return ProjectSchedule(
            mode="off",
            interval_minutes=DEFAULTS[INTERVAL],
            debounce_minutes=DEFAULTS[DEBOUNCE],
            watched=(),
            per_alias=per_alias,
        )
    return ProjectSchedule(
        mode=max((one.mode for one in active), key=lambda mode: EAGERNESS[mode]),
        interval_minutes=min(one.interval_minutes for one in active),
        debounce_minutes=min(one.debounce_minutes for one in active),
        watched=tuple(alias for alias, one in per_alias.items() if one.mode == "auto"),
        per_alias=per_alias,
    )


def for_project(cursor: Cursor, project: str, aliases: list[str]) -> ProjectSchedule:
    """Resolve every directory of a project, then fold them into one schedule."""
    return fold({alias: resolve(cursor, project, alias) for alias in aliases})


def due(
    schedule: ProjectSchedule,
    last_run: datetime | None,
    dirty: bool,
    now: datetime,
) -> str | None:
    """Say why a run is owed, or None when none is.

    A project never indexed is owed one as soon as it is not `off`: there is no
    last run to wait an interval from, and the graph is empty until it happens.
    """
    if schedule.mode == "off":
        return None
    since = None if last_run is None else now - last_run
    if schedule.mode == "auto" and dirty:
        if since is None or since >= timedelta(minutes=schedule.debounce_minutes):
            return "changed"
    if since is None or since >= timedelta(minutes=schedule.interval_minutes):
        # In `auto` this is the sweep that keeps a project indexed when the
        # watch is blind: a tree on a filesystem inotify says nothing about,
        # or a watch the kernel refused for want of `max_user_watches`.
        return "periodic" if schedule.mode == "periodic" else "fallback"
    return None


def next_due(schedule: ProjectSchedule, last_run: datetime | None) -> datetime | None:
    """When the sweep would next start a run, ignoring any change to come."""
    if schedule.mode == "off":
        return None
    if last_run is None:
        return None
    return last_run + timedelta(minutes=schedule.interval_minutes)
