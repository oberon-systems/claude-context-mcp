"""Backfill pass: `python -m ctxgraph.summarize`.

Indexing is fast and writes a summary from the head of each file. The model
is slow - seconds per file - so it comes afterwards, over the file nodes no
model has described yet, and marks each one as its own. That makes the pass
resumable: stop it, run it again, and it picks up where it left off rather
than starting over.

Named no project, it works through every one in the database. Only one tree is
mounted at a time, so that pass reads the text the index stored on each node -
the same column the worker API serves to a remote worker - rather than the
files themselves.

It is a reader of the tree and a writer of two columns, so it can run while
an agent is querying the graph.
"""

from __future__ import annotations

import argparse
import logging
import os

from psycopg2.extensions import connection as Connection

from ctxgraph.config import (
    BUILTIN_PROJECT_TYPES,
    FORCE_REEXTRACT,
    LLM_MODEL_PATH,
    PROJECT_ROOT,
    SUMMARY_LIMIT,
)
from ctxgraph.identifiers import project_mount
from ctxgraph.storage import (
    get_db_connection,
    list_files_without_llm_summary,
    list_projects,
)
from ctxgraph.summarizer import Summarizer

LOG = logging.getLogger(__name__)

# How often the pass reports where it is. A run over a large tree is measured
# in hours, and a silent hour is indistinguishable from a hung one.
PROGRESS_EVERY = 10


def parse_args() -> argparse.Namespace:
    """Read which project to describe. Nothing named means every one."""
    parser = argparse.ArgumentParser(
        prog="ctxgraph.summarize",
        description="Describe the file nodes the model has not described yet.",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="every indexed project, which is also what an unset PROJECT_ROOT means",
    )
    parser.add_argument(
        "--project",
        default="",
        help="one project by name, read from the graph rather than from the mount",
    )
    return parser.parse_args()


def describe_project(
    conn: Connection, summarizer: Summarizer, project: str, mount: str
) -> tuple[int, int]:
    """Describe one project from its files. Returns written and unreadable.

    The tree-sitter stack is imported here rather than at the top of the
    module: this pass reads files and the graph and parses nothing, so it must
    not need the parsers installed to run.
    """
    from ctxgraph.discovery import read_source
    from ctxgraph.indexer import SUMMARY_HEAD_LINES

    with conn.cursor() as cursor:
        pending = list_files_without_llm_summary(cursor, project, FORCE_REEXTRACT)
    if SUMMARY_LIMIT:
        pending = pending[:SUMMARY_LIMIT]
    LOG.info("Summarizing %d files of project %s", len(pending), project)

    written = 0
    missing = 0
    for position, rel_path in enumerate(pending, start=1):
        content, _ = read_source(os.path.join(mount, rel_path), rel_path)
        if content is None:
            missing += 1
            continue
        head = "\n".join(content.splitlines()[:SUMMARY_HEAD_LINES])

        # A commit per file, because the pass is meant to be interrupted:
        # what it has described is in the graph already.
        with conn.cursor() as cursor:
            try:
                written += summarizer.refine(cursor, project, rel_path, head)
                conn.commit()
            except Exception:
                conn.rollback()
                LOG.exception("Failed to store the summary of %s", rel_path)

        if position % PROGRESS_EVERY == 0:
            LOG.info("  %d/%d (%s)", position, len(pending), summarizer.report())

    if missing:
        LOG.info(
            "  %d file(s) of %s could not be read; the graph is ahead of the tree",
            missing,
            project,
        )
    return written, missing


def summarize_projects(projects: list[str] | None = None) -> None:
    """Describe the named projects, or every indexed one, from their files.

    The model is loaded once for the whole run: the weights cost a gigabyte
    and seconds to read, and a pass over ten projects must not pay that ten
    times. Every tree has a mount of its own, so this reads the files of all
    of them rather than the one that happened to be mounted.
    """
    conn = get_db_connection()
    try:
        if projects is None:
            with conn.cursor() as cursor:
                # The built-in projects hold records rather than files, so
                # there is no tree to mount and nothing here to describe.
                projects = [
                    name
                    for name, _, project_type, _ in list_projects(cursor)
                    if project_type not in BUILTIN_PROJECT_TYPES
                ]
        if not projects:
            LOG.info("Nothing is indexed yet, so there is nothing to describe.")
            return
        LOG.info("Describing %d project(s): %s", len(projects), ", ".join(projects))
        if SUMMARY_LIMIT:
            LOG.info(
                "Stopping after %d files of each project (SUMMARY_LIMIT)",
                SUMMARY_LIMIT,
            )

        summarizer = Summarizer(LLM_MODEL_PATH, FORCE_REEXTRACT)
        try:
            written = 0
            missing = 0
            unmounted: list[str] = []
            for project in projects:
                mount = project_mount(project)
                if not os.path.isdir(mount):
                    unmounted.append(project)
                    continue
                one_written, one_missing = describe_project(
                    conn, summarizer, project, mount
                )
                written += one_written
                missing += one_missing
            LOG.info(
                "Done with %d project(s): %d summaries written, %d files "
                "unreadable (%s)",
                len(projects) - len(unmounted),
                written,
                missing,
                summarizer.report(),
            )
            if unmounted:
                LOG.warning(
                    "Skipped %d project(s) with no mount: %s. Run `make mounts`.",
                    len(unmounted),
                    ", ".join(unmounted),
                )
        finally:
            summarizer.close()
    finally:
        conn.close()


def main() -> None:
    """Configure logging and run whichever pass was asked for."""
    logging.basicConfig(
        level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args = parse_args()
    if args.project:
        summarize_projects([args.project])
    elif args.auto or not PROJECT_ROOT.strip():
        summarize_projects()
    else:
        from ctxgraph.indexer import resolve_project

        project, _, _ = resolve_project()
        summarize_projects([project])


if __name__ == "__main__":
    main()
