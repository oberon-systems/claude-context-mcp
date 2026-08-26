"""How a project row is registered, and what the summary pass is handed.

Neither of these needs a database: they are statements and reads, so a cursor
that records what it was asked and answers from a queue is enough to pin the
behaviour that matters - which type a re-index writes, and which file nodes
the model is offered.
"""

from __future__ import annotations

from typing import Any

import pytest

from ctxgraph.config import BUILTIN_PROJECT_TYPES
from ctxgraph.storage import ensure_project, list_files_without_llm_summary


class FakeCursor:
    """Record every statement, and answer fetchone from a queue."""

    def __init__(self, rows: list[tuple[Any, ...] | None]) -> None:
        """Queue the rows the two lookups are to be answered with."""
        self.rows = list(rows)
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        """Record a statement instead of sending it anywhere."""
        self.calls.append((sql, params))

    def fetchone(self) -> tuple[Any, ...] | None:
        """Answer with the next queued row, or nothing once they run out."""
        return self.rows.pop(0) if self.rows else None

    def fetchall(self) -> list[tuple[Any, ...] | None]:
        """Answer with every row still queued, the way a SELECT is read."""
        rows, self.rows = self.rows, []
        return rows


def upsert_params(cursor: FakeCursor) -> tuple[Any, ...]:
    """Read back the parameters of the INSERT, which is always the last one."""
    return cursor.calls[-1][1]


def test_a_first_index_defaults_to_codebase() -> None:
    """A project registered without a type is a codebase."""
    cursor = FakeCursor([None, None])
    ensure_project(cursor, "kurum", "/src/kurum")
    assert upsert_params(cursor) == ("kurum", "/src/kurum", None, "codebase", None)


def test_a_named_type_is_written() -> None:
    """TYPE= on the command line reaches the statement."""
    cursor = FakeCursor([None, None])
    ensure_project(cursor, "kurum", "/src/kurum", "docs")
    assert upsert_params(cursor) == ("kurum", "/src/kurum", "docs", "codebase", "docs")


def test_a_plain_reindex_leaves_the_stored_type_alone() -> None:
    """None on both sides is what makes the COALESCE keep what is stored."""
    cursor = FakeCursor([("/src/kurum", "docs"), None])
    ensure_project(cursor, "kurum", "/src/kurum")
    assert upsert_params(cursor)[2] is None
    assert upsert_params(cursor)[4] is None


@pytest.mark.parametrize("project_type", sorted(BUILTIN_PROJECT_TYPES))
def test_refuses_to_index_into_a_builtin_project(project_type: str) -> None:
    """There is no tree behind one of these; a run would prune it empty."""
    root = f"{project_type}://agent"
    cursor = FakeCursor([(root, project_type)])
    with pytest.raises(RuntimeError, match=f"agent {project_type}"):
        ensure_project(cursor, f"_{project_type}", root)


def test_still_refuses_a_name_pointing_at_another_path() -> None:
    """The type check must not have displaced the check that was there."""
    cursor = FakeCursor([("/src/other", "codebase")])
    with pytest.raises(RuntimeError, match="already indexed from"):
        ensure_project(cursor, "kurum", "/src/kurum")


def test_files_without_a_summary_come_back_as_paths() -> None:
    """The pass reads files from the mount, so only the path is needed."""
    cursor = FakeCursor([("src/app.py",), ("README.md",)])
    assert list_files_without_llm_summary(cursor, "kurum") == [
        "src/app.py",
        "README.md",
    ]


def test_refresh_widens_the_selection_to_what_the_model_wrote() -> None:
    """The flag reaches the statement; the CASE in it does the widening."""
    cursor = FakeCursor([])
    list_files_without_llm_summary(cursor, "kurum", True)
    assert cursor.calls[-1][1] == ("kurum", True)
