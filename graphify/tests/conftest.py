"""Shared fixtures.

`ctxgraph.summarizer` imports llama_cpp at module level, which is the truth
about what the indexer needs. PyPI ships that package as a source
distribution, so requiring it here would put a llama.cpp build in front of the
test suite. Every test mocks the model anyway, so an absent package is stubbed
rather than installed.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

try:
    import llama_cpp  # noqa: F401
except ImportError:
    sys.modules["llama_cpp"] = MagicMock()

from ctxgraph.summarizer import Summarizer  # noqa: E402


@pytest.fixture
def summarizer(monkeypatch: pytest.MonkeyPatch) -> Summarizer:
    """Build a Summarizer whose model is a mock and whose weights are not read."""
    monkeypatch.setattr("ctxgraph.summarizer.ensure_model", lambda _path: None)
    monkeypatch.setattr("ctxgraph.summarizer.Llama", MagicMock())
    return Summarizer("/models/absent.gguf")
