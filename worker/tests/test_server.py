"""The llama.cpp server backend, tested without a server or a model."""

from __future__ import annotations

import urllib.error
from collections.abc import Callable
from typing import Any

import pytest
from ctxworker.client import ApiError
from ctxworker.server import ServerRunner

PROPS = {
    "default_generation_settings": {"n_ctx": 8192},
    "model_path": "C:\\models\\qwen.gguf",
    "total_slots": 1,
}


def answering(
    replies: dict[str, Any], seen: list[Any]
) -> Callable[..., dict[str, Any]]:
    """Stand in for one server, recording what it was asked."""

    def call(
        self: ServerRunner, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        seen.append((path, body))
        answer = replies[path]
        if isinstance(answer, Exception):
            raise answer
        return answer

    return call


def test_the_window_comes_from_the_server_not_the_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard has to ask the process that actually holds the context."""
    monkeypatch.setattr(ServerRunner, "call", answering({"/props": PROPS}, []))
    runner = ServerRunner("http://127.0.0.1:8080/")

    assert runner.n_ctx == 8192
    assert runner.base_url == "http://127.0.0.1:8080"
    assert runner.fits(16000, 64)
    assert not runner.fits(600000, 64)


def test_an_unnamed_window_refuses_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A server that does not report n_ctx must not block the whole job."""
    monkeypatch.setattr(ServerRunner, "call", answering({"/props": {}}, []))
    runner = ServerRunner("http://127.0.0.1:8080")

    assert runner.n_ctx == 0
    assert runner.fits(10**9, 64)


def test_one_summary_is_one_chat_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    """The prompt goes out as system plus user, and the reply comes back raw."""
    seen: list[Any] = []
    replies = {
        "/props": PROPS,
        "/v1/chat/completions": {"choices": [{"message": {"content": "A parser."}}]},
    }
    monkeypatch.setattr(ServerRunner, "call", answering(replies, seen))
    runner = ServerRunner("http://127.0.0.1:8080")

    assert runner.summarize("be brief", "describe this", 64) == "A parser."

    path, body = seen[-1]
    assert path == "/v1/chat/completions"
    assert body is not None
    assert body["max_tokens"] == 64
    assert [message["role"] for message in body["messages"]] == ["system", "user"]
    assert body["messages"][0]["content"] == "be brief"


def test_an_answer_without_choices_is_a_failed_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty answer must fail its task, not return an empty summary."""
    replies = {"/props": PROPS, "/v1/chat/completions": {"error": "no slot"}}
    monkeypatch.setattr(ServerRunner, "call", answering(replies, []))
    runner = ServerRunner("http://127.0.0.1:8080")

    with pytest.raises(ApiError):
        runner.summarize("be brief", "describe this", 64)


def test_a_server_that_is_not_there_names_the_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing listening is a startup refusal, before any job is created."""
    refused = urllib.error.URLError("connection refused")
    monkeypatch.setattr(ServerRunner, "call", answering({"/props": refused}, []))

    with pytest.raises(SystemExit) as exit_info:
        ServerRunner("http://10.10.4.99:8080")

    message = str(exit_info.value)
    assert "http://10.10.4.99:8080" in message
    assert "llama-server" in message


def test_a_key_that_is_missing_is_named_as_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """401 has one cause here, and it is not the URL."""
    monkeypatch.setattr(
        ServerRunner, "call", answering({"/props": ApiError(401, "unauthorized")}, [])
    )

    with pytest.raises(SystemExit) as exit_info:
        ServerRunner("http://10.10.4.99:8080")

    assert "--llama-server-key" in str(exit_info.value)
