"""The model table, which the root Makefile reads through this package."""

from __future__ import annotations

import sys

import pytest
from ctxworker.catalogue import DEFAULT_MODEL, MODELS, default_dir, file_name, url


def test_every_entry_names_a_gguf() -> None:
    """The downloader checks the magic number, but the name must match too."""
    for repo, name in MODELS.values():
        assert name.endswith(".gguf")
        assert "/" in repo


def test_the_default_is_in_the_table() -> None:
    """Running with no --model must not be a lookup error."""
    assert DEFAULT_MODEL in MODELS


def test_an_unknown_model_names_the_choices() -> None:
    """The Makefile shows this message verbatim."""
    with pytest.raises(SystemExit, match="pick one of"):
        file_name("gpt-9")


def test_the_url_points_at_the_file_it_names() -> None:
    """A repo and a file name that disagree would download the wrong weights."""
    assert url("qwen-3b").endswith(file_name("qwen-3b"))


def test_windows_keeps_its_weights_somewhere_windows_has(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker is expected to run there, and ~/.local is not a Windows path."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\dev\AppData\Local")
    assert "context-mcp" in str(default_dir())
