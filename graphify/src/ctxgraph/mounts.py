"""List the directories the stack should mount, one line per source.

A project is a selection of host directories rather than a single tree. Each
one is mounted at `CODE_ROOT/<project>/<alias>`, and the unnamed source - the
project mounted whole at `CODE_ROOT/<project>` - is what a project with a
single directory has always been. Neither the project name nor the mount point
is a choice a caller gets to make: both come from the same rules the indexer
applies, so a mount and the row it belongs to cannot disagree.

`--register` is what makes that exact rather than nearly true: onboarding
stores the directory as a source here, so the list is the sources table and
nothing else. The row carries no `indexed_at`, because registering a tree is
not indexing it - the dashboard offers that afterwards. `--create` registers a
project that reads no directory at all, which is how a monorepo is onboarded
before its slices are added one at a time. `--drop` and `--promote` are the
other two ways the selection changes: one stops reading a directory, the other
names the root of a project mounted whole so a second directory can join it.

The unnamed source travels as `-` rather than as an empty column: a tab is an
IFS whitespace character, so a shell reading the listing collapses two of them
into one delimiter and an empty middle field cannot be expressed at all.

The output is a list rather than the compose file itself because only the host
can tell whether a directory still exists, and a bind mount whose source is
missing is created as an empty directory rather than refused - which would
index an empty tree and prune a whole graph. `scripts/mounts.sh` does that
check and writes the file.
"""

from __future__ import annotations

import argparse
import sys

from ctxgraph.config import KNOWN_PROJECT_TYPES
from ctxgraph.identifiers import project_name, source_alias
from ctxgraph.storage import (
    drop_source,
    get_db_connection,
    list_all_sources,
    promote_root,
    register_project,
)


def parse_args() -> argparse.Namespace:
    """Read the change to make, if any, before the listing is taken."""
    parser = argparse.ArgumentParser(
        prog="ctxgraph.mounts",
        description="List every directory to mount, as project, alias and path.",
    )
    parser.add_argument(
        "--add",
        default="",
        help="host path of a directory being mounted for the first time",
    )
    parser.add_argument(
        "--name",
        default="",
        help="project for --add; derived from its last path segment when unset",
    )
    parser.add_argument(
        "--alias",
        default="",
        help="alias for --add; empty mounts the project whole from that path",
    )
    # Whether --add is stored or only listed. Onboarding stores it; every
    # other caller lists, because a bare `make mounts` and `make summarize`
    # both reach here with a path of their own and neither is onboarding
    # anything. That the path exists is checked by the host afterwards, so
    # only a caller that has already checked should pass this.
    parser.add_argument(
        "--register",
        action="store_true",
        help="store --add as a source before listing, without indexing it",
    )
    # A project onboarded ahead of its directories. The row exists so the
    # dashboard lists it and the agent files have an address to name, and the
    # path it was onboarded from is recorded without being mounted or read.
    parser.add_argument(
        "--create",
        action="store_true",
        help="register --add as a project holding no directory yet",
    )
    parser.add_argument(
        "--type",
        default="",
        dest="project_type",
        help="type for --register; empty keeps the stored one",
    )
    parser.add_argument(
        "--drop",
        default="",
        help="alias of a source of --name to stop reading",
    )
    parser.add_argument(
        "--promote",
        default="",
        help="alias to give the unnamed source of --name",
    )
    return parser.parse_args()


def register(
    root_path: str,
    name: str,
    alias: str,
    project_type: str,
    with_source: bool = True,
) -> str:
    """Store one directory as a source and return the project it belongs to."""
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
            register_project(
                cursor,
                project,
                root_path,
                project_type or None,
                alias,
                with_source,
            )
        conn.commit()
    finally:
        conn.close()
    if not with_source:
        print(
            f"registered {project}, onboarded from {root_path} and reading no "
            "directory yet",
            file=sys.stderr,
        )
        return project
    where = f"{project}/{alias}" if alias else project
    print(f"registered {where} at {root_path}", file=sys.stderr)
    return project


def drop(project: str, alias: str) -> None:
    """Stop reading one directory of a project."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            drop_source(cursor, project, alias)
        conn.commit()
    finally:
        conn.close()
    print(
        f"dropped {alias!r} from {project}; the nodes it produced go on the "
        "next index run",
        file=sys.stderr,
    )


def promote(project: str, alias: str) -> None:
    """Name the unnamed source of a project."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            promote_root(cursor, project, alias)
        conn.commit()
    finally:
        conn.close()
    print(
        f"{project} now reads its root as {alias!r}; every node id gains that "
        "first segment, so index it again before trusting the graph",
        file=sys.stderr,
    )


def mounted_sources() -> dict[tuple[str, str], str]:
    """Return the host path of every (project, alias) holding a real tree."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            return {
                (project, alias): root_path
                for project, alias, root_path in list_all_sources(cursor)
            }
    finally:
        conn.close()


def main() -> None:
    """Print one `project<TAB>alias<TAB>host path` line per source.

    The alias of a project mounted whole is written `-`, which no alias can be:
    `source_alias` strips the character and refuses what is left.
    """
    args = parse_args()
    root_path = args.add.rstrip("/")
    alias = source_alias(args.alias, root_path) if args.alias.strip() else ""

    if args.drop or args.promote:
        project = args.name.strip()
        if not project:
            print("--drop and --promote need --name", file=sys.stderr)
            raise SystemExit(2)
        if args.drop:
            drop(project, source_alias(args.drop, ""))
        if args.promote:
            promote(project, source_alias(args.promote, ""))

    if root_path and args.register:
        register(
            root_path,
            args.name,
            alias,
            args.project_type.strip(),
            not args.create,
        )

    trees = mounted_sources()
    if root_path and not args.create:
        # The same rule `resolve_project` applies, so indexing this tree lands
        # in the project its mount was written for. A registered directory is
        # already in the listing above; this is what carries an unregistered
        # one, which is how a first mount happens before the row exists.
        trees[(project_name(args.name, root_path), alias)] = root_path
    if not trees:
        print("nothing is indexed yet, so nothing is mounted", file=sys.stderr)
    for entry in sorted(trees):
        print(f"{entry[0]}\t{entry[1] or '-'}\t{trees[entry]}")


if __name__ == "__main__":
    main()
