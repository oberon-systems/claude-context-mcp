"""The text half of summarizing, which the worker API applies on its own."""

from __future__ import annotations

from ctxgraph.summary_text import (
    body_for_storage,
    content_key,
    shape,
    strip_preamble,
    useful,
)


def test_the_cache_key_is_sha256() -> None:
    """A key of the wrong width would be silently rejected by the column."""
    key = content_key("print('hi')")
    assert len(key) == 64
    assert key == "c2d0a5e0790d97a015387a995c0d0b5eb3e88138466586fc980787c9b1731eb8"


def test_the_key_follows_the_text_and_not_the_path() -> None:
    """Two identical files cost one generation, wherever they live."""
    assert content_key("same") == content_key("same")
    assert content_key("same") != content_key("other")


def test_shape_keeps_one_ascii_line() -> None:
    """A worker is a network peer, and prose arrives with anything in it."""
    # Escaped rather than literal: the repository is pure ASCII, and
    # this is the one place that needs a character which is not.
    shaped = shape("Here it is:\n\nRuns the thing \u2014 the \u201cright\u201d one.")
    assert "\n" not in shaped
    assert shaped.isascii()


def test_shape_answers_nothing_for_an_empty_reply() -> None:
    """An empty answer must not overwrite the head-of-file summary."""
    assert shape("   \n\n  ") == ""


def test_strip_preamble_drops_the_file_it_belongs_to() -> None:
    """The node already carries the path."""
    assert strip_preamble("The app.py file is a web server", "app.py") == (
        "A web server"
    )


def test_useful_rejects_an_answer_that_is_the_file_name() -> None:
    """That is worse than the summary it would replace."""
    assert not useful("CHANGELOG.md", "CHANGELOG.md")
    assert useful("Records what changed in each release.", "CHANGELOG.md")


def test_a_secret_is_never_stored_for_the_worker_to_read() -> None:
    """The column this fills is served over the network by the worker API."""
    assert body_for_storage(".env", "TOKEN=hunter2") == ""
    assert body_for_storage("deploy/.env.production", "TOKEN=hunter2") == ""
    assert body_for_storage("certs/server.pem", "-----BEGIN") == ""
    assert body_for_storage("infra/terraform.tfvars", 'password = "x"') == ""
    assert body_for_storage("src/app.py", "import os") == "import os"


def test_the_stored_body_is_bounded() -> None:
    """An index run holds one of these per code file at once."""
    stored = body_for_storage("src/big.py", "x" * 100_000)
    assert 0 < len(stored) <= 16_000
