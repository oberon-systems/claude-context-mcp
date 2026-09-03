"""The worker API: who may call it, and what it does with what it is told.

No database here, the way test_summarizer fakes one: the queue functions are
monkeypatched at their `ctxgraph.workerapi` binding and the cursor is a mock.
What is worth testing is the boundary - the token, and the refusal to trust a
worker's answer or its expired lease.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from ctxgraph import workerapi

TOKEN = "0123456789abcdef0123456789abcdef"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
LEASE = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Build an app with a token set and no database behind it."""
    monkeypatch.setattr(workerapi, "WORKER_API_TOKEN", TOKEN)
    # The suite is not the service: an app built here must not start indexing
    # trees of its own on a thread nothing in the test is waiting for.
    monkeypatch.setattr(workerapi, "SCHEDULER_ENABLED", False)
    cursor = MagicMock()

    @contextmanager
    def transaction() -> Iterator[MagicMock]:
        yield cursor

    monkeypatch.setattr(workerapi, "transaction", transaction)
    app = workerapi.create_app()
    with TestClient(app) as testing:
        testing.cursor = cursor
        yield testing


def test_health_needs_no_token(client: TestClient) -> None:
    """The compose healthcheck cannot carry one."""
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize(
    "header",
    [None, "", "Bearer ", "Basic " + TOKEN, "Bearer wrong", "Bearer parole"],
)
def test_a_bad_header_is_refused(client: TestClient, header: str | None) -> None:
    """Including a non-ASCII one, which compare_digest would raise on."""
    headers = {} if header is None else {"Authorization": header}
    assert client.get("/jobs", headers=headers).status_code == 401


def test_the_docs_are_not_published(client: TestClient) -> None:
    """FastAPI cannot put them behind the token, so they stay off."""
    assert client.get("/docs").status_code == 404


def test_no_token_configured_refuses_to_serve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This service is published to the network and serves file text."""
    monkeypatch.setattr(workerapi, "WORKER_API_TOKEN", "")
    with pytest.raises(RuntimeError, match="WORKER_API_TOKEN"):
        workerapi.create_app()

    monkeypatch.setattr(workerapi, "WORKER_API_TOKEN", "short")
    with pytest.raises(RuntimeError, match="WORKER_API_TOKEN"):
        workerapi.create_app()


def test_an_oversize_reply_is_refused_before_it_is_shaped(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker cannot stuff a megabyte into the summary cache."""
    locked = MagicMock()
    monkeypatch.setattr(workerapi.jobs, "lock_leased_task", locked)
    answer = client.post(
        "/tasks/1/result",
        headers=AUTH,
        json={
            "worker_id": "w",
            "lease_token": LEASE,
            "summary": "x" * (workerapi.WORKER_MAX_REPLY_CHARS + 1),
        },
    )
    assert answer.status_code == 413
    locked.assert_not_called()


def test_a_result_on_an_expired_lease_touches_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The task has already been given to someone else."""
    saved = MagicMock()
    monkeypatch.setattr(workerapi.jobs, "lock_leased_task", lambda *_: None)
    monkeypatch.setattr(workerapi, "save_llm_summary", saved)
    monkeypatch.setattr(workerapi, "put_cached_summary", saved)
    answer = client.post(
        "/tasks/1/result",
        headers=AUTH,
        json={"worker_id": "w", "lease_token": LEASE, "summary": "Runs the thing."},
    )
    assert answer.status_code == 409
    saved.assert_not_called()


def test_an_answer_that_says_nothing_is_cached_but_not_applied(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The files a small model is worst at must not be re-asked every pass."""
    monkeypatch.setattr(
        workerapi.jobs,
        "lock_leased_task",
        lambda *_: {
            "task_id": 1,
            "job_id": 1,
            "file_path": "CHANGELOG.md",
            "content_hash": "a" * 64,
            "attempts": 1,
            "project": "demo",
            "job_status": "running",
        },
    )
    cached = MagicMock()
    saved = MagicMock(return_value=True)
    monkeypatch.setattr(workerapi, "put_cached_summary", cached)
    monkeypatch.setattr(workerapi, "save_llm_summary", saved)
    monkeypatch.setattr(workerapi.jobs, "finish_task", MagicMock())
    monkeypatch.setattr(workerapi.jobs, "finish_job_if_drained", MagicMock())
    monkeypatch.setattr(workerapi.jobs, "job_row", lambda *_: {"status": "running"})

    answer = client.post(
        "/tasks/1/result",
        headers=AUTH,
        json={"worker_id": "w", "lease_token": LEASE, "summary": "CHANGELOG.md"},
    )
    body = answer.json()
    assert answer.status_code == 200
    assert body["applied"] is False
    assert body["reason"] == workerapi.NOT_USEFUL
    cached.assert_called_once()
    saved.assert_not_called()


def test_a_manual_summary_is_never_overwritten(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """save_llm_summary refuses it, and the answer says so rather than lying."""
    monkeypatch.setattr(
        workerapi.jobs,
        "lock_leased_task",
        lambda *_: {
            "task_id": 1,
            "job_id": 1,
            "file_path": "app.py",
            "content_hash": "a" * 64,
            "attempts": 1,
            "project": "demo",
            "job_status": "running",
        },
    )
    monkeypatch.setattr(workerapi, "put_cached_summary", MagicMock())
    monkeypatch.setattr(workerapi, "save_llm_summary", MagicMock(return_value=False))
    monkeypatch.setattr(workerapi.jobs, "finish_task", MagicMock())
    monkeypatch.setattr(workerapi.jobs, "finish_job_if_drained", MagicMock())
    monkeypatch.setattr(workerapi.jobs, "job_row", lambda *_: {"status": "running"})

    body = client.post(
        "/tasks/1/result",
        headers=AUTH,
        json={
            "worker_id": "w",
            "lease_token": LEASE,
            "summary": "Serves the application over HTTP.",
        },
    ).json()
    assert body["applied"] is False
    assert "manual" in body["reason"]


def test_the_sources_of_a_project_are_listed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each directory says whether this container actually has it mounted.

    Nothing is mounted at /code here, which is what makes the flag false
    without patching the filesystem out from under the test client.
    """
    monkeypatch.setattr(
        workerapi,
        "list_sources",
        lambda cursor, project: [("configs", "/mono/configs")],
    )
    body = client.get("/projects/mono/sources", headers=AUTH).json()
    assert body["sources"] == [
        {"alias": "configs", "root_path": "/mono/configs", "mounted": False}
    ]


def test_adding_a_directory_says_the_host_has_to_mount_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing here can write the compose override or recreate a service."""
    added: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        workerapi,
        "add_source",
        lambda cursor, project, alias, root: added.append((project, alias, root)),
    )
    monkeypatch.setattr(workerapi, "list_sources", lambda cursor, project: [])
    response = client.post(
        "/projects/mono/sources",
        headers=AUTH,
        json={"root_path": "/mono/tools/agents/", "alias": ""},
    )
    assert response.status_code == 201
    assert added == [("mono", "agents", "/mono/tools/agents")]
    assert "make mounts" in response.json()["mounts"]


def test_a_refused_directory_answers_with_the_reason(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rule that refuses it is in storage; the reason travels unchanged."""

    def refuse(cursor: object, project: str, alias: str, root: str) -> None:
        raise RuntimeError("already a source of project 'other'")

    monkeypatch.setattr(workerapi, "add_source", refuse)
    response = client.post(
        "/projects/mono/sources",
        headers=AUTH,
        json={"root_path": "/mono/tools/agents", "alias": "agents"},
    )
    assert response.status_code == 409
    assert "project 'other'" in response.json()["detail"]


def test_a_project_can_be_registered_before_it_reads_anything(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The monorepo case: the row and the address exist, the slices follow."""
    registered: list[tuple[str, str, str | None, str, bool]] = []

    def record(
        cursor: object,
        project: str,
        root_path: str,
        project_type: str | None = None,
        alias: str = "",
        with_source: bool = True,
    ) -> None:
        registered.append((project, root_path, project_type, alias, with_source))

    monkeypatch.setattr(workerapi, "register_project", record)
    monkeypatch.setattr(workerapi, "list_sources", lambda cursor, project: [])
    response = client.post(
        "/projects", headers=AUTH, json={"name": "Mono Repo", "project_type": "docs"}
    )
    assert response.status_code == 201
    # The name is cleaned by the rule that names every project, and the row
    # still needs a root_path the column will accept.
    assert registered == [("mono-repo", "registered://mono-repo", "docs", "", False)]
    assert "make mounts" in response.json()["mounts"]


def test_a_reserved_project_name_is_refused_rather_than_crashing(
    client: TestClient,
) -> None:
    """`_settings` and its siblings hold records, not a tree."""
    response = client.post("/projects", headers=AUTH, json={"name": "_settings"})
    assert response.status_code == 409
    assert "reserved" in response.json()["detail"]


def test_registering_a_directory_registers_it_as_the_whole_tree(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project given a path reads it whole, so its node ids stay unprefixed."""
    registered: list[tuple[str, str, str | None, str, bool]] = []

    def record(
        cursor: object,
        project: str,
        root_path: str,
        project_type: str | None = None,
        alias: str = "",
        with_source: bool = True,
    ) -> None:
        registered.append((project, root_path, project_type, alias, with_source))

    monkeypatch.setattr(workerapi, "register_project", record)
    monkeypatch.setattr(workerapi, "list_sources", lambda cursor, project: [])
    response = client.post(
        "/projects", headers=AUTH, json={"name": "", "root_path": "/src/kurum/"}
    )
    assert response.status_code == 201
    assert registered == [("kurum", "/src/kurum", None, "", True)]


def test_scanning_a_directory_that_is_not_mounted_is_refused(
    client: TestClient,
) -> None:
    """Nothing is mounted at /code here, and a scan reads the tree."""
    response = client.post("/projects/mono/scan", headers=AUTH, json={"alias": "web"})
    assert response.status_code == 409
    assert "recreated before it can be scanned" in response.json()["detail"]


def test_the_settings_of_an_unmounted_source_report_only_that(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Where a selection comes from cannot be answered without the tree."""
    monkeypatch.setattr(
        workerapi,
        "list_sources",
        lambda cursor, project: [("configs", "/mono/configs")],
    )
    body = client.get("/projects/mono/settings", headers=AUTH).json()
    assert body["sources"] == [{"alias": "configs", "mounted": False}]


def test_a_project_already_indexing_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard `open_run` holds is the answer the dashboard already knew."""

    def refuse(
        cursor: object, project: str, project_type: str | None, fresh: bool
    ) -> None:
        raise RuntimeError("job 7 is already indexing this project")

    monkeypatch.setattr(workerapi.indexjobs, "open_run", refuse)
    answer = client.post("/index", json={"project": "kurum"}, headers=AUTH)
    assert answer.status_code == 409
    assert answer.json()["detail"] == "job 7 is already indexing this project"


def test_an_accepted_run_is_handed_to_a_thread(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The row is answered with, and the work starts after the transaction."""
    started: list[tuple] = []
    monkeypatch.setattr(
        workerapi.indexjobs,
        "open_run",
        lambda cursor, project, project_type, fresh: {"id": 11, "project": project},
    )
    monkeypatch.setattr(
        workerapi.indexjobs,
        "run_in_background",
        lambda *args: started.append(args),
    )
    answer = client.post(
        "/index", json={"project": "kurum", "root_path": "/src/kurum"}, headers=AUTH
    )
    assert answer.status_code == 202
    assert answer.json()["id"] == 11
    assert started == [(11, "kurum", "/src/kurum", None, False)]


def test_the_schedule_of_a_project_is_folded_before_it_is_answered(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One directory in auto decides the project, and says which is watched."""
    monkeypatch.setattr(
        workerapi,
        "list_sources",
        lambda cursor, project: [("services", "/mono/services"), ("vendor", "/mono/v")],
    )
    monkeypatch.setattr(
        workerapi.schedule,
        "resolve",
        lambda cursor, project, alias: workerapi.schedule.Schedule(
            "auto" if alias == "services" else "off", 60, 5, {}
        ),
    )
    monkeypatch.setattr(workerapi.indexjobs, "last_run", lambda cursor, project: None)
    body = client.get("/projects/mono/schedule", headers=AUTH).json()
    assert body["mode"] == "auto"
    assert body["watched"] == ["services"]
    assert body["next_run"] is None
