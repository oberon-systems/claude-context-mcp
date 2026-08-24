"""Which projects a run visits, and what happens when one of them is gone."""

from __future__ import annotations

import argparse
from typing import Any

import pytest
from ctxworker import __main__ as entry
from ctxworker.client import ApiError


def listing(name: str, pending: int = 0, running: int | None = None) -> dict[str, Any]:
    """One row of what the API answers to /projects."""
    return {"name": name, "without_llm_summary": pending, "running_job": running}


class FakeClient:
    """Answer /projects from a list, and record every job opened."""

    def __init__(
        self, projects: list[dict[str, Any]], gone: set[str] | None = None
    ) -> None:
        """Queue what the API lists, and which projects have since gone."""
        self.projects_listed = projects
        self.gone = gone or set()
        self.opened: list[str] = []

    def projects(self) -> list[dict[str, Any]]:
        """List what the stack has indexed."""
        return list(self.projects_listed)

    def create_job(self, project: str, refresh: bool = False) -> dict[str, Any]:
        """Open a job, unless the project is one that has been unindexed."""
        if project in self.gone:
            raise ApiError(404, "unknown project")
        self.opened.append(project)
        return {"id": len(self.opened), "project": project}


def arguments(**over: bool) -> argparse.Namespace:
    """Build the handful of fields the project loop reads."""
    return argparse.Namespace(**{"refresh": False, "once": False, **over})


def test_a_project_with_files_left_is_visited() -> None:
    """The plain case: something to describe, so it is on the list."""
    client = FakeClient([listing("kurum", pending=12)])
    assert entry.select_projects(client) == ["kurum"]


def test_an_open_job_is_joined_with_nothing_pending() -> None:
    """A second worker on one job is what the lease queue is for."""
    client = FakeClient([listing("kurum", running=7)])
    assert entry.select_projects(client) == ["kurum"]


def test_a_described_project_is_left_out() -> None:
    """Nothing to describe and no job open means nothing to do."""
    client = FakeClient([listing("kurum"), listing("oberon", pending=3)])
    assert entry.select_projects(client) == ["oberon"]


def test_a_project_that_has_gone_does_not_stop_the_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One project unindexed since the listing costs only that project."""
    monkeypatch.setattr(entry, "run_job", lambda *_: 1)
    client = FakeClient([], gone={"kurum"})
    described, visited = entry.run_projects(
        client, None, "worker-1", arguments(), ["kurum", "oberon"]
    )
    assert client.opened == ["oberon"]
    assert (described, visited) == (1, 1)


def test_once_stops_after_the_first_project(monkeypatch: pytest.MonkeyPatch) -> None:
    """--once means one batch, so the pass ends with the job it was claimed from."""
    monkeypatch.setattr(entry, "run_job", lambda *_: 4)
    client = FakeClient([])
    described, visited = entry.run_projects(
        client, None, "worker-1", arguments(once=True), ["kurum", "oberon"]
    )
    assert client.opened == ["kurum"]
    assert (described, visited) == (4, 1)


def test_a_bare_run_asks_for_no_project(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing named and nothing in the environment is the auto case."""
    monkeypatch.delenv("WORKER_PROJECT", raising=False)
    monkeypatch.delenv("WORKER_AUTO", raising=False)
    monkeypatch.setattr("sys.argv", ["ctxworker"])
    args = entry.parse_args()
    assert args.project == ""
    assert args.auto is False


def test_the_environment_can_ask_for_every_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WORKER_AUTO=1 overrides a WORKER_PROJECT the machine already carries."""
    monkeypatch.setenv("WORKER_PROJECT", "kurum")
    monkeypatch.setenv("WORKER_AUTO", "1")
    monkeypatch.setattr("sys.argv", ["ctxworker"])
    args = entry.parse_args()
    assert args.project == "kurum"
    assert args.auto is True
