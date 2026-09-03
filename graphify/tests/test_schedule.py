"""When a project indexes itself, and what its directories agree on.

The decision is a pure one - three fields resolved over three levels, then
folded, then compared against a clock passed in - so all of it is pinned here
without a database, a mount or a wait.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.test_storage import FakeCursor

from ctxgraph.schedule import Schedule, due, fold, for_project, next_due, resolve

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
GLOBAL = ("_settings", "")


def cursor(**levels: dict) -> FakeCursor:
    """Build a cursor holding one indexing object per level, keyed as rows are."""
    objects = {}
    for key, value in levels.items():
        if key == "global":
            objects[GLOBAL] = {"indexing": value}
        elif key == "project":
            objects[("kurum", "")] = {"indexing": value}
        else:
            objects[("kurum", key)] = {"indexing": value}
    return FakeCursor(objects=objects)


def schedule(mode: str, interval: int = 60, debounce: int = 5) -> Schedule:
    """Build one level's answer, for the fold to work on."""
    return Schedule(mode, interval, debounce, {})


def test_a_project_nobody_configured_is_indexed_by_hand() -> None:
    """No level saying anything is the mode every project had before this."""
    settled = resolve(cursor(), "kurum", "")
    assert settled.mode == "off"
    assert settled.origins == {
        "mode": "default",
        "interval_minutes": "default",
        "debounce_minutes": "default",
    }


def test_the_global_default_reaches_a_project_that_says_nothing() -> None:
    """One row settles every project that has not overridden it."""
    settled = resolve(cursor(**{"global": {"mode": "periodic"}}), "kurum", "")
    assert settled.mode == "periodic"
    assert settled.origins["mode"] == "global"


def test_a_project_overrides_one_field_and_inherits_the_rest() -> None:
    """The fields are resolved apart, as the two selection documents are."""
    settled = resolve(
        cursor(
            **{
                "global": {"mode": "periodic", "interval_minutes": 30},
                "project": {"mode": "auto"},
            }
        ),
        "kurum",
        "",
    )
    assert (settled.mode, settled.interval_minutes) == ("auto", 30)
    assert settled.origins["mode"] == "project"
    assert settled.origins["interval_minutes"] == "global"


def test_a_directory_overrides_the_project_it_belongs_to() -> None:
    """The most specific level wins, the way the selection resolves."""
    settled = resolve(
        cursor(**{"project": {"mode": "periodic"}, "services": {"mode": "auto"}}),
        "kurum",
        "services",
    )
    assert settled.mode == "auto"
    assert settled.origins["mode"] == "directory"


def test_a_project_mounted_whole_reports_its_own_row_as_the_project() -> None:
    """One row is one level: the empty alias is not a directory of its own."""
    settled = resolve(cursor(**{"project": {"mode": "auto"}}), "kurum", "")
    assert settled.origins["mode"] == "project"


def test_a_mode_nobody_implements_is_ignored() -> None:
    """A value written by hand cannot make the scheduler act on nonsense."""
    settled = resolve(cursor(**{"project": {"mode": "whenever"}}), "kurum", "")
    assert settled.mode == "off"
    assert settled.origins["mode"] == "default"


def test_an_interval_of_zero_is_clamped_rather_than_obeyed() -> None:
    """Zero minutes would ask for a run on every tick, and get one."""
    settled = resolve(
        cursor(**{"project": {"mode": "periodic", "interval_minutes": 0}}),
        "kurum",
        "",
    )
    assert settled.interval_minutes == 1


def test_an_interval_that_is_not_a_number_says_nothing() -> None:
    """A string is not a duration, and the level above answers instead."""
    settled = resolve(
        cursor(
            **{
                "global": {"interval_minutes": 45},
                "project": {"interval_minutes": "soon"},
            }
        ),
        "kurum",
        "",
    )
    assert settled.interval_minutes == 45


def test_every_directory_off_is_a_project_off() -> None:
    """Nothing asking for a run means no run."""
    folded = fold({"a": schedule("off"), "b": schedule("off")})
    assert folded.mode == "off"
    assert folded.watched == ()


def test_the_most_eager_directory_decides_the_project() -> None:
    """One directory in auto is enough for the project to be watched."""
    folded = fold({"a": schedule("off"), "b": schedule("auto")})
    assert folded.mode == "auto"


def test_a_directory_that_says_off_is_not_watched() -> None:
    """The point of the level: watch the slice worked in, not the vendored one."""
    folded = fold({"vendor": schedule("off"), "services": schedule("auto")})
    assert folded.watched == ("services",)


def test_the_shortest_interval_of_the_asking_directories_wins() -> None:
    """A project runs as often as its most demanding directory asked."""
    folded = fold(
        {
            "a": schedule("periodic", interval=30, debounce=9),
            "b": schedule("periodic", interval=10, debounce=4),
        }
    )
    assert (folded.interval_minutes, folded.debounce_minutes) == (10, 4)


def test_an_off_directory_does_not_lower_the_interval() -> None:
    """A setting that was turned off must not go on acting."""
    folded = fold(
        {"a": schedule("off", interval=1), "b": schedule("periodic", interval=30)}
    )
    assert folded.interval_minutes == 30


def test_the_fold_reads_the_levels_of_a_project() -> None:
    """for_project resolves each directory before folding them."""
    folded = for_project(
        cursor(**{"project": {"mode": "periodic"}, "services": {"mode": "auto"}}),
        "kurum",
        ["services", "vendor"],
    )
    assert folded.mode == "auto"
    assert folded.watched == ("services",)


def test_nothing_is_ever_owed_while_a_project_is_off() -> None:
    """Off is manual only, however long it has been and whatever changed."""
    folded = fold({"": schedule("off")})
    assert due(folded, None, True, NOW) is None
    assert due(folded, NOW - timedelta(days=30), True, NOW) is None


def test_a_project_never_indexed_is_owed_a_run_at_once() -> None:
    """There is no last run to wait an interval from, and no graph either."""
    folded = fold({"": schedule("periodic")})
    assert due(folded, None, False, NOW) == "periodic"


def test_a_timer_waits_out_its_interval() -> None:
    """The interval is a floor, not a target."""
    folded = fold({"": schedule("periodic", interval=60)})
    assert due(folded, NOW - timedelta(minutes=59), False, NOW) is None
    assert due(folded, NOW - timedelta(minutes=60), False, NOW) == "periodic"


def test_a_change_inside_the_debounce_window_waits() -> None:
    """The throttle is what keeps a busy checkout from indexing continuously."""
    folded = fold({"": schedule("auto", interval=60, debounce=5)})
    assert due(folded, NOW - timedelta(minutes=2), True, NOW) is None
    assert due(folded, NOW - timedelta(minutes=5), True, NOW) == "changed"


def test_a_watched_project_still_indexes_when_nothing_was_noticed() -> None:
    """The sweep behind auto: a blind watch must not mean a stale graph."""
    folded = fold({"": schedule("auto", interval=60, debounce=5)})
    assert due(folded, NOW - timedelta(minutes=30), False, NOW) is None
    assert due(folded, NOW - timedelta(minutes=60), False, NOW) == "fallback"


def test_the_next_sweep_is_an_interval_after_the_last_run() -> None:
    """What the dashboard shows when nothing has changed yet."""
    folded = fold({"": schedule("periodic", interval=15)})
    last = NOW - timedelta(minutes=5)
    assert next_due(folded, last) == last + timedelta(minutes=15)
    assert next_due(fold({"": schedule("off")}), last) is None
