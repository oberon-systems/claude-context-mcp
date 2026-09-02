"""What a project and its directories may be called, and what they may not."""

from __future__ import annotations

import pytest

from ctxgraph.identifiers import (
    project_name,
    source_alias,
    source_mount,
    source_node_id,
)


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


def test_an_alias_is_derived_and_cleaned_like_a_name() -> None:
    """It is a directory segment and a node-id prefix, so it obeys one rule."""
    assert source_alias("", "/mono/deploy/configs") == "configs"
    assert source_alias("Deploy Configs", "/mono/deploy") == "deploy-configs"
    assert source_alias("tools/agents", "/mono") == "tools-agents"


def test_an_alias_may_start_with_the_builtin_prefix() -> None:
    """Unlike a project, it addresses nothing on its own."""
    assert source_alias("_internal", "/mono/internal") == "_internal"


def test_refuses_an_alias_that_would_climb_the_tree() -> None:
    """An alias is a directory inside the project mount, and stays there."""
    for candidate in (".", "..", "/", "---"):
        with pytest.raises(RuntimeError):
            source_alias(candidate, "/")


def test_the_unnamed_source_is_the_project_mount() -> None:
    """A project reading one directory is mounted whole, as it always was."""
    assert source_mount("kurum", "") == "/code/kurum"
    assert source_mount("mono", "configs") == "/code/mono/configs"


def test_a_node_id_carries_the_alias_it_came_from() -> None:
    """Two slices may each hold a README.md without colliding."""
    assert source_node_id("", "README.md") == "README.md"
    assert source_node_id("configs", "README.md") == "configs/README.md"
