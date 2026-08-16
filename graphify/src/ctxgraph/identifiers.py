"""Shape the ids the graph stores.

Both the pure resolver and the SQL layer build node ids, so the rules live
here rather than in either of them.
"""

from __future__ import annotations

from ctxgraph.config import ENTITY_SEPARATOR, MAX_NODE_ID_LENGTH


def truncate(value: str, limit: int) -> str:
    """Clip a value to what the database column accepts."""
    return value if len(value) <= limit else value[:limit]


def entity_node_id(rel_path: str, name: str) -> str:
    """Build the file scoped id of an entity node."""
    return truncate(f"{rel_path}{ENTITY_SEPARATOR}{name}", MAX_NODE_ID_LENGTH)


def owner_path(node_id: str) -> str:
    """Return the file part of an entity node id."""
    return node_id.split(ENTITY_SEPARATOR, 1)[0]
