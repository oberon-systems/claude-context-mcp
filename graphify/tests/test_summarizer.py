"""What the summarizer promises the rest of the indexer."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ctxgraph.config import LLM_INPUT_CHARS, MAX_SUMMARY_LENGTH
from ctxgraph.summarizer import Summarizer, ensure_model, shape, useful


def reply(text: str) -> dict[str, list[dict[str, dict[str, str]]]]:
    """Build what llama_cpp returns from a chat completion."""
    return {"choices": [{"message": {"content": text}}]}


def test_ensure_model_names_the_target(tmp_path: Path) -> None:
    """A missing model says how to get one, since the run stops here."""
    with pytest.raises(RuntimeError, match="llm-model-install"):
        ensure_model(str(tmp_path / "absent.gguf"))


def test_ensure_model_rejects_a_download_that_was_not_one(tmp_path: Path) -> None:
    """A download without -f saves the error page under the model's name."""
    decoy = tmp_path / "model.gguf"
    decoy.write_bytes(b"<!DOCTYPE html><title>404</title>")
    with pytest.raises(RuntimeError, match="not a GGUF file"):
        ensure_model(str(decoy))


def test_ensure_model_accepts_the_magic(tmp_path: Path) -> None:
    """Nothing is read past the magic number: the file is a gigabyte."""
    weights = tmp_path / "model.gguf"
    weights.write_bytes(b"GGUF" + b"\x00" * 16)
    ensure_model(str(weights))


def test_shape_keeps_one_ascii_line() -> None:
    """A summary is one line of ASCII, whatever the model felt like writing."""
    written = "```\nStores the \u201cgraph\u201d \u2013 nodes.\nAnd more.\n```"
    assert shape(written) == 'Stores the "graph" - nodes.'


def test_shape_truncates_like_every_other_summary() -> None:
    """The column is shared with the parsers' own summaries."""
    assert len(shape("word " * 200)) <= MAX_SUMMARY_LENGTH


def test_summarize_clamps_the_prompt(summarizer: Summarizer) -> None:
    """A file may be a megabyte; the context window is two thousand tokens."""
    summarizer.llm.create_chat_completion.return_value = reply("A summary.")
    summarizer.summarize("big.tf", "x" * (LLM_INPUT_CHARS * 3))

    messages = summarizer.llm.create_chat_completion.call_args.kwargs["messages"]
    assert len(messages[1]["content"]) <= LLM_INPUT_CHARS + len("File: big.tf\n\n")


def test_useful_rejects_the_file_name_back() -> None:
    """The small model answers a data file with its own name often enough."""
    assert not useful("docker-compose.yaml", "docker-compose.yaml")
    assert not useful("CHANGELOG.md", "CHANGELOG.md")
    assert not useful("Short.", "graphify/src/ctxgraph/indexer.py")
    assert useful("Builds the graph from both producers.", "indexer.py")


def test_refine_keeps_the_head_when_the_answer_says_nothing(
    summarizer: Summarizer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rejected answer leaves the node alone but is remembered."""
    monkeypatch.setattr("ctxgraph.summarizer.get_cached_summary", lambda *_: None)
    cached = MagicMock()
    saved = MagicMock()
    monkeypatch.setattr("ctxgraph.summarizer.put_cached_summary", cached)
    monkeypatch.setattr("ctxgraph.summarizer.save_llm_summary", saved)
    summarizer.llm.create_chat_completion.return_value = reply("CHANGELOG.md")

    assert not summarizer.refine(MagicMock(), "proj", "CHANGELOG.md", "# 0.10.1")
    assert summarizer.rejected == 1
    saved.assert_not_called()
    # Cached all the same: the next pass must not ask for the same answer.
    cached.assert_called_once()


def test_refine_stores_what_the_model_said(
    summarizer: Summarizer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A generated summary is cached, or the next run pays for it again."""
    stored: dict[str, str] = {}
    monkeypatch.setattr("ctxgraph.summarizer.get_cached_summary", lambda *_: None)
    monkeypatch.setattr(
        "ctxgraph.summarizer.put_cached_summary",
        lambda _cursor, _project, key, summary: stored.setdefault(key, summary),
    )
    monkeypatch.setattr("ctxgraph.summarizer.save_llm_summary", lambda *_: True)
    summarizer.llm.create_chat_completion.return_value = reply("Builds the graph.")

    assert summarizer.refine(MagicMock(), "proj", "indexer.py", "code")
    assert list(stored.values()) == ["Builds the graph."]
    assert summarizer.generated == 1


def test_refine_prefers_the_cache(
    summarizer: Summarizer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cache hit must not reach the model at all - that is the whole point."""
    written: list[str] = []
    monkeypatch.setattr(
        "ctxgraph.summarizer.get_cached_summary", lambda *_: "Known already."
    )
    monkeypatch.setattr(
        "ctxgraph.summarizer.save_llm_summary",
        lambda _cursor, _project, _path, summary: bool(written.append(summary)) or True,
    )

    assert summarizer.refine(MagicMock(), "proj", "indexer.py", "code")
    assert written == ["Known already."]
    assert summarizer.cached == 1
    summarizer.llm.create_chat_completion.assert_not_called()


def test_refine_ignores_the_cache_when_refreshing(
    summarizer: Summarizer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FRESH=1 means the model runs again, cached answer or not."""
    summarizer.refresh = True
    monkeypatch.setattr(
        "ctxgraph.summarizer.get_cached_summary", lambda *_: "Known already."
    )
    monkeypatch.setattr("ctxgraph.summarizer.put_cached_summary", lambda *_: None)
    monkeypatch.setattr("ctxgraph.summarizer.save_llm_summary", lambda *_: True)
    summarizer.llm.create_chat_completion.return_value = reply("Written again.")

    assert summarizer.refine(MagicMock(), "proj", "indexer.py", "code")
    assert summarizer.generated == 1
    assert summarizer.cached == 0


def test_refine_skips_an_empty_file(summarizer: Summarizer) -> None:
    """Nothing to read, and one cache entry would answer for all of them."""
    assert not summarizer.refine(MagicMock(), "proj", "pkg/__init__.py", "")
    summarizer.llm.create_chat_completion.assert_not_called()


def test_refine_leaves_the_stored_summary_on_failure(
    summarizer: Summarizer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One file the model chokes on costs that file nothing it had."""
    monkeypatch.setattr("ctxgraph.summarizer.get_cached_summary", lambda *_: None)
    saved = MagicMock()
    monkeypatch.setattr("ctxgraph.summarizer.save_llm_summary", saved)
    summarizer.llm.create_chat_completion.side_effect = ValueError("too many tokens")

    assert not summarizer.refine(
        MagicMock(), "proj", "roles/web/tasks/main.yml", "# Installs nginx\n"
    )
    assert summarizer.failed == 1
    saved.assert_not_called()


def test_close_releases_the_weights(summarizer: Summarizer) -> None:
    """A gigabyte of resident memory outliving the pass is worth releasing."""
    llm = summarizer.llm
    summarizer.close()

    llm.close.assert_called_once()
    assert summarizer.llm is None
