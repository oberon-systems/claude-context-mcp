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
