"""List the trees the stack should mount, one `name<TAB>host path` per line.

Where each tree is mounted is not a choice a caller gets to make: it is
`CODE_ROOT/<project>`, and the project is named by the same rule the indexer
uses, so a mount and the row it belongs to cannot disagree about what a
project is called.

`--register` is what makes that exact rather than nearly true: onboarding
stores the tree as a project here, so the list is the projects table and
nothing else. The row carries no `indexed_at`, because registering a tree is
not indexing it - the dashboard offers that afterwards.

The output is a list rather than the compose file itself because only the host
can tell whether a root still exists, and a bind mount whose source is missing
is created as an empty directory rather than refused - which would index an
empty tree and prune a whole graph. `scripts/mounts.sh` does that check and
writes the file.
"""

from __future__ import annotations

import argparse
import sys

from ctxgraph.config import BUILTIN_PROJECT_TYPES, KNOWN_PROJECT_TYPES
from ctxgraph.identifiers import project_name
from ctxgraph.storage import get_db_connection, list_projects, register_project


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
    # Whether --add is stored or only listed. Onboarding stores it; every
    # other caller lists, because a bare `make mounts` and `make summarize`
    # both reach here with a path of their own and neither is onboarding
    # anything. That the path exists is checked by the host afterwards, so
    # only a caller that has already checked should pass this.
    parser.add_argument(
        "--register",
        action="store_true",
        help="store --add as a project before listing, without indexing it",
    )
    parser.add_argument(
        "--type",
        default="",
        dest="project_type",
        help="type for --register; empty keeps the stored one",
    )
    return parser.parse_args()


def register(root_path: str, name: str, project_type: str) -> str:
    """Store one tree as a project and return the name it was stored under."""
    project = project_name(name, root_path)
    if project_type and project_type not in KNOWN_PROJECT_TYPES:
        print(
            f"unknown project type {project_type!r}, expected one of "
            f"{', '.join(sorted(KNOWN_PROJECT_TYPES))}",
            file=sys.stderr,
        )
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            register_project(cursor, project, root_path, project_type or None)
        conn.commit()
    finally:
        conn.close()
    print(f"registered {project} at {root_path}", file=sys.stderr)
    return project


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
    root_path = args.add.rstrip("/")
    if root_path and args.register:
        register(root_path, args.name, args.project_type.strip())
    trees = dict(indexed_trees())
    if root_path:
        # The same rule `resolve_project` applies, so indexing this tree lands
        # in the project its mount was written for. A registered tree is
        # already in the listing above; this is what carries an unregistered
        # one, which is how a first mount happens before the row exists.
        trees[project_name(args.name, root_path)] = root_path
    if not trees:
        print("nothing is indexed yet, so nothing is mounted", file=sys.stderr)
    for name in sorted(trees):
        print(f"{name}\t{trees[name]}")


if __name__ == "__main__":
    main()
