"""List the trees the stack should mount, one `name<TAB>host path` per line.

Where each tree is mounted is not a choice a caller gets to make: it is
`CODE_ROOT/<project>`, and the project is named by the same rule the indexer
uses, so a mount and the row it belongs to cannot disagree about what a
project is called.

The output is a list rather than the compose file itself because only the host
can tell whether a root still exists, and a bind mount whose source is missing
is created as an empty directory rather than refused - which would index an
empty tree and prune a whole graph. `scripts/mounts.sh` does that check and
writes the file.
"""

from __future__ import annotations

import argparse
import sys

from ctxgraph.config import BUILTIN_PROJECT_TYPES
from ctxgraph.identifiers import project_name
from ctxgraph.storage import get_db_connection, list_projects


def parse_args() -> argparse.Namespace:
    """Read the tree to add on top of what the database already knows."""
    parser = argparse.ArgumentParser(
        prog="ctxgraph.mounts",
        description="List every tree to mount, as name and host path.",
    )
    parser.add_argument(
        "--add",
        default="",
        help="host path of a tree being indexed for the first time",
    )
    parser.add_argument(
        "--name",
        default="",
        help="name for --add; derived from its last path segment when unset",
    )
    return parser.parse_args()


def indexed_trees() -> list[tuple[str, str]]:
    """Return the (name, host path) of every project holding a real tree.

    The built-in projects hold records rather than files and carry a
    `memory://agent`-style root that no mount could ever answer.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            return [
                (name, root_path)
                for name, root_path, project_type, _ in list_projects(cursor)
                if project_type not in BUILTIN_PROJECT_TYPES
            ]
    finally:
        conn.close()


def main() -> None:
    """Print one `name<TAB>host path` line per tree, sorted and deduplicated."""
    args = parse_args()
    trees = dict(indexed_trees())
    if args.add:
        root_path = args.add.rstrip("/")
        # The same rule `resolve_project` applies, so indexing this tree lands
        # in the project its mount was written for.
        trees[project_name(args.name, root_path)] = root_path
    if not trees:
        print("nothing is indexed yet, so nothing is mounted", file=sys.stderr)
    for name in sorted(trees):
        print(f"{name}\t{trees[name]}")


if __name__ == "__main__":
    main()
