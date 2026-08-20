"""Shape the ids the graph stores.

Both the pure resolver and the SQL layer build node ids, so the rules live
here rather than in either of them.
"""

from __future__ import annotations

import posixpath
import re

from ctxgraph.config import (
    BUILTIN_NAME_PREFIX,
    ENTITY_SEPARATOR,
    MAX_NODE_ID_LENGTH,
    MAX_PROJECT_NAME_LENGTH,
)

# A project name travels in the /mcp/<name> endpoint as well as in the
# database, so it is kept to what survives a URL path segment untouched.
_PROJECT_NAME_ALLOWED = re.compile(r"[^a-z0-9._-]+")


def truncate(value: str, limit: int) -> str:
    """Clip a value to what the database column accepts."""
    return value if len(value) <= limit else value[:limit]


def entity_node_id(rel_path: str, name: str) -> str:
    """Build the file scoped id of an entity node."""
    return truncate(f"{rel_path}{ENTITY_SEPARATOR}{name}", MAX_NODE_ID_LENGTH)


def owner_path(node_id: str) -> str:
    """Return the file part of an entity node id."""
    return node_id.split(ENTITY_SEPARATOR, 1)[0]


def project_name(explicit: str, root_path: str) -> str:
    """Settle on the name a project is addressed by.

    An explicit name wins; otherwise the last segment of the root path is
    used, which is what makes indexing a neighbour a matter of naming its
    directory and nothing else.
    """
    candidate = explicit.strip() or posixpath.basename(root_path.rstrip("/"))
    # Checked before the cleaning below, which would turn `_memory` into
    # `memory` and index a tree into the built-in project without saying so.
    if candidate.startswith(BUILTIN_NAME_PREFIX):
        raise RuntimeError(
            f"project name {candidate!r} is reserved: names starting with "
            f"{BUILTIN_NAME_PREFIX!r} belong to the built-in projects, which "
            "hold records written by an agent rather than an indexed tree; "
            "pass PROJECT_NAME to index this one under another name"
        )
    cleaned = _PROJECT_NAME_ALLOWED.sub("-", candidate.lower()).strip("-")
    # "." and ".." survive the allowed set and would name the parent of every
    # per-project directory the name is used for.
    if not cleaned or cleaned in {".", ".."}:
        raise RuntimeError(
            f"cannot derive a project name from {root_path!r}; "
            "pass PROJECT_NAME explicitly"
        )
    return truncate(cleaned, MAX_PROJECT_NAME_LENGTH)
