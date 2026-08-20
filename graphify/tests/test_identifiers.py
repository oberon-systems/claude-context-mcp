"""What a project may be called, and what it may not."""

from __future__ import annotations

import pytest

from ctxgraph.identifiers import project_name


def test_derives_the_name_from_the_last_path_segment() -> None:
    """Naming a neighbour is meant to be a matter of naming its directory."""
    assert project_name("", "/home/zombig/src/kurum") == "kurum"
    assert project_name("", "/home/zombig/src/kurum/") == "kurum"


def test_an_explicit_name_wins_and_is_cleaned() -> None:
    """A name travels in a URL path segment, so it is narrowed to what fits."""
    assert project_name("My Repo", "/home/zombig/src/kurum") == "my-repo"


def test_reserves_the_builtin_prefix() -> None:
    """Without this the cleaning turns `_memory` into `memory`, silently."""
    with pytest.raises(RuntimeError, match="reserved"):
        project_name("_memory", "/home/zombig/src/whatever")
    with pytest.raises(RuntimeError, match="reserved"):
        project_name("", "/home/zombig/src/_common")


def test_refuses_a_name_that_would_climb_the_tree() -> None:
    """A name is also a directory under the extractor cache root."""
    for root in ("/", "/home/zombig/src/.", "/home/zombig/src/.."):
        with pytest.raises(RuntimeError):
            project_name("", root)
