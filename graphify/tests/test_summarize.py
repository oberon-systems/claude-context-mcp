"""The pass over every project: which files reach the model, and how many.

No database and no model, but real trees on disk: the listing query and the
summarizer are replaced while the mounts are actual directories, so what is
left under test is the loop itself - one project after another, the limit
applied to each, and a node whose file is gone left alone.
"""

from __future__ import annotations

import pathlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import pytest

from ctxgraph import summarize


class FakeConnection:
    """A connection whose cursors do nothing, the query being patched out."""

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


Pending = dict[str, list[tuple[str, str | None]]]


@pytest.fixture
def trees(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[[Pending], None]:
    """Build the mounts as real directories and answer the listing from them.

    Real files rather than a mocked read: reading the tree is the whole point
    of the mount, and a text of None is a node whose file is gone.
    """

    def build(pending: Pending) -> None:
        for project, files in pending.items():
            root = tmp_path / project
            root.mkdir(parents=True, exist_ok=True)
            for rel_path, text in files:
                if text is None:
                    continue
                path = root / rel_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
        monkeypatch.setattr(
            summarize, "project_mount", lambda project: str(tmp_path / project)
        )
        monkeypatch.setattr(
            summarize,
            "list_projects",
            lambda cursor: [(name, f"/src/{name}", "codebase", 1) for name in pending],
        )
        monkeypatch.setattr(
            summarize,
            "list_files_without_llm_summary",
            lambda cursor, project, refresh=False: [
                rel_path for rel_path, _ in pending[project]
            ],
        )

    return build


def test_every_project_is_described(
    trees: Callable[[Pending], None], stack: tuple[FakeConnection, FakeSummarizer]
) -> None:
    """A bare pass walks the projects table rather than one mounted tree."""
    conn, summarizer = stack
    trees(
        {
            "kurum": [("app.py", "import os")],
            "oberon": [("main.go", "package main")],
        }
    )
    summarize.summarize_projects()
    assert summarizer.seen == [("kurum", "app.py"), ("oberon", "main.go")]
    assert conn.commits == 2
    assert summarizer.closed


def test_the_limit_is_spent_on_each_project(
    monkeypatch: pytest.MonkeyPatch,
    trees: Callable[[Pending], None],
    stack: tuple[FakeConnection, FakeSummarizer],
) -> None:
    """A budget for the whole run would be spent entirely on the first project."""
    _, summarizer = stack
    monkeypatch.setattr(summarize, "SUMMARY_LIMIT", 1)
    trees(
        {
            "kurum": [("app.py", "import os"), ("util.py", "import sys")],
            "oberon": [("main.go", "package main"), ("go.mod", "module oberon")],
        }
    )
    summarize.summarize_projects()
    assert summarizer.seen == [("kurum", "app.py"), ("oberon", "main.go")]


def test_a_node_whose_file_is_gone_never_reaches_the_model(
    trees: Callable[[Pending], None], stack: tuple[FakeConnection, FakeSummarizer]
) -> None:
    """The graph is ahead of the tree, and the count is what says so."""
    conn, summarizer = stack
    trees({"kurum": [("deleted.py", None), ("app.py", "import os")]})
    summarize.summarize_projects()
    assert summarizer.seen == [("kurum", "app.py")]
    assert conn.commits == 1


def test_an_unmounted_project_is_skipped(
    trees: Callable[[Pending], None], stack: tuple[FakeConnection, FakeSummarizer]
) -> None:
    """A project with no mount is reported rather than read as an empty tree."""
    _, summarizer = stack
    trees({"kurum": [("app.py", "import os")]})
    summarize.summarize_projects(["kurum", "never-mounted"])
    assert summarizer.seen == [("kurum", "app.py")]


def test_a_named_project_skips_the_listing(
    monkeypatch: pytest.MonkeyPatch,
    trees: Callable[[Pending], None],
    stack: tuple[FakeConnection, FakeSummarizer],
) -> None:
    """--project names one project without walking the projects table."""
    _, summarizer = stack
    trees({"kurum": [("app.py", "import os")]})
    monkeypatch.setattr(
        summarize, "list_projects", lambda cursor: pytest.fail("listed anyway")
    )
    summarize.summarize_projects(["kurum"])
    assert summarizer.seen == [("kurum", "app.py")]


def test_an_empty_database_loads_no_model(
    trees: Callable[[Pending], None], stack: tuple[FakeConnection, FakeSummarizer]
) -> None:
    """A gigabyte of weights is not read to describe nothing."""
    _, summarizer = stack
    trees({})
    summarize.summarize_projects()
    assert summarizer.seen == []
    assert not summarizer.closed
