"""What the second pass refuses to write, and what invalidates the first.

Neither needs a database: `link_file` decides which edges to attempt before
it sends anything, and the hash it decides on is a pure function.
"""

from __future__ import annotations

from typing import Any

import pytest

from ctxgraph import indexer
from ctxgraph.indexer import compute_hash, link_file

PATH = "docker-compose.yaml"
COMPOSE = """
services:
  web:
    image: nginx:1.27
"""
# What the old YAML parser left behind for the same file, before the compose
# parser took it over: a node named after the top level key.
STALE = [{"id": f"{PATH}::key.services", "name": "key.services", "type": "key"}]
CURRENT = [{"name": "service.web", "type": "service"}]


@pytest.fixture
def edges(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str]]:
    """Collect the edges `link_file` sends, instead of sending them."""
    written: list[tuple[str, str, str]] = []

    def record(
        cursor: Any,  # noqa: ANN401
        project: str,
        source_id: str,
        target_id: str,
        relation_type: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        written.append((source_id, target_id, relation_type))

    monkeypatch.setattr(indexer, "insert_edge", record)
    monkeypatch.setattr(indexer, "ensure_external_node", lambda *args: None)
    return written


def run(entities: list[dict[str, str]]) -> int:
    """Link the compose file against the entity set it is handed."""
    return link_file(None, "demo", PATH, COMPOSE, set(), {}, entities)


def test_an_edge_from_an_undeclared_entity_is_dropped(
    edges: list[tuple[str, str, str]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Stale nodes must not make the run attempt an edge nothing can hold.

    The database would refuse the foreign key, and the refusal aborts the
    transaction - which costs the file every edge, not the one.
    """
    with caplog.at_level("WARNING"):
        count = run(STALE)
    assert count == 1
    assert edges == [(PATH, f"{PATH}::key.services", "contains")]
    assert "service.web" in caplog.text


def test_the_same_edge_is_written_once_the_entity_is_there(
    edges: list[tuple[str, str, str]],
) -> None:
    """With the node the current parser declares, the edge is written."""
    run(CURRENT)
    assert (f"{PATH}::service.web", "image:nginx:1.27", "uses_image") in edges


def test_the_hash_changes_when_the_parsers_do(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A parser change invalidates a file whose content never moved."""
    monkeypatch.setattr(indexer, "parsers_revision", lambda: "before")
    before = compute_hash(COMPOSE)
    monkeypatch.setattr(indexer, "parsers_revision", lambda: "after")
    assert compute_hash(COMPOSE) != before
