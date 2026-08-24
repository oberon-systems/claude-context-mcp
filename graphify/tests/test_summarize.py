"""The pass over every project: which files reach the model, and how many.

No database and no model: the two queries and the summarizer are replaced, so
what is left under test is the loop itself - one project after another, the
limit applied to each, and the files nothing was stored for left alone.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from ctxgraph import summarize


class FakeConnection:
    """A connection whose cursors do nothing, both queries being patched out."""

    def __init__(self) -> None:
        """Count the commits, which is what makes the pass resumable."""
        self.commits = 0

    @contextmanager
    def cursor(self) -> Iterator[None]:
        """Hand out a cursor nothing is asked of."""
        yield None

    def commit(self) -> None:
        """Record one file's worth of work being made durable."""
        self.commits += 1

    def rollback(self) -> None:
        """Nothing was written, so nothing is undone."""

    def close(self) -> None:
        """Nothing was opened."""


class FakeSummarizer:
    """Record every file it is asked about, and describe all of them."""

    def __init__(self) -> None:
        """Start with nothing seen and the model never loaded."""
        self.seen: list[tuple[str, str]] = []
        self.closed = False

    def refine(self, cursor: None, project: str, rel_path: str, text: str) -> bool:
        """Answer as the real one does, and remember what was asked."""
        self.seen.append((project, rel_path))
        return True

    def report(self) -> str:
        """Report as the real one does; the contents do not matter here."""
        return "0 generated"

    def close(self) -> None:
        """Release the weights that were never loaded."""
        self.closed = True


@pytest.fixture
def stack(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeConnection, FakeSummarizer]:
    """Put a fake connection and a fake model in front of the pass."""
    conn = FakeConnection()
    summarizer = FakeSummarizer()
    monkeypatch.setattr(summarize, "get_db_connection", lambda: conn)
    monkeypatch.setattr(summarize, "Summarizer", lambda *_, **__: summarizer)
    monkeypatch.setattr(summarize, "SUMMARY_LIMIT", 0)
    return conn, summarizer


def with_projects(
    monkeypatch: pytest.MonkeyPatch, pending: dict[str, list[tuple[str, str]]]
) -> None:
    """Answer both queries from a table of project name to file rows."""
    monkeypatch.setattr(
        summarize,
        "list_projects",
        lambda cursor: [(name, f"/src/{name}", "codebase", 1) for name in pending],
    )
    monkeypatch.setattr(
        summarize,
        "list_pending_summaries",
        lambda cursor, project, refresh=False: pending[project],
    )


def test_every_project_is_described(
    monkeypatch: pytest.MonkeyPatch, stack: tuple[FakeConnection, FakeSummarizer]
) -> None:
    """A bare pass walks the projects table rather than one mounted tree."""
    conn, summarizer = stack
    with_projects(
        monkeypatch,
        {
            "kurum": [("app.py", "import os")],
            "oberon": [("main.go", "package main")],
        },
    )
    summarize.summarize_stored()
    assert summarizer.seen == [("kurum", "app.py"), ("oberon", "main.go")]
    assert conn.commits == 2
    assert summarizer.closed


def test_the_limit_is_spent_on_each_project(
    monkeypatch: pytest.MonkeyPatch, stack: tuple[FakeConnection, FakeSummarizer]
) -> None:
    """A budget for the whole run would be spent entirely on the first project."""
    _, summarizer = stack
    monkeypatch.setattr(summarize, "SUMMARY_LIMIT", 1)
    with_projects(
        monkeypatch,
        {
            "kurum": [("app.py", "import os"), ("util.py", "import sys")],
            "oberon": [("main.go", "package main"), ("go.mod", "module oberon")],
        },
    )
    summarize.summarize_stored()
    assert summarizer.seen == [("kurum", "app.py"), ("oberon", "main.go")]


def test_a_file_with_no_stored_text_never_reaches_the_model(
    monkeypatch: pytest.MonkeyPatch, stack: tuple[FakeConnection, FakeSummarizer]
) -> None:
    """Nothing to describe, and the count is what names the project to re-index."""
    conn, summarizer = stack
    with_projects(monkeypatch, {"kurum": [(".env", ""), ("app.py", "import os")]})
    summarize.summarize_stored()
    assert summarizer.seen == [("kurum", "app.py")]
    assert conn.commits == 1


def test_a_named_project_skips_the_listing(
    monkeypatch: pytest.MonkeyPatch, stack: tuple[FakeConnection, FakeSummarizer]
) -> None:
    """--project names one project without the tree having to be mounted."""
    _, summarizer = stack
    with_projects(monkeypatch, {"kurum": [("app.py", "import os")]})
    monkeypatch.setattr(
        summarize, "list_projects", lambda cursor: pytest.fail("listed anyway")
    )
    summarize.summarize_stored(["kurum"])
    assert summarizer.seen == [("kurum", "app.py")]


def test_an_empty_database_loads_no_model(
    monkeypatch: pytest.MonkeyPatch, stack: tuple[FakeConnection, FakeSummarizer]
) -> None:
    """A gigabyte of weights is not read to describe nothing."""
    _, summarizer = stack
    with_projects(monkeypatch, {})
    summarize.summarize_stored()
    assert summarizer.seen == []
    assert not summarizer.closed
